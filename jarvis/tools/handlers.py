"""Pipecat function-call handlers: the tools the LLM can invoke.

``build_tools(session)`` returns ``FunctionSchema`` objects whose handlers
close over a ``JarvisSession``. Put them on the ``LLMContext`` and Pipecat
registers them with the LLM service automatically.

Design rules baked in here (not left to the prompt):

- Nothing is ever typed into a tmux pane in one step. ``stage_prompt``
  stores a draft; ``send_staged_prompt`` sends it only if the *user's* most
  recent utterance in the context contains a confirmation word.
- ``answer_permission(allow=True)`` has the same confirmation guard.
- Verbatim reading goes through the ``Narrator`` (sentence cursor, resumable)
  and tells the LLM not to respond, so what you hear is the real text.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import FunctionCallResultProperties

from jarvis.events import claude_summary
from jarvis.fillers import pick_filler
from jarvis.session import JarvisSession, StagedPrompt
from jarvis.speakable import to_speakable
from jarvis.tools.search import decisive, find_tabs
from jarvis.tools import tmux as tmux_tools

CONFIRM_WORDS = ("send", "yes", "go ahead", "confirm", "do it", "allow", "approve")
_MAX_SUMMARY_CHARS = 3000


def _latest_user_text(context: Any) -> str:
    """Text of the most recent user message in the LLM context, or ''."""
    messages = getattr(context, "messages", None) or []
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        return str(content)
    return ""


def user_confirmed(context: Any) -> bool:
    text = re.sub(r"[^\w\s]", " ", _latest_user_text(context).lower())
    return any(re.search(rf"\b{re.escape(word)}\b", text) for word in CONFIRM_WORDS)


_NUMBER_WORDS = {
    0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
}


def number_word(n: int) -> str:
    return _NUMBER_WORDS.get(n, str(n))


def describe_tabs(windows, active_tab: int | None) -> str:
    """Spoken listing: 'Six tabs. Zero, bash. One, agora, active. ...'"""
    if not windows:
        return "No tabs open."
    parts = []
    for w in windows:
        label = "claude" if w.pane_command in ("claude", "node") else w.pane_command
        item = f"{number_word(w.index)}, {w.name}, {label}"
        if w.index == active_tab:
            item += ", active"
        parts.append(item)
    count = number_word(len(windows)).capitalize()
    return f"{count} tabs. " + ". ".join(p[0].upper() + p[1:] for p in parts) + "."


def _resolve_target_tab(session: JarvisSession, arguments: dict) -> int | None:
    tab = arguments.get("tab")
    if tab is None or str(tab).strip().lower() in ("", "active", "current", "this", "none"):
        return session.active_tab
    if isinstance(tab, int):
        return tab
    return tmux_tools.resolve_tab(str(tab), session=session.tmux_session)


def _window_for(session: JarvisSession, tab: int):
    for window in tmux_tools.tmux_list(session=session.tmux_session):
        if window.index == tab:
            return window
    return None


def build_tools(session: JarvisSession) -> list[FunctionSchema]:
    """Return the tool schemas, with handlers bound to ``session``."""

    async def _speak_instead_of_llm(params, spoken: str, result: dict) -> None:
        """Speak a deterministic sentence and skip the LLM's follow-up turn.

        Saves a whole LLM round trip (~1 s) on commands whose answer needs
        no reasoning. Falls back to a normal tool result when there is no
        narrator (text REPL, tests).
        """
        if session.narrator is None:
            await params.result_callback(result)
            return
        await session.narrator.begin(spoken)
        await params.result_callback(
            {**result, "spoken": spoken},
            properties=FunctionCallResultProperties(run_llm=False),
        )

    async def _filler(tool_name: str) -> None:
        """Say something short while a tool + LLM follow-up (~2-3 s) runs."""
        if session.narrator is not None:
            await session.narrator.say(pick_filler(tool_name))

    async def list_tabs(params) -> None:
        windows = tmux_tools.tmux_list(session=session.tmux_session)
        result = {
            "active_tab": session.active_tab,
            "tabs": [
                {"index": w.index, "name": w.name, "command": w.pane_command}
                for w in windows
            ],
        }
        await _speak_instead_of_llm(params, describe_tabs(windows, session.active_tab), result)

    async def switch_tab(params) -> None:
        query = str(params.arguments.get("query", ""))
        tab = tmux_tools.resolve_tab(query, session=session.tmux_session)
        if tab is None:
            # Not a number or window name: maybe a topic ("the login bug").
            matches = find_tabs(query, session.event_store, session=session.tmux_session)[:3]
            winner = decisive(matches)
            if winner is None:
                await params.result_callback(
                    {
                        "error": f"no tab named {query!r}",
                        "candidates": [
                            {"tab": m.tab, "name": m.name, "snippet": m.snippet} for m in matches
                        ],
                        "instruction": "Read the candidates back by number and ask, or ask for the tab number.",
                    }
                )
                return
            tab = winner.tab
        session.active_tab = tab
        window = _window_for(session, tab)
        name = window.name if window else None
        await _speak_instead_of_llm(
            params,
            f"Tab {number_word(tab)}, {name}." if name else f"Tab {number_word(tab)}.",
            {"active_tab": tab, "name": name},
        )

    async def find_tab(params) -> None:
        topic = str(params.arguments.get("topic", "")).strip()
        if not topic:
            await params.result_callback({"error": "empty topic"})
            return
        await _filler("find_tab")
        matches = find_tabs(topic, session.event_store, session=session.tmux_session)[:3]
        winner = decisive(matches)
        result = {
            "topic": topic,
            "candidates": [
                {"tab": m.tab, "name": m.name, "score": m.score,
                 "words_matched": f"{m.terms_hit}/{m.terms_total}", "snippet": m.snippet}
                for m in matches
            ],
        }
        if winner is not None:
            session.active_tab = winner.tab
            await _speak_instead_of_llm(
                params,
                f"That's tab {number_word(winner.tab)}, {winner.name}. Switched.",
                {**result, "switched_to": winner.tab},
            )
            return
        if not matches:
            result["instruction"] = "No tab mentions that. Tell the user and ask for the tab number."
        else:
            result["instruction"] = (
                "Ambiguous. Read the top candidates back as 'tab <number>, <name>' "
                "with a few words from each snippet, and ask which one. Do not switch."
            )
        await params.result_callback(result)

    async def read_tab(params) -> None:
        tab = _resolve_target_tab(session, params.arguments)
        if tab is None:
            await params.result_callback({"error": "no active tab; ask which tab"})
            return
        text = claude_summary(tab, session.event_store, session=session.tmux_session)
        if session.narrator is None:
            await params.result_callback({"tab": tab, "text": text[:_MAX_SUMMARY_CHARS]})
            return
        window = _window_for(session, tab)
        lead = f"Tab {number_word(tab)}, {window.name}." if window else f"Tab {number_word(tab)}."
        await session.narrator.begin(f"{lead} {to_speakable(text)}")
        await params.result_callback(
            {"status": "reading aloud", "tab": tab},
            properties=FunctionCallResultProperties(run_llm=False),
        )

    async def summarize_tab(params) -> None:
        tab = _resolve_target_tab(session, params.arguments)
        if tab is None:
            await params.result_callback({"error": "no active tab; ask which tab"})
            return
        await _filler("claude_summary")
        text = claude_summary(tab, session.event_store, session=session.tmux_session)
        await params.result_callback(
            {
                "tab": tab,
                "latest_output": to_speakable(text)[-_MAX_SUMMARY_CHARS:],
                "instruction": (
                    "Summarise this for a driver in at most three short sentences. "
                    "Never quote commands, paths, or code; say what they do."
                ),
            }
        )

    async def stage_prompt(params) -> None:
        tab = _resolve_target_tab(session, params.arguments)
        text = str(params.arguments.get("text", "")).strip()
        if tab is None:
            await params.result_callback({"error": "no active tab; ask which tab"})
            return
        if not text:
            await params.result_callback({"error": "empty prompt"})
            return
        session.staged = StagedPrompt(tab=tab, text=text)
        await _filler("stage_prompt")
        await params.result_callback(
            {
                "staged": {"tab": tab, "text": text},
                "instruction": (
                    "Read the staged text back to the user word for word, then ask "
                    "them to say 'send' to confirm. Do not call send_staged_prompt "
                    "until they do."
                ),
            }
        )

    async def send_staged_prompt(params) -> None:
        staged = session.staged
        if staged is None:
            await params.result_callback({"error": "nothing staged"})
            return
        if not user_confirmed(params.context):
            await params.result_callback(
                {"error": "user has not said a confirmation word yet; ask them to say 'send'"}
            )
            return
        try:
            tmux_tools.tmux_send(
                staged.tab, staged.text, confirmed=True, session=session.tmux_session
            )
        except PermissionError as exc:
            await params.result_callback({"error": str(exc)})
            return
        session.staged = None
        await params.result_callback({"sent": True, "tab": staged.tab})

    async def discard_staged_prompt(params) -> None:
        had = session.staged is not None
        session.staged = None
        await params.result_callback({"discarded": had})

    async def resume_reading(params) -> None:
        if session.narrator is None or not await session.narrator.resume():
            await params.result_callback({"error": "nothing to resume"})
            return
        await params.result_callback(
            {"status": "resumed"}, properties=FunctionCallResultProperties(run_llm=False)
        )

    async def skip_sentence(params) -> None:
        if session.narrator is None or not await session.narrator.skip():
            await params.result_callback({"error": "nothing to skip"})
            return
        await params.result_callback(
            {"status": "skipped"}, properties=FunctionCallResultProperties(run_llm=False)
        )

    async def stop_reading(params) -> None:
        if session.narrator is not None:
            await session.narrator.stop()
        await params.result_callback({"status": "stopped"})

    async def pending_permissions(params) -> None:
        windows = tmux_tools.tmux_list(session=session.tmux_session)
        pending = []
        for session_id in session.event_store.session_ids():
            event = session.event_store.get(session_id)
            if event is None or not event.pending_permission:
                continue
            tab = session.event_store.tab_for_session(session_id, windows)
            pending.append({"tab": tab, "prompt": event.pending_permission})
        await params.result_callback({"pending": pending})

    async def answer_permission(params) -> None:
        tab = _resolve_target_tab(session, params.arguments)
        allow = bool(params.arguments.get("allow", False))
        if tab is None:
            await params.result_callback({"error": "no active tab; ask which tab"})
            return
        if allow and not user_confirmed(params.context):
            await params.result_callback(
                {"error": "user has not explicitly approved; ask them to say 'allow'"}
            )
            return
        tmux_tools.tmux_send_key(tab, "Enter" if allow else "Escape", session=session.tmux_session)
        await params.result_callback({"tab": tab, "answered": "allow" if allow else "deny"})

    tab_property = {
        "tab": {
            "type": "string",
            "description": "Tab number or name, only if the user named one. Otherwise omit this argument entirely.",
        }
    }

    return [
        _schema("list_tabs", "List the open tmux tabs (windows) and which one is active.", {}, [], list_tabs),
        _schema(
            "switch_tab",
            "Make a tab the active one. Accepts a tab number or a name/directory fragment.",
            {"query": {"type": "string", "description": "Tab number or name fragment."}},
            ["query"],
            switch_tab,
        ),
        _schema(
            "find_tab",
            "Find which tab the user means from a topic they describe ('the one about the phone setup', 'where we fixed the login bug'). Searches every tab's recent text. Switches automatically when one tab clearly wins.",
            {"topic": {"type": "string", "description": "The topic words the user said, minus filler."}},
            ["topic"],
            find_tab,
        ),
        _schema(
            "read_tab",
            "Read the latest Claude output of a tab aloud, word for word. Use when the user asks to read, read out, or hear what it said.",
            tab_property,
            [],
            read_tab,
        ),
        _schema(
            "summarize_tab",
            "Fetch the latest Claude output of a tab so you can summarise it briefly. Use for 'update me', 'what's going on', 'status'.",
            tab_property,
            [],
            summarize_tab,
        ),
        _schema(
            "stage_prompt",
            "Stage a prompt the user dictated for Claude. Does NOT send it. Call this when the user says 'tell it to ...', 'reply that ...', or dictates instructions.",
            {"text": {"type": "string", "description": "The prompt text, cleaned up but preserving every technical term."}, **tab_property},
            ["text"],
            stage_prompt,
        ),
        _schema(
            "send_staged_prompt",
            "Send the staged prompt into its tab. Only call after the user explicitly said 'send' or 'yes'.",
            {},
            [],
            send_staged_prompt,
        ),
        _schema("discard_staged_prompt", "Throw away the staged prompt.", {}, [], discard_staged_prompt),
        _schema("resume_reading", "Continue reading from where the last reading was interrupted.", {}, [], resume_reading),
        _schema("skip_sentence", "Skip the current sentence of the reading and continue.", {}, [], skip_sentence),
        _schema("stop_reading", "Stop the current reading.", {}, [], stop_reading),
        _schema("pending_permissions", "List tabs where Claude is waiting for a permission answer.", {}, [], pending_permissions),
        _schema(
            "answer_permission",
            "Answer a Claude permission prompt in a tab. allow=true presses Enter (accept), allow=false presses Escape (reject). Only allow after the user explicitly says so.",
            {"allow": {"type": "boolean", "description": "True to allow, false to reject."}, **tab_property},
            ["allow"],
            answer_permission,
        ),
    ]


def _logged(name: str, handler: Callable) -> Callable:
    """Log every call to a tool before running it: ``TOOLCALL <name>
    args=<...>``. Generic and provider-agnostic -- this is what makes a
    Groq-vs-Ollama desktop A/B run comparable turn by turn (see
    scripts/compare_llm_runs.py), not a change to any handler's behavior."""

    async def wrapper(params) -> None:
        logger.info(f"TOOLCALL {name} args={dict(params.arguments)}")
        await handler(params)

    return wrapper


def _schema(
    name: str, description: str, properties: dict, required: list[str], handler: Callable
) -> FunctionSchema:
    return FunctionSchema(
        name=name,
        description=description,
        properties=properties,
        required=required,
        handler=_logged(name, handler),
    )

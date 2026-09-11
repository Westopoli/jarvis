# spec: specs/cascade-b.md::Acceptance criteria::AC-11..12
"""Text REPL driver for Jarvis (leaf-04, spec_lines 25-27).

``run_turn`` classifies a transcript's intent via ``jarvis.intent`` and, for
"command" intents, dispatches through ``jarvis.tools.registry.dispatch`` --
never touching ``jarvis.tools.tmux`` directly. It always returns a
non-empty, plain-text reply and never raises for ordinary input.
"""
from __future__ import annotations

from jarvis.intent import classify_intent
from jarvis.tools.registry import dispatch
from jarvis.types import SessionState

_ACKNOWLEDGEMENTS = {
    "draft": "Got it, I'll pass that along.",
    "narrate": "Sure, let me read that.",
    "smalltalk": "I'm Jarvis, here to help you drive your tmux sessions.",
}
_DEFAULT_ACKNOWLEDGEMENT = "Okay."


def run_turn(text: str, session_state: SessionState) -> str:
    """Classify ``text``'s intent and produce a speakable reply.

    Command intents dispatch through the tool registry; every other intent
    (and any failure along the way) resolves to a short acknowledgement.
    Never raises for ordinary input (spec_lines 26).
    """
    try:
        intent = classify_intent(text)
    except Exception:
        intent = "smalltalk"

    try:
        if intent == "command":
            return _handle_command(text, session_state)
        return _ACKNOWLEDGEMENTS.get(intent, _DEFAULT_ACKNOWLEDGEMENT)
    except Exception:
        return "Sorry, I couldn't do that."


def _select_tool(text: str) -> tuple[str, dict]:
    """Pick a tool name + arguments from what the transcript asks for."""
    lowered = text.lower()

    if any(word in lowered for word in ("summar", "update", "happening", "status")):
        return "claude_summary", {}

    if "tab" in lowered or "window" in lowered:
        if "open" in lowered or "list" in lowered or "which" in lowered:
            return "tmux_list", {}
        return "switch_tab", {"query": text}

    return "tmux_list", {}


def _handle_command(text: str, session_state: SessionState) -> str:
    name, arguments = _select_tool(text)
    try:
        result = dispatch(name, arguments, session_state)
    except Exception:
        return "Sorry, I couldn't complete that command."
    return _describe_result(name, result)


def _describe_result(name: str, result: object) -> str:
    if name == "tmux_list":
        windows = result or []
        if not windows:
            return "You have no open tabs."
        listing = ", ".join(f"{w.index} {w.name}" for w in windows)
        return f"You have {len(windows)} tabs open: {listing}."
    if name == "switch_tab":
        if result is None:
            return "I couldn't find a matching tab."
        return f"Switched to tab {result}."
    if name == "claude_summary":
        return f"Here's the latest: {result}"
    return "Done."

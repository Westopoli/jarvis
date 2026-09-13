"""Tool handlers: staging/confirmation guards, tab resolution, narration hand-off."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from jarvis.session import JarvisSession
from jarvis.tools import handlers
from jarvis.types import TmuxWindow

WINDOWS = [
    TmuxWindow(index=1, name="api", pane_path="/home/u/api", pane_command="claude"),
    TmuxWindow(index=2, name="web", pane_path="/home/u/web", pane_command="bash"),
]


class _Context:
    def __init__(self, user_text: str | None) -> None:
        self.messages: list[dict] = [{"role": "system", "content": "x"}]
        if user_text is not None:
            self.messages.append({"role": "user", "content": user_text})
        self.messages.append({"role": "assistant", "content": "ok"})


@dataclass
class _Params:
    arguments: dict
    context: Any
    results: list = field(default_factory=list)
    properties: list = field(default_factory=list)

    async def result_callback(self, result, *, properties=None):
        self.results.append(result)
        self.properties.append(properties)


class _FakeNarrator:
    def __init__(self) -> None:
        self.begun: list[str] = []
        self.resumed = 0

    async def begin(self, text: str) -> None:
        self.begun.append(text)

    async def resume(self) -> bool:
        self.resumed += 1
        return True

    async def skip(self) -> bool:
        return True

    async def stop(self) -> None:
        pass


@pytest.fixture
def env(monkeypatch):
    sent: list[tuple] = []
    keys: list[tuple] = []
    monkeypatch.setattr(handlers.tmux_tools, "tmux_list", lambda session=None: WINDOWS)
    monkeypatch.setattr(
        handlers.tmux_tools,
        "resolve_tab",
        lambda q, session=None: {"1": 1, "2": 2, "api": 1, "web": 2}.get(q.strip().lower()),
    )
    monkeypatch.setattr(
        handlers.tmux_tools,
        "tmux_send",
        lambda tab, text, confirmed=False, force=False, session=None: sent.append((tab, text, confirmed)),
    )
    monkeypatch.setattr(
        handlers.tmux_tools, "tmux_send_key", lambda tab, key, session=None: keys.append((tab, key))
    )
    monkeypatch.setattr(handlers, "claude_summary", lambda tab, store, session=None: f"output of {tab}")
    session = JarvisSession(active_tab=1)
    tools = {t.name: t.handler for t in handlers.build_tools(session)}
    return session, tools, sent, keys


def _tool_names(session):
    return [t.name for t in handlers.build_tools(session)]


def test_every_tool_has_a_handler_and_unique_name():
    session = JarvisSession()
    names = _tool_names(session)
    assert len(names) == len(set(names))
    assert {"list_tabs", "switch_tab", "read_tab", "summarize_tab", "stage_prompt",
            "send_staged_prompt", "answer_permission"} <= set(names)


async def test_list_tabs_reports_active(env):
    session, tools, _, _ = env
    p = _Params({}, _Context("jarvis what tabs are open"))
    await tools["list_tabs"](p)
    assert p.results[0]["active_tab"] == 1
    assert [t["index"] for t in p.results[0]["tabs"]] == [1, 2]
    assert p.properties[0] is None  # no narrator: LLM answers


async def test_list_tabs_speaks_directly_when_narrator_present(env):
    session, tools, _, _ = env
    session.narrator = _FakeNarrator()
    p = _Params({}, _Context("jarvis what tabs are open"))
    await tools["list_tabs"](p)
    assert session.narrator.begun == ["Two tabs. One, api, claude, active. Two, web, bash."]
    assert p.properties[0].run_llm is False


def test_describe_tabs_empty():
    assert handlers.describe_tabs([], None) == "No tabs open."


async def test_switch_tab_sets_active(env):
    session, tools, _, _ = env
    session.narrator = _FakeNarrator()
    p = _Params({"query": "web"}, _Context("switch to web"))
    await tools["switch_tab"](p)
    assert session.active_tab == 2
    assert session.narrator.begun == ["Tab two, web."]
    p2 = _Params({"query": "nope"}, _Context("switch to nope"))
    await tools["switch_tab"](p2)
    assert "error" in p2.results[0]
    assert session.active_tab == 2


async def test_stage_then_send_requires_confirmation_word(env):
    session, tools, sent, _ = env
    await tools["stage_prompt"](_Params({"text": "use pydantic"}, _Context("tell it to use pydantic")))
    assert session.staged is not None and session.staged.tab == 1

    p = _Params({}, _Context("hmm not sure"))
    await tools["send_staged_prompt"](p)
    assert "error" in p.results[0]
    assert sent == []

    p = _Params({}, _Context("Send."))
    await tools["send_staged_prompt"](p)
    assert p.results[0] == {"sent": True, "tab": 1}
    assert sent == [(1, "use pydantic", True)]
    assert session.staged is None


async def test_send_with_nothing_staged_errors(env):
    _, tools, sent, _ = env
    p = _Params({}, _Context("send"))
    await tools["send_staged_prompt"](p)
    assert "error" in p.results[0]
    assert sent == []


async def test_stage_prompt_targets_named_tab(env):
    session, tools, _, _ = env
    await tools["stage_prompt"](_Params({"text": "hi", "tab": "web"}, _Context("x")))
    assert session.staged.tab == 2


async def test_discard(env):
    session, tools, _, _ = env
    await tools["stage_prompt"](_Params({"text": "hi"}, _Context("x")))
    p = _Params({}, _Context("scratch that"))
    await tools["discard_staged_prompt"](p)
    assert p.results[0] == {"discarded": True}
    assert session.staged is None


async def test_read_tab_hands_off_to_narrator_and_silences_llm(env):
    session, tools, _, _ = env
    session.narrator = _FakeNarrator()
    p = _Params({}, _Context("read it"))
    await tools["read_tab"](p)
    assert session.narrator.begun == ["Tab 1, api. output of 1"]
    assert p.properties[0].run_llm is False


async def test_read_tab_without_narrator_returns_text(env):
    session, tools, _, _ = env
    p = _Params({"tab": "2"}, _Context("read tab two"))
    await tools["read_tab"](p)
    assert p.results[0]["text"] == "output of 2"


async def test_summarize_tab_returns_output_for_llm(env):
    _, tools, _, _ = env
    p = _Params({}, _Context("update me"))
    await tools["summarize_tab"](p)
    assert p.results[0]["latest_output"] == "output of 1"
    assert p.properties[0] is None  # LLM runs


async def test_no_active_tab_errors(env):
    session, tools, _, _ = env
    session.active_tab = None
    p = _Params({}, _Context("read it"))
    await tools["read_tab"](p)
    assert "error" in p.results[0]


async def test_answer_permission_allow_requires_confirmation(env):
    _, tools, _, keys = env
    p = _Params({"allow": True}, _Context("what does it want"))
    await tools["answer_permission"](p)
    assert "error" in p.results[0] and keys == []

    p = _Params({"allow": True}, _Context("yes allow it"))
    await tools["answer_permission"](p)
    assert keys == [(1, "Enter")]

    p = _Params({"allow": False}, _Context("no"))
    await tools["answer_permission"](p)
    assert keys[-1] == (1, "Escape")


async def test_pending_permissions_lists_tabs(env):
    session, tools, _, _ = env
    session.event_store.record({"session_id": "s1", "cwd": "/home/u/web",
                                "event": "notification", "message": "Permission needed: run git push"})
    p = _Params({}, _Context("anything waiting"))
    await tools["pending_permissions"](p)
    assert p.results[0]["pending"] == [{"tab": 2, "prompt": "Permission needed: run git push"}]


async def test_resume_reading(env):
    session, tools, _, _ = env
    session.narrator = _FakeNarrator()
    p = _Params({}, _Context("keep going"))
    await tools["resume_reading"](p)
    assert session.narrator.resumed == 1
    assert p.properties[0].run_llm is False


def test_user_confirmed_word_boundaries():
    assert handlers.user_confirmed(_Context("Yes."))
    assert handlers.user_confirmed(_Context("okay go ahead and send it"))
    assert not handlers.user_confirmed(_Context("yesterday's sendoff"))
    assert not handlers.user_confirmed(_Context(None))

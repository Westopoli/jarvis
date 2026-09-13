from __future__ import annotations

from jarvis import announcer as announcer_mod
from jarvis.announcer import Announcer, tab_phrase
from jarvis.session import JarvisSession
from jarvis.types import TmuxWindow

WINDOWS = [
    TmuxWindow(index=2, name="api", pane_path="/home/u/api", pane_command="claude"),
]


def _session(monkeypatch) -> JarvisSession:
    monkeypatch.setattr(announcer_mod.tmux_tools, "tmux_list", lambda session=None: WINDOWS)
    return JarvisSession()


def test_tab_phrase_words():
    assert tab_phrase(2, "api") == "tab two, api"
    assert tab_phrase(12) == "tab 12"
    assert tab_phrase(None) == "an unknown tab"


def test_nothing_to_announce(monkeypatch):
    assert Announcer(_session(monkeypatch)).poll() == []


def test_stop_event_announced_once(monkeypatch):
    session = _session(monkeypatch)
    session.event_store.record(
        {"session_id": "s1", "cwd": "/home/u/api", "event": "stop", "message": "done"}
    )
    a = Announcer(session)
    assert a.poll() == ["Tab two, api has finished. Want me to read it?"]
    assert a.poll() == []


def test_permission_event_announced_with_prompt(monkeypatch):
    session = _session(monkeypatch)
    session.event_store.record(
        {"session_id": "s1", "cwd": "/home/u/api", "event": "notification",
         "message": "Permission needed to run git push"}
    )
    out = Announcer(session).poll()
    assert out == ["Tab two, api is asking for permission: Permission needed to run git push. Shall I allow it?"]


def test_new_event_after_announce_is_announced_again(monkeypatch):
    session = _session(monkeypatch)
    a = Announcer(session)
    session.event_store.record({"session_id": "s1", "cwd": "/home/u/api", "event": "stop", "message": "one"})
    assert len(a.poll()) == 1
    event = session.event_store.get("s1")
    event.ts += 1.0  # a later Stop
    assert len(a.poll()) == 1

# spec: specs/cascade-a.md::Acceptance criteria::AC-27..32
"""Tests for the Claude Code hook ingest path (leaf-06, AC 27-32):
``hooks/jarvis_stop.sh``, ``hooks/jarvis_notify.sh``, ``jarvis/events.py`` and
``jarvis/server.py``.

Two deliberate choices:

* The hook scripts are exercised as real subprocesses against a throwaway
  ``http.server`` listener bound to an ephemeral loopback port, handed to them
  as ``JARVIS_PORT``.  Nothing is mocked inside the script, so the JSON body it
  actually puts on the wire is what gets asserted.
* ``POST /events`` is driven through the ASGI interface directly rather than
  ``fastapi.testclient.TestClient``, because TestClient needs ``httpx``, which
  this project does not depend on.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from jarvis import server as jarvis_server
from jarvis.events import EventStore, claude_summary
from jarvis.types import SessionEvent, TmuxWindow

HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"

WINDOWS = [
    TmuxWindow(index=0, name="editor", pane_path="/home/w/proj-a", pane_command="sleep"),
    TmuxWindow(index=1, name="api", pane_path="/home/w/proj-a/service", pane_command="claude"),
    TmuxWindow(index=2, name="web", pane_path="/home/w", pane_command="node"),
]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

@pytest.fixture
def hook_listener():
    """A loopback HTTP server standing in for the running Jarvis daemon."""
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            received.append({"path": self.path, "raw": raw})
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):  # silence stderr noise
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1], received
    finally:
        server.shutdown()
        server.server_close()


def _run_hook(script: str, payload: dict, port: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(HOOKS_DIR / script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=20,
        env={**os.environ, "JARVIS_PORT": str(port)},
    )


def _post_asgi(app, path: str, payload: dict) -> tuple[int, bytes]:
    """Drive one POST through the ASGI app without an HTTP client."""
    body = json.dumps(payload).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"127.0.0.1"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 54321),
        "server": ("127.0.0.1", 8765),
    }
    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m for m in messages if m["type"] == "http.response.start")["status"]
    out = b"".join(
        m.get("body", b"") for m in messages if m["type"] == "http.response.body"
    )
    return status, out


# --------------------------------------------------------------------------
# AC-27 / AC-28: the hook shell scripts
# --------------------------------------------------------------------------

def test_stop_hook_posts_the_last_assistant_message(hook_listener, tmp_path):
    port, received = hook_listener
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(
            json.dumps(entry)
            for entry in [
                {"type": "user", "message": {"content": [{"type": "text", "text": "run the tests"}]}},
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "first answer"}]}},
                {"type": "user", "message": {"content": [{"type": "text", "text": "and again"}]}},
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "the retry is fixed"}]}},
                {"type": "system", "message": {"content": [{"type": "text", "text": "hook noise"}]}},
            ]
        )
        + "\n"
    )
    proc = _run_hook(
        "jarvis_stop.sh",
        {
            "session_id": "abc123",
            "cwd": "/home/w/proj-a/service",
            "transcript_path": str(transcript),
            "hook_event_name": "Stop",
        },
        port,
    )

    assert (proc.returncode, len(received)) == (0, 1), proc.stderr
    assert received[0]["path"] == "/events"
    assert json.loads(received[0]["raw"]) == {
        "session_id": "abc123",
        "cwd": "/home/w/proj-a/service",
        "event": "stop",
        "message": "the retry is fixed",
    }


def test_notify_hook_posts_the_notification_message(hook_listener):
    port, received = hook_listener
    proc = _run_hook(
        "jarvis_notify.sh",
        {
            "session_id": "def456",
            "cwd": "/home/w/proj-a",
            "message": "Claude needs your permission to use Bash",
            "hook_event_name": "Notification",
        },
        port,
    )

    assert (proc.returncode, len(received)) == (0, 1), proc.stderr
    assert json.loads(received[0]["raw"]) == {
        "session_id": "def456",
        "cwd": "/home/w/proj-a",
        "event": "notification",
        "message": "Claude needs your permission to use Bash",
    }


# --------------------------------------------------------------------------
# AC-29: EventStore.record
# --------------------------------------------------------------------------

def test_permission_notification_is_stored_as_pending_permission():
    store = EventStore()
    store.record(
        {
            "session_id": "s1",
            "cwd": "/home/w/proj-a/service",
            "event": "notification",
            "message": "Claude needs your permission to use Bash",
        }
    )
    event = store.get("s1")

    assert isinstance(event, SessionEvent)
    assert (event.cwd, event.pending_permission, event.ts != 0.0) == (
        "/home/w/proj-a/service",
        "Claude needs your permission to use Bash",
        True,
    )


def test_ordinary_notification_leaves_pending_permission_unset():
    store = EventStore()
    store.record(
        {
            "session_id": "s1",
            "cwd": "/home/w/proj-a",
            "event": "notification",
            "message": "Claude is waiting for your input",
        }
    )

    assert store.get("s1").pending_permission is None


def test_stop_event_clears_a_pending_permission_and_records_the_message():
    store = EventStore()
    store.record(
        {
            "session_id": "s1",
            "cwd": "/home/w/proj-a",
            "event": "notification",
            "message": "Claude needs your permission to use Bash",
        }
    )
    store.record(
        {
            "session_id": "s1",
            "cwd": "/home/w/proj-a",
            "event": "stop",
            "message": "done deploying",
        }
    )
    event = store.get("s1")

    assert (event.pending_permission, event.last_message) == (None, "done deploying")


def test_store_keeps_only_the_latest_event_per_session():
    store = EventStore()
    for message in ("first", "second", "third"):
        store.record(
            {"session_id": "s1", "cwd": "/home/w/proj-a", "event": "stop", "message": message}
        )

    assert store.get("s1").last_message == "third"


# --------------------------------------------------------------------------
# AC-30: tab_for_session
# --------------------------------------------------------------------------

def test_exact_cwd_match_beats_a_shorter_prefix_match():
    store = EventStore()
    store.record(
        {"session_id": "s1", "cwd": "/home/w/proj-a/service", "event": "stop", "message": "x"}
    )

    assert store.tab_for_session("s1", WINDOWS) == 1


def test_longest_prefix_wins_when_no_window_matches_exactly():
    store = EventStore()
    store.record(
        {
            "session_id": "s1",
            "cwd": "/home/w/proj-a/service/deep/nested",
            "event": "stop",
            "message": "x",
        }
    )

    assert store.tab_for_session("s1", WINDOWS) == 1


def test_tab_for_session_returns_none_when_nothing_matches():
    store = EventStore()
    store.record({"session_id": "s1", "cwd": "/var/tmp/other", "event": "stop", "message": "x"})

    assert store.tab_for_session("s1", WINDOWS) is None


def test_tab_for_session_returns_none_for_an_empty_window_list():
    store = EventStore()
    store.record({"session_id": "s1", "cwd": "/home/w/proj-a", "event": "stop", "message": "x"})

    assert store.tab_for_session("s1", []) is None


def test_tab_for_session_returns_none_for_an_unknown_session():
    assert EventStore().tab_for_session("never-seen", WINDOWS) is None


# --------------------------------------------------------------------------
# AC-31: POST /events
# --------------------------------------------------------------------------

def test_post_events_delegates_to_event_store_record(monkeypatch):
    recorded: list[dict] = []
    monkeypatch.setattr(
        EventStore, "record", lambda self, payload: recorded.append(payload)
    )
    payload = {
        "session_id": "abc123",
        "cwd": "/home/w/proj-a/service",
        "event": "stop",
        "message": "the retry is fixed",
    }

    status, body = _post_asgi(jarvis_server.app, "/events", payload)

    assert status == 200
    assert body == b""
    assert recorded == [payload]


def test_server_binds_to_loopback_only():
    assert jarvis_server.HOST == "127.0.0.1"


# --------------------------------------------------------------------------
# AC-32: claude_summary
# --------------------------------------------------------------------------

def test_claude_summary_prefers_the_stored_last_message():
    store = EventStore()
    store.record(
        {
            "session_id": "s1",
            "cwd": "/home/w/proj-a/service",
            "event": "stop",
            "message": "tests are green",
        }
    )

    with patch("jarvis.events.tmux_list", return_value=WINDOWS) as mock_list, patch(
        "jarvis.events.tmux_read"
    ) as mock_read:
        summary = claude_summary(tab=1, store=store)

    assert (summary, mock_read.call_count, mock_list.call_count) == (
        "tests are green",
        0,
        1,
    )


def test_claude_summary_falls_back_when_no_session_maps_to_the_tab():
    # Store has an event, but it maps to tab 1 (see WINDOWS) — requesting a
    # different tab must not "any message in the store" its way to a hit.
    store = EventStore()
    store.record(
        {
            "session_id": "s1",
            "cwd": "/home/w/proj-a/service",
            "event": "stop",
            "message": "tests are green",
        }
    )

    with patch("jarvis.events.tmux_list", return_value=WINDOWS), patch(
        "jarvis.events.tmux_read", return_value="cleaned pane text"
    ):
        summary = claude_summary(tab=2, store=store)

    assert summary == "cleaned pane text"


def test_claude_summary_falls_back_to_two_hundred_lines_of_pane_text():
    with patch("jarvis.events.tmux_read", return_value="cleaned pane text") as mock_read:
        summary = claude_summary(tab=2, store=EventStore())

    args, kwargs = mock_read.call_args
    assert summary == "cleaned pane text"
    assert (args[0], args[1] if len(args) > 1 else kwargs.get("lines")) == (2, 200)

# spec: specs/cascade-a.md::Acceptance criteria::AC-29..32
"""Claude Code hook event ingest (leaf-06, spec_lines 54-60).

``EventStore`` tracks the latest event per Claude Code session (keyed by
``session_id``), maps a session to a tmux window via its recorded ``cwd``,
and ``claude_summary`` renders a one-line status for a tmux tab, preferring
a recorded message over a raw pane read.
"""
from __future__ import annotations

import re
import time

from jarvis.tools.tmux import tmux_list, tmux_read
from jarvis.types import SessionEvent, TmuxWindow

# Substring match (case-insensitive) identifying a Claude Code permission
# prompt notification. spec_lines 57.
_PERMISSION_PATTERN = re.compile(r"permission", re.IGNORECASE)


class EventStore:
    """In-memory store of the latest event per session_id. spec_lines 57-58."""

    def __init__(self) -> None:
        self._events: dict[str, SessionEvent] = {}

    def record(self, payload: dict) -> None:
        session_id = payload["session_id"]
        cwd = payload.get("cwd")
        event_type = payload.get("event")
        message = payload.get("message")

        event = self._events.get(session_id)
        if event is None:
            event = SessionEvent(cwd=cwd)
            self._events[session_id] = event

        event.cwd = cwd
        event.ts = time.time()

        if event_type == "stop":
            event.pending_permission = None
            event.last_message = message
        elif event_type == "notification":
            if message and _PERMISSION_PATTERN.search(message):
                event.pending_permission = message

    def get(self, session_id: str) -> SessionEvent | None:
        return self._events.get(session_id)

    def session_ids(self) -> list[str]:
        return list(self._events.keys())

    def tab_for_session(
        self, session_id: str, windows: list[TmuxWindow]
    ) -> int | None:
        event = self._events.get(session_id)
        if event is None or not windows:
            return None

        cwd = event.cwd
        for window in windows:
            if window.pane_path == cwd:
                return window.index

        best_index: int | None = None
        best_len = -1
        for window in windows:
            prefix = window.pane_path.rstrip("/") + "/"
            if cwd.startswith(prefix) and len(prefix) > best_len:
                best_len = len(prefix)
                best_index = window.index
        return best_index


def claude_summary(tab: int, store: EventStore, session: str | None = None) -> str:
    """spec_lines 60."""
    session_ids = store.session_ids()
    if session_ids:
        windows = tmux_list(session=session)
        for session_id in session_ids:
            if store.tab_for_session(session_id, windows) == tab:
                event = store.get(session_id)
                if event is not None and event.last_message is not None:
                    return event.last_message
                break

    return tmux_read(tab, 200, session=session)

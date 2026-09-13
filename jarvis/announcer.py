"""Proactive announcements from Claude Code hook events.

``Announcer.poll()`` compares the ``EventStore`` against what has already
been announced and returns the sentences Jarvis should speak unprompted:
"Tab two finished." when a session's Stop hook fires, "Tab two is asking for
permission: ..." when a permission prompt arrives. The caller (the desktop
runner) queues them as ``TTSSpeakFrame``s.

Pure logic, no Pipecat, so it is unit-testable without a pipeline.
"""
from __future__ import annotations

from jarvis.session import JarvisSession
from jarvis.tools import tmux as tmux_tools

_NUMBER_WORDS = {
    0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
}


def tab_phrase(tab: int | None, name: str | None = None) -> str:
    if tab is None:
        return "an unknown tab"
    word = _NUMBER_WORDS.get(tab, str(tab))
    return f"tab {word}, {name}" if name else f"tab {word}"


class Announcer:
    def __init__(self, session: JarvisSession, *, max_prompt_chars: int = 200) -> None:
        self._session = session
        self._max_prompt_chars = max_prompt_chars

    def poll(self) -> list[str]:
        store = self._session.event_store
        session_ids = store.session_ids()
        if not session_ids:
            return []

        windows = tmux_tools.tmux_list(session=self._session.tmux_session)
        names = {w.index: w.name for w in windows}
        announcements: list[str] = []

        for session_id in session_ids:
            event = store.get(session_id)
            if event is None:
                continue
            last_seen = self._session.announced.get(session_id)
            if last_seen is not None and event.ts <= last_seen:
                continue
            self._session.announced[session_id] = event.ts

            tab = store.tab_for_session(session_id, windows)
            phrase = tab_phrase(tab, names.get(tab) if tab is not None else None)
            if event.pending_permission:
                prompt = event.pending_permission[: self._max_prompt_chars]
                announcements.append(
                    f"{phrase.capitalize()} is asking for permission: {prompt}. Shall I allow it?"
                )
            elif event.last_message:
                announcements.append(f"{phrase.capitalize()} has finished. Want me to read it?")
        return announcements

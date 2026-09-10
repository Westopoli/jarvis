# spec: specs/cascade-a.md::Acceptance criteria::AC-1..7
"""tmux control tools for Jarvis (leaf-01, spec_lines 9-16).

Thin wrappers around the `tmux` CLI: list windows in a session, fuzzy-resolve
a human query to a window index, read cleaned pane text, and (guarded by an
explicit confirmation + an allow-listed pane command) send keystrokes into a
pane.
"""
from __future__ import annotations

import os
import re
import subprocess
from difflib import SequenceMatcher
from pathlib import Path

from jarvis.types import TmuxWindow

# Box-drawing characters used to frame Claude Code / other TUI panes.
_BOX_CHARS = "─│┌┐└┘╭╮╯╰┃┏┓┗┛"
# Known Claude Code TUI spinner glyphs (braille spinner frames plus the
# asterisk-style "thinking" glyphs it also cycles through).
_SPINNER_GLYPHS = "✳✢✶✻✽⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

# Below this similarity score, resolve_tab reports no match at all.
_MIN_SIMILARITY = 0.5

# Pane commands tmux_send is allowed to type into without `force=True`.
_ALLOWED_SEND_COMMANDS = ("claude", "node")


def _resolve_session(session: str | None) -> str:
    if session is not None:
        return session
    return os.environ.get("JARVIS_TMUX_SESSION") or "main"


def _clean_pane_text(raw: str) -> str:
    """Strip ANSI escapes plus box-drawing/spinner chrome lines (spec_lines 12)."""
    text = _ANSI_RE.sub("", raw)
    kept = []
    for line in text.split("\n"):
        lead = line.lstrip()
        if lead and (lead[0] in _BOX_CHARS or lead[0] in _SPINNER_GLYPHS):
            continue
        kept.append(line)
    return "\n".join(kept)


def tmux_list(session: str | None = None) -> list[TmuxWindow]:
    session = _resolve_session(session)
    result = subprocess.run(
        [
            "tmux",
            "list-windows",
            "-t",
            session,
            "-F",
            "#I|#W|#{pane_current_path}|#{pane_current_command}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    windows: list[TmuxWindow] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        index_str, name, pane_path, pane_command = line.split("|", 3)
        windows.append(
            TmuxWindow(
                index=int(index_str),
                name=name,
                pane_path=pane_path,
                pane_command=pane_command,
            )
        )
    return windows


def resolve_tab(query: str, session: str | None = None) -> int | None:
    try:
        return int(query)
    except ValueError:
        pass

    best_index: int | None = None
    best_score = 0.0
    for window in tmux_list(session=session):
        for candidate in (window.name, Path(window.pane_path).name):
            score = SequenceMatcher(None, query.lower(), candidate.lower()).ratio()
            if score > best_score:
                best_score = score
                best_index = window.index

    if best_score >= _MIN_SIMILARITY:
        return best_index
    return None


def tmux_read(tab: int, lines: int, session: str | None = None) -> str:
    session = _resolve_session(session)
    result = subprocess.run(
        [
            "tmux",
            "capture-pane",
            "-p",
            "-t",
            f"{session}:{tab}",
            "-S",
            f"-{lines}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return _clean_pane_text(result.stdout)


def tmux_send(
    tab: int,
    text: str,
    confirmed: bool = False,
    force: bool = False,
    session: str | None = None,
) -> None:
    if not confirmed:
        raise PermissionError("tmux_send requires confirmed=True")

    session = _resolve_session(session)

    if not force:
        window = next(
            (w for w in tmux_list(session=session) if w.index == tab), None
        )
        allowed = window is not None and window.pane_command in _ALLOWED_SEND_COMMANDS
        if not allowed:
            raise PermissionError(
                f"tmux_send refused: pane {tab} is not running an allowed "
                "command (use force=True to override)"
            )

    target = f"{session}:{tab}"
    subprocess.run(["tmux", "send-keys", "-t", target, "-l", text], check=True)
    subprocess.run(["tmux", "send-keys", "-t", target, "Enter"], check=True)

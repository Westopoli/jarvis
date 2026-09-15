# spec: specs/cascade-c.md::Acceptance criteria::AC-12..13
"""Filler-phrase strategy for Jarvis tool calls (leaf-04, AC-12-13).

``FILLERS`` maps each of the five tool names exposed by
``jarvis.tools.registry.TOOL_SCHEMAS`` to a list of short spoken phrases a
voice assistant can say while the tool call is in flight. The tool names are
hardcoded here rather than imported from the registry, mirroring how
``jarvis/intent.py``'s ``filler_for`` already avoids that import.
"""
from __future__ import annotations

import random as _random_module
from typing import Optional

FILLERS: dict[str, list[str]] = {
    "tmux_list": [
        "Let me check your tmux windows.",
        "Taking a look at your tabs now.",
    ],
    "switch_tab": [
        "Switching tabs for you.",
        "One moment, changing tabs.",
    ],
    "claude_summary": [
        "Let me see what Claude's been up to.",
        "Checking on that session for you.",
    ],
    "send_prompt": [
        "Sending that over now.",
        "One second, sending your message.",
    ],
    "resume_reading": [
        "Picking up where we left off.",
        "Resuming that for you now.",
    ],
    "stage_prompt": [
        "Got it. Let me read that back.",
        "Right. Here's what I have.",
    ],
    "find_tab": [
        "Let me look for that.",
        "Searching the tabs.",
    ],
    "summarize_tab": [
        "Let me see what Claude's been up to.",
        "One moment, having a look.",
    ],
}

_FALLBACK_PHRASE = "One moment, please."


def pick_filler(tool_name: str, rng: Optional[object] = None) -> str:
    """Return one filler phrase for ``tool_name``.

    Uses ``rng.choice(...)`` when ``rng`` is supplied; otherwise falls back
    to the module-level ``random`` functions. Unrecognised tool names never
    raise ``KeyError`` — they fall back to a generic non-empty phrase.
    """
    phrases = FILLERS.get(tool_name)
    if not phrases:
        return _FALLBACK_PHRASE

    if rng is not None:
        return rng.choice(phrases)
    return _random_module.choice(phrases)

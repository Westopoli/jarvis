"""Per-conversation state shared by the tool handlers and the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jarvis.events import EventStore


@dataclass
class StagedPrompt:
    tab: int
    text: str


@dataclass
class JarvisSession:
    """Everything a single voice session needs to remember between tool calls.

    ``narrator`` is set by ``build_pipeline`` once the pipeline exists;
    handlers that need it must tolerate ``None`` (text-only REPL, tests).
    """

    tmux_session: str = "main"
    active_tab: int | None = None
    staged: StagedPrompt | None = None
    event_store: EventStore = field(default_factory=EventStore)
    narrator: Any | None = None
    # session_id -> last event timestamp already announced to the user.
    announced: dict[str, float] = field(default_factory=dict)

"""Shared type contract across cascades. Parent-owned; leaves import shapes
from here, never edit this file. Only cross-leaf shared value types live
here — each leaf implements its own callables in its own module (see each
spec's per-leaf file headers), importing these shapes where it needs them.

Every symbol comment-cites the spec file + line(s) it encodes.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


# ---------------------------------------------------------------------------
# jarvis/tools/tmux.py  (spec_lines 9-16)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TmuxWindow:
    """One tmux window row. spec_lines 10."""
    index: int
    name: str
    pane_path: str
    pane_command: str


# ---------------------------------------------------------------------------
# jarvis/conversation.py  (spec_lines 18-33)
# ---------------------------------------------------------------------------

class ConversationState(str, Enum):
    """spec_lines 20-29."""
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    NARRATING = "NARRATING"
    DRAFTING = "DRAFTING"
    CONFIRMING = "CONFIRMING"
    SMALLTALK = "SMALLTALK"


@dataclass(frozen=True)
class SendPrompt:
    """spec_lines 28, 33."""
    tab: int
    text: str


@dataclass
class TranscriptEvent:
    """spec_lines 25, 30."""
    text: str
    words: int
    has_wake_word: bool = False
    addressed: bool = False


# ---------------------------------------------------------------------------
# jarvis/audio/hallucination.py  (spec_lines 44)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WhisperSegment:
    """spec_lines 44."""
    text: str
    no_speech_prob: float
    avg_logprob: float


# ---------------------------------------------------------------------------
# jarvis/audio/gate.py  (spec_lines 46-52)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VADParams:
    """spec_lines 47."""
    confidence: float
    start_secs: float
    stop_secs: float
    min_volume: float


WakeWordDetector = Callable[[str], bool]


@dataclass(frozen=True)
class AudioSegmentResult:
    """Transcription + VAD result for one audio segment, fed to InterruptGate.
    spec_lines 47-52."""
    words: int
    vad_active: bool
    text: str
    segment: WhisperSegment | None = None


# ---------------------------------------------------------------------------
# jarvis/events.py  (spec_lines 54-60)
# ---------------------------------------------------------------------------

@dataclass
class SessionEvent:
    """spec_lines 57."""
    cwd: str
    last_message: str | None = None
    pending_permission: str | None = None
    ts: float = 0.0


# ---------------------------------------------------------------------------
# jarvis/repl.py, jarvis/tools/registry.py  (cascade-b.md spec_lines 3, 11-12)
# ---------------------------------------------------------------------------

@dataclass
class SessionState:
    """Per-conversation state threaded through tool dispatch and the REPL.
    cascade-b.md spec_lines 3, 12. `event_store` defaults to a fresh
    `jarvis.events.EventStore` (cascade A, already real) so `dispatch`'s
    `claude_summary` route (spec_lines 12) always has a store to read,
    without importing jarvis.events at the dataclass-definition site."""
    active_tab: int | None = None
    reader: Any | None = None
    event_store: Any | None = None

    def __post_init__(self) -> None:
        if self.event_store is None:
            from jarvis.events import EventStore
            self.event_store = EventStore()

"""Sentence-by-sentence narration with an interrupt-safe cursor.

``Narrator`` is a Pipecat ``FrameProcessor`` placed between the LLM and the
TTS service. Tool handlers call ``begin(text)`` to start reading a block of
text aloud; the narrator then feeds the TTS one sentence at a time, advancing
only after the output transport reports the bot stopped speaking. When the
user barges in, Pipecat broadcasts an ``InterruptionFrame`` — the narrator
pauses and keeps its cursor on the sentence that was cut off, so ``resume()``
picks up exactly there.

Why one sentence at a time rather than one big ``TTSSpeakFrame``: the cursor
is what makes "resume where you left off" possible after an interruption.
"""
from __future__ import annotations

import enum

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InterruptionFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from jarvis.reader import Reader


class NarrationState(str, enum.Enum):
    IDLE = "IDLE"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"


class Narrator(FrameProcessor):
    """Reads text aloud one sentence at a time, pausing on interruption."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._reader: Reader | None = None
        self._state = NarrationState.IDLE
        # True between pushing a sentence and hearing BotStartedSpeaking for
        # it. Guards against a BotStoppedSpeaking that belongs to earlier
        # audio (an LLM reply or filler still draining) advancing the cursor.
        self._awaiting_start = False
        self._speaking = False

    # -- public API (called from tool handlers) --------------------------

    @property
    def state(self) -> NarrationState:
        return self._state

    @property
    def has_narration(self) -> bool:
        return self._reader is not None

    async def begin(self, text: str) -> None:
        """Start narrating ``text`` from its first sentence."""
        self._reader = Reader(text)
        await self._play(self._reader.next())

    async def resume(self) -> bool:
        """Re-speak the sentence that was interrupted and continue from there.

        Returns False when there is nothing to resume.
        """
        if self._reader is None or self._state is NarrationState.PLAYING:
            return False
        sentence = self._reader.resume()
        if sentence is None:
            return False
        await self._play(sentence)
        return True

    async def skip(self) -> bool:
        """Skip the current sentence and continue with the next one."""
        if self._reader is None:
            return False
        if self._state is NarrationState.PAUSED:
            # The cursor already sits past the interrupted sentence.
            await self._play(self._reader.next())
            return True
        # Playing: the current sentence keeps playing; drop the one after it.
        self._reader.skip()
        return True

    async def stop(self) -> None:
        """Abandon the current narration."""
        self._reader = None
        self._state = NarrationState.IDLE
        self._awaiting_start = False

    # -- frame handling ---------------------------------------------------

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            if self._state is NarrationState.PLAYING:
                self._state = NarrationState.PAUSED
            self._awaiting_start = False
            self._speaking = False
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._speaking = True
            if self._awaiting_start:
                self._awaiting_start = False
        elif isinstance(frame, BotStoppedSpeakingFrame):
            was_ours = self._speaking and not self._awaiting_start
            self._speaking = False
            if was_ours and self._state is NarrationState.PLAYING:
                await self._advance()

        await self.push_frame(frame, direction)

    # -- internals --------------------------------------------------------

    async def _advance(self) -> None:
        assert self._reader is not None
        await self._play(self._reader.next())

    async def _play(self, sentence: str | None) -> None:
        if sentence is None:
            self._state = NarrationState.IDLE
            self._reader = None
            return
        self._state = NarrationState.PLAYING
        self._awaiting_start = True
        await self.push_frame(TTSSpeakFrame(text=sentence), FrameDirection.DOWNSTREAM)

"""Sentence-by-sentence narration with an interrupt-safe cursor.

``Narrator`` is a Pipecat ``FrameProcessor`` placed between the LLM and the
TTS service. Tool handlers call ``begin(text)`` to read a block of text
aloud. Sentences are handed to the TTS one at a time with a lookahead of
one, so the next sentence is synthesised while the current one plays (no
gaps) but never more than one is queued (so an interruption loses at most
one sentence of position).

How the cursor stays accurate: the output transport processes the TTS
service's ``TTSStoppedFrame`` in order with the audio, so it reports
``BotStoppedSpeakingFrame`` once per sentence, exactly when that sentence's
audio has finished playing. Each such frame advances ``played``. On an
``InterruptionFrame`` the queued sentence is discarded by Pipecat and the
narrator pauses with the cursor on the sentence that was cut off, so
``resume()`` re-speaks it.
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

LOOKAHEAD = 1


class NarrationState(str, enum.Enum):
    IDLE = "IDLE"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"


class Narrator(FrameProcessor):
    """Reads text aloud one sentence at a time, pausing on interruption."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._sentences: list[str] = []
        self._played = 0  # sentences whose audio finished
        self._pushed = 0  # sentences handed to the TTS
        self._state = NarrationState.IDLE
        # Set once we hear BotStartedSpeaking after our own push, so a
        # BotStoppedSpeaking that belongs to earlier audio (an LLM reply or a
        # filler still draining) cannot advance the cursor.
        self._armed = False

    # -- public API (called from tool handlers) --------------------------

    @property
    def state(self) -> NarrationState:
        return self._state

    @property
    def has_narration(self) -> bool:
        return bool(self._sentences)

    @property
    def position(self) -> tuple[int, int]:
        """(sentences finished, total)."""
        return self._played, len(self._sentences)

    async def begin(self, text: str) -> None:
        """Start narrating ``text`` from its first sentence."""
        self._sentences = Reader(text)._sentences
        self._played = 0
        self._pushed = 0
        self._armed = False
        if not self._sentences:
            self._state = NarrationState.IDLE
            return
        self._state = NarrationState.PLAYING
        await self._fill()

    async def resume(self) -> bool:
        """Re-speak the sentence that was interrupted and continue from there."""
        if self._state is not NarrationState.PAUSED:
            return False
        self._state = NarrationState.PLAYING
        self._pushed = self._played
        self._armed = False
        await self._fill()
        return True

    async def skip(self) -> bool:
        """Skip a sentence: the interrupted one when paused, the next unqueued
        one when playing (the queued lookahead cannot be recalled)."""
        if not self._sentences:
            return False
        if self._state is NarrationState.PAUSED:
            self._played += 1
            if self._played >= len(self._sentences):
                await self.stop()
                return True
            return await self.resume()
        if self._pushed < len(self._sentences):
            del self._sentences[self._pushed]
        return True

    async def say(self, text: str) -> None:
        """Speak a one-off sentence (a filler) without touching the cursor.

        Not added to the LLM context: fillers are noise there.
        """
        await self.push_frame(
            TTSSpeakFrame(text=text, append_to_context=False), FrameDirection.DOWNSTREAM
        )

    async def stop(self) -> None:
        """Abandon the current narration."""
        self._sentences = []
        self._played = self._pushed = 0
        self._state = NarrationState.IDLE
        self._armed = False

    # -- frame handling ---------------------------------------------------

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            if self._state is NarrationState.PLAYING:
                # Pipecat drops the queued lookahead; cursor stays on the
                # sentence that was cut off (index == self._played).
                self._state = NarrationState.PAUSED
                self._pushed = self._played
            self._armed = False
        elif isinstance(frame, BotStartedSpeakingFrame):
            if self._state is NarrationState.PLAYING and self._pushed > self._played:
                self._armed = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            if self._state is NarrationState.PLAYING and self._armed:
                self._played += 1
                if self._played >= len(self._sentences):
                    await self.stop()
                else:
                    await self._fill()

        await self.push_frame(frame, direction)

    # -- internals --------------------------------------------------------

    async def _fill(self) -> None:
        limit = min(len(self._sentences), self._played + 1 + LOOKAHEAD)
        while self._pushed < limit:
            sentence = self._sentences[self._pushed]
            self._pushed += 1
            await self.push_frame(TTSSpeakFrame(text=sentence), FrameDirection.DOWNSTREAM)

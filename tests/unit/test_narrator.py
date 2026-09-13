"""Narrator: sentence cursor, advance on bot-stopped, pause on interruption."""
from __future__ import annotations

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InterruptionFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from jarvis.narrator import Narrator, NarrationState

TEXT = "First sentence. Second sentence. Third sentence."


class _Sink(FrameProcessor):
    def __init__(self) -> None:
        super().__init__(enable_direct_mode=True, name="Sink")
        self.spoken: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSSpeakFrame):
            self.spoken.append(frame.text)


@pytest.fixture
def narrator():
    n = Narrator(enable_direct_mode=True, name="Narrator")
    sink = _Sink()
    n.link(sink)
    return n, sink


async def _bot_cycle(n: Narrator) -> None:
    await n.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    await n.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)


async def test_begin_speaks_only_first_sentence(narrator):
    n, sink = narrator
    await n.begin(TEXT)
    assert sink.spoken == ["First sentence."]
    assert n.state is NarrationState.PLAYING


async def test_advances_one_sentence_per_bot_stop(narrator):
    n, sink = narrator
    await n.begin(TEXT)
    await _bot_cycle(n)
    await _bot_cycle(n)
    assert sink.spoken == ["First sentence.", "Second sentence.", "Third sentence."]
    await _bot_cycle(n)
    assert n.state is NarrationState.IDLE
    assert not n.has_narration


async def test_interruption_pauses_and_resume_repeats_cut_sentence(narrator):
    n, sink = narrator
    await n.begin(TEXT)
    await _bot_cycle(n)  # now speaking "Second sentence."
    await n.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    await n.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
    assert n.state is NarrationState.PAUSED
    # A stop that follows the interruption must not advance the cursor.
    await n.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    assert sink.spoken == ["First sentence.", "Second sentence."]

    assert await n.resume() is True
    assert sink.spoken[-1] == "Second sentence."
    assert n.state is NarrationState.PLAYING


async def test_skip_while_paused_moves_to_next_sentence(narrator):
    n, sink = narrator
    await n.begin(TEXT)
    await n.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
    assert await n.skip() is True
    assert sink.spoken == ["First sentence.", "Second sentence."]


async def test_unrelated_bot_stop_does_not_advance(narrator):
    """A BotStoppedSpeaking for audio that started before our push (a filler
    or an LLM reply draining) must not count as our sentence finishing."""
    n, sink = narrator
    await n.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)  # filler
    await n.begin(TEXT)
    await n.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)  # filler ends
    assert sink.spoken == ["First sentence."]
    await _bot_cycle(n)
    assert sink.spoken == ["First sentence.", "Second sentence."]


async def test_resume_with_nothing_returns_false(narrator):
    n, _ = narrator
    assert await n.resume() is False
    await n.begin(TEXT)
    assert await n.resume() is False  # already playing


async def test_stop_clears(narrator):
    n, sink = narrator
    await n.begin(TEXT)
    await n.stop()
    assert n.state is NarrationState.IDLE
    await _bot_cycle(n)
    assert sink.spoken == ["First sentence."]

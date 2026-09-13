"""Narrator: lookahead of one, advance on bot-stopped, pause on interruption."""
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

TEXT = "First sentence. Second sentence. Third sentence. Fourth sentence."


class _Sink(FrameProcessor):
    def __init__(self) -> None:
        super().__init__(enable_direct_mode=True, name="Sink")
        self.spoken: list[str] = []
        self.frames: list[TTSSpeakFrame] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSSpeakFrame):
            self.spoken.append(frame.text)
            self.frames.append(frame)


@pytest.fixture
def narrator():
    n = Narrator(enable_direct_mode=True, name="Narrator")
    sink = _Sink()
    n.link(sink)
    return n, sink


async def _started(n):
    await n.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)


async def _stopped(n):
    await n.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)


async def _interrupt(n):
    await n.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)


async def test_begin_queues_first_two_sentences(narrator):
    n, sink = narrator
    await n.begin(TEXT)
    assert sink.spoken == ["First sentence.", "Second sentence."]
    assert n.state is NarrationState.PLAYING
    assert n.position == (0, 4)


async def test_each_bot_stop_advances_and_keeps_one_ahead(narrator):
    n, sink = narrator
    await n.begin(TEXT)
    await _started(n)
    await _stopped(n)  # first finished
    assert sink.spoken == ["First sentence.", "Second sentence.", "Third sentence."]
    assert n.position == (1, 4)
    await _stopped(n)  # second finished
    assert sink.spoken[-1] == "Fourth sentence."
    await _stopped(n)
    await _stopped(n)
    assert n.state is NarrationState.IDLE
    assert not n.has_narration


async def test_interruption_pauses_on_cut_sentence_and_resume_repeats_it(narrator):
    n, sink = narrator
    await n.begin(TEXT)
    await _started(n)
    await _stopped(n)  # first done, second playing, third queued
    await _interrupt(n)
    assert n.state is NarrationState.PAUSED
    assert n.position == (1, 4)
    await _stopped(n)  # the transport's stop for the cut audio: ignored
    assert n.position == (1, 4)

    assert await n.resume() is True
    # Re-speaks the cut sentence and re-queues the lookahead Pipecat dropped.
    assert sink.spoken[-2:] == ["Second sentence.", "Third sentence."]
    assert n.state is NarrationState.PLAYING


async def test_skip_while_paused_moves_past_cut_sentence(narrator):
    n, sink = narrator
    await n.begin(TEXT)
    await _started(n)
    await _interrupt(n)  # first sentence cut
    assert await n.skip() is True
    assert sink.spoken[-2:] == ["Second sentence.", "Third sentence."]
    assert n.position == (1, 4)


async def test_skip_while_playing_drops_the_next_unqueued_sentence(narrator):
    n, sink = narrator
    await n.begin(TEXT)  # 1 playing, 2 queued
    assert await n.skip() is True  # drops 3
    await _started(n)
    await _stopped(n)
    assert sink.spoken == ["First sentence.", "Second sentence.", "Fourth sentence."]


async def test_unrelated_bot_stop_does_not_advance(narrator):
    """A stop for audio that started before our push (a filler or LLM reply
    draining) must not count as our sentence finishing."""
    n, sink = narrator
    await _started(n)  # filler playing
    await n.begin(TEXT)
    await _stopped(n)  # filler ends: not armed yet
    assert n.position == (0, 4)
    await _started(n)
    await _stopped(n)
    assert n.position == (1, 4)


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
    await _started(n)
    await _stopped(n)
    assert len(sink.spoken) == 2


async def test_empty_text_is_a_noop(narrator):
    n, sink = narrator
    await n.begin("   ")
    assert n.state is NarrationState.IDLE
    assert sink.spoken == []


async def test_say_speaks_without_context_or_cursor(narrator):
    n, sink = narrator
    await n.begin(TEXT)
    await n.say("One moment.")
    assert sink.spoken[-1] == "One moment."
    assert sink.frames[-1].append_to_context is False
    assert n.position == (0, 4)

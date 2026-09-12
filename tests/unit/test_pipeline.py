# spec: specs/cascade-b.md::Acceptance criteria::AC-8..10
"""Tests for pipeline assembly and the fake transport harness (leaf-03, spec_lines 20-23).

Nothing cascade-A is mocked here: the ``Conversation``, ``Reader`` and
``InterruptGate`` handed to ``build_pipeline`` are the real admitted objects,
and the ``Pipeline`` that comes back is a real
``pipecat.pipeline.pipeline.Pipeline``.  The composition assertion AC-10 asks
for is therefore observable rather than structural -- the pipeline is proved
to consult those three objects because their real rules decide what the fake
transport captures: the real ``InterruptGate``'s word-count floor and
wake-word rule decide whether narration keeps flowing, the real
``Conversation``'s NARRATING gate decides the state it lands in, and the real
``Reader``'s sentence split decides the text that comes out.

That still leaves AC-9's own claim -- that the returned ``Pipeline`` object is
what actually carries frames from ``transport`` through those objects --
unproven by state assertions alone, since a ``FakeTransport`` could reach the
same states by calling ``conversation``/``gate``/``reader`` directly and never
touch the ``Pipeline`` at all.
``test_pushing_a_transcript_genuinely_drives_frames_through_the_real_pipeline``
closes that gap structurally: any external frame handed to a real Pipecat
``Pipeline`` is, by that class's own ``process_frame``
(``pipecat/pipeline/pipeline.py``), necessarily routed through
``pipeline.processors[0]`` (its source) for downstream delivery, and a
downstream frame reaching the transport's captured output can only have done
so by cascading, link by link, all the way to ``pipeline.processors[-1]``
(its sink) -- so spying on those two real ``FrameProcessor.process_frame``
methods and asserting both actually ran makes a ``FakeTransport``/
``build_pipeline`` pair that routes around the ``Pipeline`` object fail here.
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

from pipecat.pipeline.pipeline import Pipeline

from jarvis.audio.gate import InterruptGate
from jarvis.conversation import Conversation
from jarvis.pipeline import build_pipeline
from jarvis.reader import Reader
from jarvis.types import ConversationState, VADParams
from tests.harness.fake_transport import FakeTransport

NARRATION = (
    "First narrated sentence. Second narrated sentence. Third narrated sentence. "
    "Fourth narrated sentence. Fifth narrated sentence."
)
MIN_WORDS = 3
VAD = VADParams(confidence=0.7, start_secs=0.3, stop_secs=0.8, min_volume=0.6)


def _narrating(min_words: int = MIN_WORDS):
    """A built pipeline with the real Conversation already in NARRATING."""
    transport = FakeTransport()
    conversation = Conversation(active_tab=1, min_words=min_words)
    reader = Reader(NARRATION)
    gate = InterruptGate(
        vad_params=VAD,
        min_words=min_words,
        wake_word_detector=lambda text: "jarvis" in text.lower(),
    )
    pipeline = build_pipeline(transport, conversation, reader, gate)

    conversation.speech_start()
    conversation.llm_intent("narrate")
    assert conversation.state is ConversationState.NARRATING
    return transport, conversation, pipeline


def _narration_frames(texts):
    return [text for text in texts if "narrated sentence" in text]


# ---------------------------------------------------------------------------
# AC-9 -- build_pipeline returns a real Pipeline (spec_lines 22)
# ---------------------------------------------------------------------------

def test_build_pipeline_returns_a_real_pipecat_pipeline():
    # spec_lines 22
    transport, _conversation, pipeline = _narrating()
    assert isinstance(pipeline, Pipeline)
    assert len(pipeline.processors) >= 3


def test_pushing_a_transcript_genuinely_drives_frames_through_the_real_pipeline():
    """AC-9's composition claim, made structural (spec_lines 22).

    A ``FakeTransport`` that calls ``conversation``/``gate``/``reader``
    directly -- bypassing the ``Pipeline`` object entirely -- would still
    pass every state-based assertion in this file. Spying on the real
    ``Pipeline``'s own boundary processors closes that gap: any frame
    ``FakeTransport`` hands to the actual pipeline entry point
    (``pipeline.process_frame``/``queue_frame``, or the equivalent call on
    its first processor) must pass through ``pipeline.processors[0]``, and
    narration text cannot land in ``emitted_tts_text()`` without cascading,
    real link by real link, all the way to ``pipeline.processors[-1]``.
    """
    transport, _conversation, pipeline = _narrating()

    entry, exit_ = pipeline.processors[0], pipeline.processors[-1]
    entry_spy = AsyncMock(wraps=entry.process_frame)
    exit_spy = AsyncMock(wraps=exit_.process_frame)
    entry.process_frame = entry_spy
    exit_.process_frame = exit_spy

    transport.push_transcript("mm hmm", words=2, has_wake_word=False)

    assert entry_spy.await_count >= 1, "the pipeline's own source never ran process_frame"
    assert exit_spy.await_count >= 1, "the pipeline's own sink never ran process_frame"
    assert _narration_frames(transport.emitted_tts_text())


# ---------------------------------------------------------------------------
# AC-9 -- build_pipeline threads llm_model through, not just accepts it
# ---------------------------------------------------------------------------


def test_build_pipeline_threads_the_llm_model_argument_through():
    """spec_lines 22: ``llm_model="qwen3:8b"`` must not be a silently-dropped
    default -- both the default value and a caller-supplied override must be
    observable on whatever ``build_pipeline`` hands back."""
    assert inspect.signature(build_pipeline).parameters["llm_model"].default == "qwen3:8b"

    transport = FakeTransport()
    conversation = Conversation(active_tab=1, min_words=MIN_WORDS)
    reader = Reader(NARRATION)
    gate = InterruptGate(
        vad_params=VAD,
        min_words=MIN_WORDS,
        wake_word_detector=lambda text: "jarvis" in text.lower(),
    )
    pipeline = build_pipeline(transport, conversation, reader, gate, llm_model="llama3.1:70b")

    assert pipeline.llm_model == "llama3.1:70b"


# ---------------------------------------------------------------------------
# AC-8 -- the fake transport harness (spec_lines 21)
# ---------------------------------------------------------------------------

def test_fake_transport_captures_nothing_before_any_frame_is_pushed():
    """Cardinality zero (spec_lines 21)."""
    transport, _conversation, _pipeline = _narrating()
    captured = transport.emitted_tts_text()
    assert isinstance(captured, list)
    assert captured == []


def test_fake_transport_captures_tts_text_in_reader_order():
    """Ordering axis (spec_lines 21): emission order is preserved."""
    transport, _conversation, _pipeline = _narrating()
    transport.push_transcript("mm hmm", words=2, has_wake_word=False)
    transport.push_transcript("mm hmm", words=2, has_wake_word=False)

    captured = transport.emitted_tts_text()
    assert all(isinstance(text, str) for text in captured)
    joined = " ".join(captured)
    assert "First narrated sentence" in joined
    assert joined.index("First narrated") < joined.index("Second narrated")


# ---------------------------------------------------------------------------
# AC-10 -- narration, then interrupt (spec_lines 23)
# ---------------------------------------------------------------------------

def test_narration_emits_then_stops_after_a_wake_word_interrupt():
    # spec_lines 23
    transport, conversation, _pipeline = _narrating()

    transport.push_transcript("mm hmm", words=2, has_wake_word=False)
    before = list(transport.emitted_tts_text())
    assert _narration_frames(before), "narration must reach TTS before the interrupt"
    assert conversation.state is ConversationState.NARRATING

    transport.push_transcript("jarvis stop reading that", words=4, has_wake_word=True)
    assert conversation.state is ConversationState.LISTENING

    transport.push_transcript("carry on then please", words=4, has_wake_word=False)
    after = transport.emitted_tts_text()[len(before):]
    assert _narration_frames(after) == []


def test_a_short_segment_does_not_interrupt_and_narration_keeps_flowing():
    """Range boundary: ``words == min_words - 1`` is below the floor (spec_lines 23)."""
    transport, conversation, _pipeline = _narrating()

    transport.push_transcript("mm hmm", words=2, has_wake_word=False)
    before = len(transport.emitted_tts_text())
    transport.push_transcript("jarvis ok", words=MIN_WORDS - 1, has_wake_word=True)

    assert conversation.state is ConversationState.NARRATING
    assert len(transport.emitted_tts_text()) > before


def test_wake_word_at_exactly_min_words_does_interrupt():
    """Range boundary: the floor is ``>=``, so ``words == min_words`` cuts in."""
    # spec_lines 23
    transport, conversation, _pipeline = _narrating()

    transport.push_transcript("mm hmm", words=2, has_wake_word=False)
    transport.push_transcript("jarvis hold on", words=MIN_WORDS, has_wake_word=True)

    assert conversation.state is ConversationState.LISTENING


def test_a_long_segment_without_the_wake_word_never_interrupts_narration():
    """The real InterruptGate's NARRATING branch: word count alone is not enough."""
    # spec_lines 23
    transport, conversation, _pipeline = _narrating()

    transport.push_transcript("mm hmm", words=2, has_wake_word=False)
    before = len(transport.emitted_tts_text())
    transport.push_transcript(
        "that looks about right to me", words=6, has_wake_word=False
    )

    assert conversation.state is ConversationState.NARRATING
    assert len(transport.emitted_tts_text()) > before


# ---------------------------------------------------------------------------
# Real-transcript fallback (post-cascade follow-up): FakeTransport.push_transcript
# always sets words/has_wake_word on the frame's metadata, so a real
# TranscriptionFrame from an actual STT service -- which carries neither --
# was never exercised. _NarratingProcessor must compute both from frame.text
# in that case, since jarvis/run_desktop.py wires a real WhisperSTTService
# whose frames won't have this test-only metadata.
# ---------------------------------------------------------------------------


def _push_bare_transcript(transport, text: str) -> None:
    """Push a real-shaped TranscriptionFrame with no words/has_wake_word
    metadata, standing in for actual STT output (unlike
    FakeTransport.push_transcript, which always sets both)."""
    import asyncio
    import time

    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.processors.frame_processor import FrameDirection

    frame = TranscriptionFrame(text=text, user_id="fake-user", timestamp=str(time.time()))
    asyncio.run(transport._input.queue_frame(frame, FrameDirection.DOWNSTREAM))


def test_narration_advances_from_a_bare_transcript_frame_with_no_metadata():
    """A real STT frame (no words/has_wake_word metadata) must still be able
    to tick narration forward on a sub-threshold utterance."""
    transport, conversation, _pipeline = _narrating()

    before = len(transport.emitted_tts_text())
    _push_bare_transcript(transport, "mm hmm")  # 2 words, below MIN_WORDS -- no interrupt

    assert conversation.state is ConversationState.NARRATING
    assert len(transport.emitted_tts_text()) > before


def test_bare_transcript_with_wake_word_text_interrupts_narration():
    """A real STT frame whose text contains the wake word, with enough
    words, must still interrupt -- computed from frame.text, not metadata."""
    transport = FakeTransport()
    conversation = Conversation(active_tab=1, min_words=MIN_WORDS)
    reader = Reader(NARRATION)
    gate = InterruptGate(
        vad_params=VAD,
        min_words=MIN_WORDS,
        wake_word_detector=lambda text: "jarvis" in text.lower(),
    )
    build_pipeline(transport, conversation, reader, gate)
    conversation.speech_start()
    conversation.llm_intent("narrate")
    assert conversation.state is ConversationState.NARRATING

    _push_bare_transcript(transport, "jarvis stop reading that")  # 4 words, has "jarvis"

    assert conversation.state is not ConversationState.NARRATING


def test_build_pipeline_accepts_a_custom_wake_word_detector_for_bare_transcripts():
    """A caller-supplied wake_word_detector (e.g. run_desktop.py's own) must
    be what a bare (metadata-free) transcript frame is checked against, not
    just the module's default "jarvis" substring check."""
    transport = FakeTransport()
    conversation = Conversation(active_tab=1, min_words=MIN_WORDS)
    reader = Reader(NARRATION)
    gate = InterruptGate(
        vad_params=VAD,
        min_words=MIN_WORDS,
        wake_word_detector=lambda text: "computer" in text.lower(),
    )
    build_pipeline(
        transport,
        conversation,
        reader,
        gate,
        wake_word_detector=lambda text: "computer" in text.lower(),
    )
    conversation.speech_start()
    conversation.llm_intent("narrate")

    # "jarvis" alone would not match this custom detector -- confirms the
    # custom callable, not the hardcoded default, decided the outcome.
    _push_bare_transcript(transport, "jarvis please stop now")
    assert conversation.state is ConversationState.NARRATING

    _push_bare_transcript(transport, "computer please stop now")
    assert conversation.state is not ConversationState.NARRATING

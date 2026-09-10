# spec: specs/cascade-a.md::Acceptance criteria::AC-1..32
"""Cascade A umbrella test — behavioral, cross-leaf composition only.

Exercises: Reader cursor lifecycle, Conversation DRAFTING/CONFIRMING ->
SendPrompt, InterruptGate composed with the hallucination filter, and
EventStore -> claude_summary falling back to tmux_read. Per-leaf edge
cases live in each leaf's own test file, not here.
"""
from unittest.mock import patch

from jarvis.types import (
    AudioSegmentResult,
    ConversationState,
    SendPrompt,
    TranscriptEvent,
    VADParams,
    WhisperSegment,
)
from jarvis.reader import Reader
from jarvis.conversation import Conversation
from jarvis.audio.gate import InterruptGate
from jarvis.events import EventStore, claude_summary


def test_reader_reads_then_resumes_after_interruption():
    reader = Reader("First sentence. Second sentence. Third sentence.")
    assert reader.next() == "First sentence."
    assert reader.next() == "Second sentence."
    # simulate an interruption: resume must return the same sentence, not advance
    assert reader.resume() == "Second sentence."
    assert reader.next() == "Third sentence."
    assert reader.next() is None
    assert reader.done is True


def test_conversation_draft_confirm_send_flow():
    convo = Conversation(active_tab=3)
    convo.speech_start()
    convo.llm_intent("draft")
    assert convo.state == ConversationState.DRAFTING

    for fragment in ["use pydantic", "instead of", "dataclasses"]:
        result = convo.transcript(TranscriptEvent(text=fragment, words=2))
        assert result is None

    convo.transcript(TranscriptEvent(text="done", words=1))
    assert convo.state == ConversationState.CONFIRMING

    sent = convo.transcript(TranscriptEvent(text="send", words=1))
    assert sent == SendPrompt(tab=3, text="use pydantic instead of dataclasses")


def test_interrupt_gate_rejects_hallucinated_segment_even_with_wake_word():
    gate = InterruptGate(
        vad_params=VADParams(confidence=0.7, start_secs=0.3, stop_secs=0.8, min_volume=0.6),
        min_words=3,
        wake_word_detector=lambda text: "jarvis" in text.lower(),
    )
    hallucinated = AudioSegmentResult(
        words=5,
        vad_active=True,
        text="jarvis thank you for watching",
        segment=WhisperSegment(
            text="thank you for watching",
            no_speech_prob=0.9,
            avg_logprob=-1.5,
        ),
    )
    assert gate.should_interrupt(hallucinated, ConversationState.NARRATING) is False


def test_claude_summary_falls_back_to_tmux_read_when_no_event():
    store = EventStore()
    with patch("jarvis.events.tmux_read", return_value="cleaned pane text") as mock_read:
        summary = claude_summary(tab=2, store=store)
    assert summary == "cleaned pane text"
    mock_read.assert_called_once()

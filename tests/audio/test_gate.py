# spec: specs/cascade-a.md::Acceptance criteria::AC-21..26
"""Tests for ``jarvis/audio/gate.py`` (leaf-05, AC 21-26).

The hallucination check is composed from the *real* wave-1
``jarvis.audio.hallucination.is_hallucination`` rather than a stub, so AC-26's
rejection is anchored to segments that filter genuinely classifies.
"""
from __future__ import annotations

import pytest

from jarvis.audio.gate import InterruptGate
from jarvis.audio.hallucination import is_hallucination
from jarvis.types import (
    AudioSegmentResult,
    ConversationState,
    VADParams,
    WhisperSegment,
)

VAD = VADParams(confidence=0.7, start_secs=0.3, stop_secs=0.8, min_volume=0.6)
MIN_WORDS = 3


def _gate(min_words: int = MIN_WORDS) -> InterruptGate:
    return InterruptGate(
        vad_params=VAD,
        min_words=min_words,
        wake_word_detector=lambda text: "jarvis" in text.lower(),
    )


# --------------------------------------------------------------------------
# AC-21: construction
# --------------------------------------------------------------------------

def test_gate_keeps_its_vad_params_and_word_minimum():
    gate = _gate()

    assert gate.vad_params == VAD
    assert gate.min_words == MIN_WORDS


# --------------------------------------------------------------------------
# AC-22 / AC-23: word-count floor
# --------------------------------------------------------------------------

def test_honk_only_segment_reports_no_interrupt():
    # A car horn: VAD fires, nothing transcribes.
    honk = AudioSegmentResult(words=0, vad_active=True, text="")

    assert _gate().should_interrupt(honk, ConversationState.NARRATING) is False


@pytest.mark.parametrize(
    "words, vad_active",
    [(0, True), (0, False), (1, True), (2, True), (2, False)],
)
def test_below_the_word_minimum_never_interrupts_regardless_of_vad(words, vad_active):
    # Wake word present throughout, so only the word count can be deciding.
    result = AudioSegmentResult(
        words=words, vad_active=vad_active, text="jarvis stop"
    )

    assert _gate().should_interrupt(result, ConversationState.NARRATING) is False


def test_exactly_the_word_minimum_with_the_wake_word_interrupts():
    # The boundary itself: `words < min_words` is rejected, so words == min passes.
    result = AudioSegmentResult(words=MIN_WORDS, vad_active=True, text="jarvis stop reading")

    assert _gate().should_interrupt(result, ConversationState.NARRATING) is True


# --------------------------------------------------------------------------
# AC-24 / AC-25: the NARRATING wake-word gate
# --------------------------------------------------------------------------

def test_narrating_ignores_a_long_segment_without_the_wake_word():
    result = AudioSegmentResult(
        words=9, vad_active=True, text="the deploy failed again on the staging box"
    )

    assert _gate().should_interrupt(result, ConversationState.NARRATING) is False


def test_narrating_yields_to_a_long_segment_carrying_the_wake_word():
    result = AudioSegmentResult(
        words=9, vad_active=True, text="hey jarvis stop reading and open the diff"
    )

    assert _gate().should_interrupt(result, ConversationState.NARRATING) is True


def test_wake_word_segment_with_no_whisper_segment_attached_still_interrupts():
    result = AudioSegmentResult(
        words=5, vad_active=True, text="jarvis open the diff", segment=None
    )

    assert _gate().should_interrupt(result, ConversationState.NARRATING) is True


def test_outside_narrating_a_wake_word_segment_interrupts():
    result = AudioSegmentResult(words=5, vad_active=True, text="jarvis open the diff")

    assert _gate().should_interrupt(result, ConversationState.IDLE) is True


# --------------------------------------------------------------------------
# AC-26: hallucinated segments
# --------------------------------------------------------------------------

def test_low_confidence_hallucination_is_rejected_despite_wake_word():
    segment = WhisperSegment(
        text="jarvis stop reading that", no_speech_prob=0.92, avg_logprob=-1.8
    )
    result = AudioSegmentResult(
        words=6, vad_active=True, text=segment.text, segment=segment
    )

    assert is_hallucination(segment) is True
    assert _gate().should_interrupt(result, ConversationState.NARRATING) is False


def test_known_phrase_hallucination_is_rejected_despite_wake_word():
    segment = WhisperSegment(
        text="jarvis thank you for watching", no_speech_prob=0.05, avg_logprob=-0.2
    )
    result = AudioSegmentResult(
        words=5, vad_active=True, text=segment.text, segment=segment
    )

    assert _gate().should_interrupt(result, ConversationState.NARRATING) is False

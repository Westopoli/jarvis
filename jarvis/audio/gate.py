"""Interrupt gate for narration. spec_lines 46-52 (specs/cascade-a.md, AC-21..26).

``InterruptGate`` decides whether a transcribed audio segment should
interrupt an in-progress narration or otherwise be treated as user speech
worth acting on.
"""
from __future__ import annotations

from jarvis.audio.hallucination import is_hallucination
from jarvis.types import (
    AudioSegmentResult,
    ConversationState,
    VADParams,
    WakeWordDetector,
)


class InterruptGate:
    """Gates whether an audio segment should trigger an interrupt.

    spec_lines 46-52.
    """

    def __init__(
        self,
        vad_params: VADParams,
        min_words: int,
        wake_word_detector: WakeWordDetector,
    ) -> None:
        self.vad_params = vad_params
        self.min_words = min_words
        self.wake_word_detector = wake_word_detector

    def should_interrupt(
        self,
        result: AudioSegmentResult,
        conversation_state: ConversationState,
    ) -> bool:
        # AC-26: reject segments the hallucination filter flags, even when
        # a wake word is present in the (bogus) transcription.
        if result.segment is not None and is_hallucination(result.segment):
            return False

        # AC-22/23: a car horn or otherwise near-empty transcription never
        # interrupts, regardless of VAD activity.
        if result.words < self.min_words:
            return False

        # AC-24/25: while narrating, only the wake word can cut in - the
        # word-count floor above is necessary but not sufficient.
        if conversation_state is ConversationState.NARRATING:
            return bool(self.wake_word_detector(result.text))

        return True

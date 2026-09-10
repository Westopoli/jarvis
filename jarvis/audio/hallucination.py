"""Whisper hallucination filter. spec_lines 44 (specs/cascade-a.md, AC-20).

Whisper occasionally "hallucinates" fixed boilerplate phrases on segments that
contain little or no real speech (silence, noise, music). ``is_hallucination``
flags a segment as a hallucination when the model itself is unconfident about
it (both probability thresholds below are cleared), or when its transcribed
text is a known hallucinated phrase.
"""
from __future__ import annotations

from jarvis.types import WhisperSegment

# Known Whisper hallucination strings, matched case-insensitively as
# substrings of the segment text. Not exhaustive - extend as new
# hallucinations are observed in the wild.
HALLUCINATION_PHRASES: tuple[str, ...] = (
    "thank you for watching",
    "thanks for watching",
    "please subscribe",
    "like and subscribe",
    "see you in the next video",
    "don't forget to subscribe",
)

_NO_SPEECH_PROB_THRESHOLD = 0.6
_AVG_LOGPROB_THRESHOLD = -1.0


def is_hallucination(segment: WhisperSegment) -> bool:
    """True if ``segment`` looks like a Whisper hallucination.

    Triggers when the model is simultaneously unconfident about there being
    speech (``no_speech_prob`` above threshold) and unconfident about its own
    transcription (``avg_logprob`` below threshold), or when the text matches
    a known hallucinated phrase from ``HALLUCINATION_PHRASES``.
    """
    if (
        segment.no_speech_prob > _NO_SPEECH_PROB_THRESHOLD
        and segment.avg_logprob < _AVG_LOGPROB_THRESHOLD
    ):
        return True

    text = segment.text.lower()
    return any(phrase in text for phrase in HALLUCINATION_PHRASES)

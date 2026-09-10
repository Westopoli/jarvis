# spec: specs/cascade-a.md::Acceptance criteria::AC-20
"""Unit tests for ``jarvis/audio/hallucination.py`` (leaf-04, AC 20).

Both numeric thresholds in AC-20 are strict (``no_speech_prob > 0.6`` and
``avg_logprob < -1.0``) and they are ANDed, so the grid below walks each
threshold value itself, one step past it, and the combination that satisfies
only one half.
"""
from __future__ import annotations

import pytest

from jarvis.audio.hallucination import HALLUCINATION_PHRASES, is_hallucination
from jarvis.types import WhisperSegment


@pytest.mark.parametrize(
    "no_speech_prob, avg_logprob, expected",
    [
        (0.70, -1.50, True),    # both thresholds cleared
        (0.60, -1.50, False),   # at the probability threshold: `>` is strict
        (0.61, -1.50, True),    # one step past it
        (0.70, -1.00, False),   # at the logprob threshold: `<` is strict
        (0.70, -1.01, True),    # one step past it
        (0.70, -0.50, False),   # probability alone is not enough (AND, not OR)
        (0.20, -3.00, False),   # logprob alone is not enough
    ],
)
def test_probability_thresholds_are_strict_and_conjunctive(no_speech_prob, avg_logprob, expected):
    segment = WhisperSegment(
        text="some ordinary transcription",
        no_speech_prob=no_speech_prob,
        avg_logprob=avg_logprob,
    )

    assert is_hallucination(segment) is expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Thank you for watching", True),                 # case-insensitive
        ("and thank you for watching this build", True),  # substring, not equality
        ("run the integration tests again", False),
    ],
)
def test_known_phrases_are_matched_case_insensitively_as_substrings(text, expected):
    # Confident, speech-like scores, so only the phrase list can decide.
    segment = WhisperSegment(text=text, no_speech_prob=0.05, avg_logprob=-0.2)

    assert is_hallucination(segment) is expected


def test_every_documented_phrase_is_detected():
    for phrase in HALLUCINATION_PHRASES:
        segment = WhisperSegment(text=phrase, no_speech_prob=0.05, avg_logprob=-0.2)
        assert is_hallucination(segment) is True, phrase


def test_documented_list_contains_the_spec_named_phrase():
    assert "thank you for watching" in [p.lower() for p in HALLUCINATION_PHRASES]


def test_empty_text_with_confident_scores_is_not_a_hallucination():
    segment = WhisperSegment(text="", no_speech_prob=0.05, avg_logprob=-0.2)

    assert is_hallucination(segment) is False

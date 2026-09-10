# spec: specs/cascade-a.md::Acceptance criteria::AC-14..18
"""Unit tests for ``jarvis/reader.py`` (leaf-03, AC 14-18).

The cursor contract is the interesting part: ``next()`` advances, ``resume()``
must hand back the sentence narration was interrupted on rather than the one
after it, and ``done`` flips only once the cursor is past the last sentence.
"""
from __future__ import annotations

from jarvis.reader import Reader


def test_reader_splits_on_terminators_and_keeps_them_attached():
    reader = Reader("First one. Second one! Third one? Fourth has no terminator")

    assert [reader.next() for _ in range(4)] == [
        "First one.",
        "Second one!",
        "Third one?",
        "Fourth has no terminator",
    ]
    assert reader.next() is None
    assert reader.done is True


def test_reader_starts_at_sentence_zero_and_is_not_done():
    reader = Reader("Alpha. Beta.")

    assert reader.done is False
    assert reader.next() == "Alpha."


def test_resume_returns_the_last_returned_sentence_without_advancing():
    reader = Reader("One. Two. Three.")
    reader.next()
    reader.next()

    assert reader.resume() == "Two."
    assert reader.resume() == "Two."
    assert reader.next() == "Three."


def test_resume_before_any_next_returns_the_first_sentence():
    # Spec is silent on resuming before narration started; the cursor sits at
    # sentence 0, so resume hands back sentence 0.
    assert Reader("One. Two.").resume() == "One."


def test_skip_advances_the_cursor_without_returning_a_sentence():
    reader = Reader("One. Two. Three.")

    assert reader.skip() is None
    assert reader.next() == "Two."


def test_empty_text_yields_no_sentences_and_is_immediately_done():
    reader = Reader("")

    assert reader.next() is None
    assert reader.done is True


def test_a_terminator_not_followed_by_whitespace_does_not_split():
    reader = Reader("Dr. Smith paid $3.50 today.")

    assert [reader.next(), reader.next(), reader.next()] == [
        "Dr.",
        "Smith paid $3.50 today.",
        None,
    ]


def test_reader_walks_a_two_hundred_sentence_narration():
    # 200 sentences is the spec's peak narration length.
    reader = Reader(" ".join(f"Sentence {i}." for i in range(200)))
    consumed = 0
    while reader.next() is not None:
        consumed += 1

    assert consumed == 200

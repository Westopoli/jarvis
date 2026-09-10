# spec: specs/cascade-a.md::Acceptance criteria::AC-8..13
"""Unit tests for ``jarvis/conversation.py`` (leaf-02, AC 8-13).

The gate signal (word count, wake word, addressed flag) arrives on the
``TranscriptEvent`` -- this leaf reacts to it, it does not compute it, so the
word minimum is exercised as a constructor knob around its boundary.
"""
from __future__ import annotations

import pytest

from jarvis.conversation import Conversation
from jarvis.types import ConversationState, SendPrompt, TranscriptEvent


def _narrating(min_words: int = 3, tab: int = 5) -> Conversation:
    convo = Conversation(active_tab=tab, min_words=min_words)
    convo.speech_start()
    convo.llm_intent("narrate")
    return convo


def _drafting(tab: int = 7) -> Conversation:
    convo = Conversation(active_tab=tab)
    convo.speech_start()
    convo.llm_intent("draft")
    return convo


# --------------------------------------------------------------------------
# AC-8 / AC-9: entry state and dispatch
# --------------------------------------------------------------------------

def test_conversation_starts_in_idle():
    assert Conversation(active_tab=1).state == ConversationState.IDLE


def test_speech_event_moves_idle_to_listening():
    convo = Conversation(active_tab=1)
    convo.speech_start()

    assert convo.state == ConversationState.LISTENING


@pytest.mark.parametrize(
    "intent, expected",
    [
        ("narrate", ConversationState.NARRATING),
        ("draft", ConversationState.DRAFTING),
        ("smalltalk", ConversationState.SMALLTALK),
    ],
)
def test_llm_intent_dispatches_out_of_listening(intent, expected):
    convo = Conversation(active_tab=1)
    convo.speech_start()
    convo.llm_intent(intent)

    assert convo.state == expected


# --------------------------------------------------------------------------
# AC-10: the NARRATING word/wake gate
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "words, has_wake_word, addressed, expected",
    [
        # below the minimum -- no interruption even with the wake word
        (0, True, True, ConversationState.NARRATING),   # honk / zero words
        (2, True, False, ConversationState.NARRATING),  # min - 1
        # at or above the minimum, but not addressed to Jarvis
        (3, False, False, ConversationState.NARRATING),
        (12, False, False, ConversationState.NARRATING),
        # at the minimum exactly, and addressed -> interrupts
        (3, True, False, ConversationState.LISTENING),
        (3, False, True, ConversationState.LISTENING),
    ],
)
def test_narrating_only_yields_to_a_gated_transcript(words, has_wake_word, addressed, expected):
    convo = _narrating(min_words=3)
    convo.transcript(
        TranscriptEvent(
            text="jarvis stop reading that",
            words=words,
            has_wake_word=has_wake_word,
            addressed=addressed,
        )
    )

    assert convo.state == expected


def test_narration_end_returns_to_idle():
    convo = _narrating()
    convo.narration_end()

    assert convo.state == ConversationState.IDLE


# --------------------------------------------------------------------------
# AC-11 / AC-12: DRAFTING accumulation
# --------------------------------------------------------------------------

def test_drafting_concatenates_fragments_in_arrival_order():
    convo = _drafting(tab=7)
    emitted = [
        convo.transcript(TranscriptEvent(text=fragment, words=2))
        for fragment in ("use pydantic", "instead of", "dataclasses")
    ]

    assert emitted == [None, None, None]

    convo.transcript(TranscriptEvent(text="done", words=1))
    assert convo.state == ConversationState.CONFIRMING

    assert convo.transcript(TranscriptEvent(text="send", words=1)) == SendPrompt(
        tab=7, text="use pydantic instead of dataclasses"
    )


def test_scratch_that_clears_the_accumulator_without_leaving_drafting():
    convo = _drafting(tab=2)
    convo.transcript(TranscriptEvent(text="delete the database", words=3))
    convo.transcript(TranscriptEvent(text="scratch that", words=2))

    assert convo.state == ConversationState.DRAFTING

    convo.transcript(TranscriptEvent(text="add a retry", words=3))
    convo.transcript(TranscriptEvent(text="done", words=1))

    assert convo.transcript(TranscriptEvent(text="send", words=1)) == SendPrompt(
        tab=2, text="add a retry"
    )


def test_confirming_an_empty_draft_sends_empty_text():
    convo = _drafting(tab=4)
    convo.transcript(TranscriptEvent(text="done", words=1))

    assert convo.transcript(TranscriptEvent(text="send", words=1)) == SendPrompt(
        tab=4, text=""
    )


# --------------------------------------------------------------------------
# AC-13: CONFIRMING
# --------------------------------------------------------------------------

def test_send_in_confirming_returns_to_idle():
    convo = _drafting(tab=1)
    convo.transcript(TranscriptEvent(text="ship it", words=2))
    convo.transcript(TranscriptEvent(text="done", words=1))
    convo.transcript(TranscriptEvent(text="send", words=1))

    assert convo.state == ConversationState.IDLE


def test_yes_in_confirming_also_emits_send_prompt():
    convo = _drafting(tab=9)
    convo.transcript(TranscriptEvent(text="ship it", words=2))
    convo.transcript(TranscriptEvent(text="done", words=1))

    assert convo.transcript(TranscriptEvent(text="yes", words=1)) == SendPrompt(
        tab=9, text="ship it"
    )


def test_confirming_reasks_once_then_reverts_to_the_prior_state():
    convo = _drafting(tab=3)
    convo.transcript(TranscriptEvent(text="ship it", words=2))
    convo.transcript(TranscriptEvent(text="done", words=1))

    first_miss = convo.transcript(TranscriptEvent(text="what time is it", words=4))

    assert first_miss is None
    assert convo.state == ConversationState.CONFIRMING

    convo.transcript(TranscriptEvent(text="still not an answer", words=4))

    assert convo.state == ConversationState.DRAFTING


def test_no_in_confirming_is_treated_as_a_miss_that_reasks_then_reverts():
    # spec_lines 28: CONFIRMING names "no" alongside "send"/"yes"/"edit" but
    # gives it no distinct semantics of its own -- treated here as a
    # non-vocabulary miss (same behavior class as the "what time is it" /
    # "still not an answer" miss-path test above), since the spec does not
    # define separate "cancel" behavior for it.
    convo = _drafting(tab=8)
    convo.transcript(TranscriptEvent(text="ship it", words=2))
    convo.transcript(TranscriptEvent(text="done", words=1))

    first_miss = convo.transcript(TranscriptEvent(text="no", words=1))

    assert first_miss is None
    assert convo.state == ConversationState.CONFIRMING

    convo.transcript(TranscriptEvent(text="no", words=1))

    assert convo.state == ConversationState.DRAFTING


def test_send_returns_to_narrating_when_drafting_interrupted_a_narration():
    convo = _narrating(min_words=3, tab=6)
    convo.transcript(
        TranscriptEvent(text="jarvis hold on", words=3, has_wake_word=True)
    )
    convo.llm_intent("draft")
    convo.transcript(TranscriptEvent(text="fix the retry", words=3))
    convo.transcript(TranscriptEvent(text="done", words=1))

    emitted = convo.transcript(TranscriptEvent(text="send", words=1))

    assert emitted == SendPrompt(tab=6, text="fix the retry")
    assert convo.state == ConversationState.NARRATING


# --------------------------------------------------------------------------
# AC-9: SMALLTALK exits
# --------------------------------------------------------------------------

def test_back_to_work_leaves_smalltalk():
    convo = Conversation(active_tab=1)
    convo.speech_start()
    convo.llm_intent("smalltalk")
    convo.transcript(TranscriptEvent(text="back to work", words=3))

    assert convo.state == ConversationState.IDLE


def test_command_intent_leaves_smalltalk():
    convo = Conversation(active_tab=1)
    convo.speech_start()
    convo.llm_intent("smalltalk")
    convo.llm_intent("command")

    assert convo.state == ConversationState.IDLE

# spec: specs/cascade-a.md::Acceptance criteria::AC-8..13
"""``Conversation`` -- the six-state conversation state machine.

See spec_lines 18-33. This module reacts to already-classified signals
(``TranscriptEvent.words`` / ``has_wake_word`` / ``addressed`` for the
NARRATING gate, and ``llm_intent`` for LISTENING dispatch); it does not
compute wake-word detection, VAD, or intent classification itself.
"""
from __future__ import annotations

import re

from jarvis.types import ConversationState, SendPrompt, TranscriptEvent

_CONFIRM_WORDS = ("send", "yes")
_DRAFT_END_WORDS = ("done", "send")
_SCRATCH_PHRASE = "scratch that"
_BACK_TO_WORK_PHRASE = "back to work"

_INTENT_TARGETS = {
    "narrate": ConversationState.NARRATING,
    "draft": ConversationState.DRAFTING,
    "smalltalk": ConversationState.SMALLTALK,
}


def _norm(text: str) -> str:
    """Lowercase and strip punctuation so STT output like "Send." matches."""
    return re.sub(r"[^\w\s]", "", text).strip().lower()


class Conversation:
    """The per-user conversation state machine (spec_lines 18-33)."""

    def __init__(self, active_tab: int, min_words: int = 3) -> None:
        self.active_tab = active_tab
        self.min_words = min_words
        self.state = ConversationState.IDLE

        # DRAFTING accumulator (spec_lines 31-32).
        self._draft_parts: list[str] = []

        # Where a successful CONFIRMING "send"/"yes" should land (spec_lines
        # 28): IDLE normally, or NARRATING if a narration was interrupted to
        # enter DRAFTING.
        self._confirm_return_state = ConversationState.IDLE
        self._narration_was_interrupted = False

        # CONFIRMING's re-ask-once-then-revert bookkeeping (spec_lines 28).
        self._confirm_miss_count = 0
        self._confirm_prior_state = ConversationState.DRAFTING

    # -- entry points --------------------------------------------------

    def speech_start(self) -> None:
        """Speech detected. IDLE -> LISTENING (spec_lines 24)."""
        if self.state is ConversationState.IDLE:
            self.state = ConversationState.LISTENING

    def llm_intent(self, intent: str) -> None:
        """Classified intent for the current turn (spec_lines 25, 29)."""
        if self.state is ConversationState.LISTENING:
            target = _INTENT_TARGETS.get(intent)
            if target is ConversationState.DRAFTING:
                self._confirm_return_state = (
                    ConversationState.NARRATING
                    if self._narration_was_interrupted
                    else ConversationState.IDLE
                )
            if target is not None:
                self.state = target
        elif self.state is ConversationState.SMALLTALK:
            if intent == "command":
                self.state = ConversationState.IDLE

    def narration_end(self) -> None:
        """Narration finished on its own. NARRATING -> IDLE (spec_lines 26)."""
        if self.state is ConversationState.NARRATING:
            self.state = ConversationState.IDLE

    def transcript(self, event: TranscriptEvent) -> SendPrompt | None:
        """A recognized transcript fragment, routed by current state."""
        if self.state is ConversationState.NARRATING:
            self._handle_narrating(event)
        elif self.state is ConversationState.DRAFTING:
            self._handle_drafting(event)
        elif self.state is ConversationState.CONFIRMING:
            return self._handle_confirming(event)
        elif self.state is ConversationState.SMALLTALK:
            self._handle_smalltalk(event)
        return None

    # -- per-state handlers ----------------------------------------------

    def _handle_narrating(self, event: TranscriptEvent) -> None:
        # spec_lines 30: below the word minimum, or lacking a wake word /
        # addressed classification, does not interrupt narration.
        gated_in = event.words >= self.min_words and (
            event.has_wake_word or event.addressed
        )
        if gated_in:
            self._narration_was_interrupted = True
            self.state = ConversationState.LISTENING

    def _handle_drafting(self, event: TranscriptEvent) -> None:
        text = _norm(event.text)
        if text == _SCRATCH_PHRASE:
            # spec_lines 32: clears the accumulator without leaving DRAFTING.
            self._draft_parts = []
            return
        if text in _DRAFT_END_WORDS:
            self._confirm_prior_state = ConversationState.DRAFTING
            self._confirm_miss_count = 0
            self.state = ConversationState.CONFIRMING
            return
        # spec_lines 31: accumulate, whitespace-joined, in arrival order.
        self._draft_parts.append(event.text)

    def _handle_confirming(self, event: TranscriptEvent) -> SendPrompt | None:
        text = _norm(event.text)
        if text in _CONFIRM_WORDS:
            draft = " ".join(self._draft_parts)
            prompt = SendPrompt(tab=self.active_tab, text=draft)
            self._draft_parts = []
            self._confirm_miss_count = 0
            self._narration_was_interrupted = False
            self.state = self._confirm_return_state
            return prompt

        # spec_lines 28: anything else re-asks once, then reverts to the
        # prior state on a second consecutive miss.
        if self._confirm_miss_count == 0:
            self._confirm_miss_count = 1
            return None
        self._confirm_miss_count = 0
        self.state = self._confirm_prior_state
        return None

    def _handle_smalltalk(self, event: TranscriptEvent) -> None:
        if _norm(event.text) == _BACK_TO_WORK_PHRASE:
            self.state = ConversationState.IDLE

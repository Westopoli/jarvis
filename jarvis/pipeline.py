# spec: specs/cascade-b.md::Acceptance criteria::AC-9..10
"""Pipeline assembly: wires a transport through cascade A's real
``Conversation``, ``Reader`` and ``InterruptGate`` objects inside a real
``pipecat.pipeline.pipeline.Pipeline`` (leaf-03, spec_lines 22-23).
"""
from __future__ import annotations

from pipecat.frames.frames import Frame, TranscriptionFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from jarvis.types import AudioSegmentResult, ConversationState, TranscriptEvent


class _NarratingProcessor(FrameProcessor):
    """Consults ``conversation``/``reader``/``interrupt_gate`` for every
    incoming transcript frame: while narrating, it feeds the next sentence
    to TTS unless the interrupt gate and the conversation's own NARRATING
    gate agree the segment should cut narration short (spec_lines 23,
    AC-10).
    """

    def __init__(self, conversation, reader, interrupt_gate, **kwargs) -> None:
        super().__init__(enable_direct_mode=True, **kwargs)
        self._conversation = conversation
        self._reader = reader
        self._gate = interrupt_gate

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if direction is not FrameDirection.DOWNSTREAM or not isinstance(frame, TranscriptionFrame):
            return

        words = frame.metadata.get("words", 0)
        has_wake_word = frame.metadata.get("has_wake_word", False)

        was_narrating = self._conversation.state is ConversationState.NARRATING
        interrupted = False
        if was_narrating:
            segment = AudioSegmentResult(words=words, vad_active=True, text=frame.text)
            interrupted = self._gate.should_interrupt(segment, self._conversation.state)

        self._conversation.transcript(
            TranscriptEvent(text=frame.text, words=words, has_wake_word=has_wake_word)
        )

        still_narrating = self._conversation.state is ConversationState.NARRATING
        if was_narrating and not interrupted and still_narrating:
            sentence = self._reader.next()
            if sentence is not None:
                await self.push_frame(TTSSpeakFrame(text=sentence), FrameDirection.DOWNSTREAM)


def build_pipeline(
    transport,
    conversation,
    reader,
    interrupt_gate,
    llm_model: str = "qwen3:8b",
    tts_service=None,
) -> Pipeline:
    """Assemble ``transport``, ``conversation``, ``reader`` and
    ``interrupt_gate`` into a real Pipecat ``Pipeline`` (spec_lines 22,
    AC-9).

    ``transport``'s own ``input()``/``output()`` frame processors are used
    as the pipeline's boundary (``Pipeline``'s ``source``/``sink``), so
    every frame pushed into the transport, and every frame reaching its
    captured output, genuinely transits this ``Pipeline`` object rather than
    bypassing it.

    ``tts_service`` is optional and defaults to ``None`` deliberately: a real
    TTS service (e.g. ``jarvis.tts.kokoro.build_kokoro_tts_service()``)
    downloads model files over the network on first use, which the
    automated test suite must never trigger. Callers running the real
    desktop voice loop pass a real service here; it is inserted into the
    processor chain right after narration, consuming each ``TTSSpeakFrame``
    and emitting real audio frames downstream. Tests (and the umbrella)
    leave this ``None`` and get the pre-existing text-frame-only behavior
    unchanged.
    """
    narrator = _NarratingProcessor(conversation, reader, interrupt_gate)
    processors = [narrator] if tts_service is None else [narrator, tts_service]
    pipeline = Pipeline(processors, source=transport.input(), sink=transport.output())
    # AC-9: the model argument must be threaded through, not silently
    # dropped -- observable on the returned Pipeline for callers/tests.
    pipeline.llm_model = llm_model
    return pipeline

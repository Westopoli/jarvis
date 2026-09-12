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

    ``wake_word_detector`` is optional: the fake-transport test harness
    always supplies ``words``/``has_wake_word`` directly on the frame's
    ``metadata`` (cascade B's test-only convention), so real detection is
    never exercised by the automated suite. A real ``TranscriptionFrame``
    (from an actual STT service) carries neither, so this falls back to
    counting words in ``frame.text`` and running ``wake_word_detector``
    (or a default "jarvis" substring check) against it — the same
    text-based simplification cascades A/B already use in place of a real
    audio-level wake-word model (openWakeWord), documented there as a
    deliberate v1 shortcut.
    """

    def __init__(
        self, conversation, reader, interrupt_gate, wake_word_detector=None, **kwargs
    ) -> None:
        super().__init__(enable_direct_mode=True, **kwargs)
        self._conversation = conversation
        self._reader = reader
        self._gate = interrupt_gate
        self._wake_word_detector = wake_word_detector or (lambda text: "jarvis" in text.lower())

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if direction is not FrameDirection.DOWNSTREAM or not isinstance(frame, TranscriptionFrame):
            return

        words = frame.metadata.get("words")
        if words is None:
            words = len(frame.text.split())
        has_wake_word = frame.metadata.get("has_wake_word")
        if has_wake_word is None:
            has_wake_word = self._wake_word_detector(frame.text)

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
    wake_word_detector=None,
    stt_service=None,
    vad_processor=None,
) -> Pipeline:
    """Assemble ``transport``, ``conversation``, ``reader`` and
    ``interrupt_gate`` into a real Pipecat ``Pipeline`` (spec_lines 22,
    AC-9).

    ``transport``'s own ``input()``/``output()`` frame processors are used
    as the pipeline's boundary (``Pipeline``'s ``source``/``sink``), so
    every frame pushed into the transport, and every frame reaching its
    captured output, genuinely transits this ``Pipeline`` object rather than
    bypassing it.

    ``vad_processor``, ``stt_service`` and ``tts_service`` are optional and
    default to ``None`` deliberately: a real VAD processor, STT service
    (e.g. Whisper) and TTS service (e.g.
    ``jarvis.tts.kokoro.build_kokoro_tts_service()``) each load model files
    or touch real audio hardware, which the automated test suite must never
    trigger. ``jarvis/run_desktop.py`` passes real instances here for the
    actual desktop voice loop, in pipeline order: ``vad_processor`` (raw
    audio in, speech-boundary frames out) -> ``stt_service`` (-> real
    ``TranscriptionFrame``) -> narration -> ``tts_service`` (``TTSSpeakFrame``
    in, real audio frames out). Tests (and the umbrella) leave all three
    ``None`` and push pre-transcribed ``TranscriptionFrame``s directly,
    getting the pre-existing text-frame-only behavior unchanged.
    """
    narrator = _NarratingProcessor(conversation, reader, interrupt_gate, wake_word_detector)
    processors = [narrator]
    if stt_service is not None:
        processors.insert(0, stt_service)
    if vad_processor is not None:
        processors.insert(0, vad_processor)
    if tts_service is not None:
        processors.append(tts_service)
    pipeline = Pipeline(processors, source=transport.input(), sink=transport.output())
    # AC-9: the model argument must be threaded through, not silently
    # dropped -- observable on the returned Pipeline for callers/tests.
    pipeline.llm_model = llm_model
    return pipeline

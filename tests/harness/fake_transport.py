# spec: specs/cascade-b.md::Acceptance criteria::AC-8
"""Fake Pipecat transport/frame-processor harness (leaf-03, spec_lines 21).

``FakeTransport`` mirrors the shape of a real Pipecat transport
(``pipecat.transports.base_transport.BaseTransport``): ``input()`` returns the
frame processor that stands in for STT output at the front of a pipeline, and
``output()`` returns the frame processor that captures every TTS-bound text
frame reaching the far end of the pipeline. Both are real, importable
``pipecat.processors.frame_processor.FrameProcessor`` instances, wired as the
boundary (``source``/``sink``) of the real ``pipecat.pipeline.pipeline.Pipeline``
``jarvis.pipeline.build_pipeline`` returns -- nothing here bypasses that
``Pipeline`` object.

No real audio hardware is touched: ``push_transcript`` hands the pipeline a
pre-transcribed ``TranscriptionFrame`` directly, standing in for a completed
STT segment on replayed fixture audio.
"""
from __future__ import annotations

import asyncio
import time

from pipecat.frames.frames import Frame, TranscriptionFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.base_transport import BaseTransport


class _FakeTransportInput(FrameProcessor):
    """Pipeline entry point standing in for STT output.

    Runs in Pipecat's "direct mode" (frames are processed synchronously as
    they arrive rather than via the usual queue/task machinery), so a plain,
    non-async test can drive a full frame round-trip without running a real
    pipeline worker.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(enable_direct_mode=True, **kwargs)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class _FakeTransportOutput(FrameProcessor):
    """Pipeline exit point capturing every TTS-bound text frame, in order."""

    def __init__(self, captured: list[str], **kwargs) -> None:
        super().__init__(enable_direct_mode=True, **kwargs)
        self._captured = captured

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction is FrameDirection.DOWNSTREAM and isinstance(frame, TTSSpeakFrame):
            self._captured.append(frame.text)


class FakeTransport(BaseTransport):
    """A fake transport sufficient to drive a real ``Pipeline`` in a test.

    ``push_transcript`` pushes a pre-transcribed transcript frame into the
    pipeline (standing in for STT output); ``emitted_tts_text`` returns every
    TTS-bound text frame the pipeline has emitted so far, in emission order.
    """

    def __init__(self) -> None:
        super().__init__(name="FakeTransport")
        self._captured_tts_text: list[str] = []
        self._input = _FakeTransportInput(name="FakeTransportInput")
        self._output = _FakeTransportOutput(self._captured_tts_text, name="FakeTransportOutput")

    def input(self) -> FrameProcessor:
        """The frame processor standing in for this transport's STT input."""
        return self._input

    def output(self) -> FrameProcessor:
        """The frame processor capturing this transport's TTS output."""
        return self._output

    def push_transcript(self, text: str, *, words: int, has_wake_word: bool = False) -> None:
        """Push a pre-transcribed transcript frame into the pipeline.

        ``words`` and ``has_wake_word`` stand in for the word count and
        wake-word detection a real STT/VAD front end would already have
        computed for this segment.
        """
        frame = TranscriptionFrame(text=text, user_id="fake-user", timestamp=str(time.time()))
        frame.metadata["words"] = words
        frame.metadata["has_wake_word"] = has_wake_word
        asyncio.run(self._input.queue_frame(frame, FrameDirection.DOWNSTREAM))

    def emitted_tts_text(self) -> list[str]:
        """Every TTS-bound text frame the pipeline emitted, in order."""
        return list(self._captured_tts_text)

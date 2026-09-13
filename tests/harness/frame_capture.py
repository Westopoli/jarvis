"""Test-only transport that records every frame reaching the pipeline's end.

Unlike ``fake_transport.FakeTransport`` (direct mode, TTS text only), this
one is meant to run inside a real ``PipelineWorker`` so the user aggregator's
turn strategies (which need tasks and timers) actually execute.
"""
from __future__ import annotations

from pipecat.frames.frames import Frame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.base_transport import BaseTransport


class _Passthrough(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class _Capture(FrameProcessor):
    def __init__(self, captured: list[Frame], **kwargs) -> None:
        super().__init__(**kwargs)
        self._captured = captured

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction is FrameDirection.DOWNSTREAM:
            self._captured.append(frame)
        await self.push_frame(frame, direction)


class CaptureTransport(BaseTransport):
    def __init__(self) -> None:
        super().__init__(name="CaptureTransport")
        self.captured: list[Frame] = []
        self._input = _Passthrough(name="CaptureTransportInput")
        self._output = _Capture(self.captured, name="CaptureTransportOutput")

    def input(self) -> FrameProcessor:
        return self._input

    def output(self) -> FrameProcessor:
        return self._output

    def frames_of(self, frame_type: type) -> list[Frame]:
        return [f for f in self.captured if isinstance(f, frame_type)]

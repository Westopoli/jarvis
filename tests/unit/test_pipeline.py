"""Pipeline assembly + turn gating, run inside a real PipelineWorker.

No STT/LLM/TTS/VAD models are loaded: transcripts are queued as
``TranscriptionFrame``s and the test observes what the user aggregator lets
through to the LLM context. The wake phrase and min-words rules are Pipecat's
own strategies, configured by ``build_pipeline``.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    EndFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection
from pipecat.workers.runner import WorkerRunner

from jarvis.pipeline import TurnConfig, build_pipeline
from jarvis.session import JarvisSession
from tests.harness.frame_capture import CaptureTransport

FAST = TurnConfig(user_turn_stop_timeout_secs=0.3, wake_timeout_secs=5.0, min_words=3)


def _t(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(text=text, user_id="test", timestamp=str(time.time()))


def _user_messages(context) -> list[str]:
    return [m["content"] for m in context.messages if m.get("role") == "user"]


class _Run:
    """Runs the built pipeline in the background for the duration of a test."""

    def __init__(self, config: TurnConfig = FAST) -> None:
        self.transport = CaptureTransport()
        self.session = JarvisSession(active_tab=1)
        self.built = build_pipeline(self.transport, self.session, config=config, vad=False)
        self.worker = PipelineWorker(
            self.built.pipeline,
            params=PipelineParams(),
            idle_timeout_secs=None,
            check_dangling_tasks=False,
        )
        self._runner = WorkerRunner(handle_sigint=False)
        self._ready = asyncio.Event()

        @self.worker.event_handler("on_pipeline_started")
        async def _started(worker, frame):
            self._ready.set()

    async def __aenter__(self):
        self._task = asyncio.create_task(self._runner.run(self.worker))
        await asyncio.wait_for(self._ready.wait(), timeout=10)
        await asyncio.sleep(0.1)
        return self

    async def __aexit__(self, *exc):
        await self.worker.queue_frame(EndFrame())
        try:
            await asyncio.wait_for(self._task, timeout=5)
        except (asyncio.TimeoutError, Exception):
            self._task.cancel()

    async def say(self, text: str, settle: float = 0.6) -> None:
        await self.worker.queue_frame(_t(text))
        await asyncio.sleep(settle)


def test_build_pipeline_wires_session_narrator():
    transport = CaptureTransport()
    session = JarvisSession()
    built = build_pipeline(transport, session, vad=False)
    assert session.narrator is built.narrator
    assert built.context.messages[0]["role"] == "system"
    assert built.context.tools is not None


async def test_wake_phrase_gates_the_llm():
    async with _Run() as run:
        await run.say("what tabs are open")
        assert _user_messages(run.built.context) == []
        await run.say("Jarvis, what tabs are open?")
        assert any("what tabs are open" in m for m in _user_messages(run.built.context))


async def test_awake_window_lets_followups_through_without_wake_phrase():
    async with _Run() as run:
        await run.say("jarvis hello")
        await run.say("switch to tab two")
        msgs = _user_messages(run.built.context)
        assert any("switch to tab two" in m for m in msgs)


async def test_short_utterance_does_not_interrupt_while_bot_speaks():
    async with _Run() as run:
        await run.say("jarvis hello")  # wake
        # Bot starts talking (as the output transport would report).
        await run.transport.output().push_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        await asyncio.sleep(0.1)
        await run.say("honk")  # one word: below min_words while bot speaks
        msgs = _user_messages(run.built.context)
        assert not any("honk" in m for m in msgs)
        await run.say("jarvis stop reading please")  # four words
        msgs = _user_messages(run.built.context)
        assert any("stop reading" in m for m in msgs)


async def test_narrator_output_reaches_transport():
    async with _Run() as run:
        await run.built.narrator.begin("Alpha one. Beta two.")
        await asyncio.sleep(0.2)
        spoken = [f.text for f in run.transport.frames_of(TTSSpeakFrame)]
        assert spoken == ["Alpha one."]

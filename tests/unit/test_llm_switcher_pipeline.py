"""Proves, with a real running Pipecat pipeline (no network, synthetic
stand-in LLMs), the two things the Groq/Ollama failover design depends on
that a static source read can't settle by itself:

1. ``LLMSwitcher`` (a ``ParallelPipeline`` subclass) genuinely works as a
   single drop-in node inside Jarvis's flat linear ``Pipeline([...])`` --
   frames flow through it end to end, and the shared ``LLMContext(tools=...)``
   pattern already used in ``jarvis/pipeline.py`` correctly syncs tool
   handlers onto *both* switcher members, including the inactive one (which
   sits behind a branch filter and never sees the context frame directly).
2. The failover *mechanism* itself: a plain, unforced error from the active
   member does **not** trigger a switch (locking in, executably, the gap
   ``jarvis/llm_providers.py``'s ``_FailoverGroqLLMService`` exists to
   close), while the same error forced permanent does -- and the pipeline
   keeps answering afterwards, on the backup, without needing to be rebuilt.

Uses the real ``LLMSwitcher``/``ServiceSwitcherStrategyFailover`` classes
throughout; only the two member "LLMs" are synthetic.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.llm_switcher import LLMSwitcher
from pipecat.pipeline.service_switcher import ServiceSwitcherStrategyFailover
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService
from pipecat.workers.runner import WorkerRunner

from jarvis.pipeline import TurnConfig, build_pipeline
from jarvis.session import JarvisSession
from tests.harness.frame_capture import CaptureTransport

FAST = TurnConfig(user_turn_stop_timeout_secs=0.3, wake_timeout_secs=5.0, min_words=1)


class _StubLLM(LLMService):
    """A synthetic "LLM": on each LLMContextFrame it either answers with a
    fixed reply or raises a synthetic error, ``fail_first_n`` times."""

    def __init__(self, reply_text: str, *, fail_first_n: int = 0, force_permanent: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self._reply_text = reply_text
        self._fail_first_n = fail_first_n
        self._force_permanent = force_permanent
        self.turns_answered = 0
        self.errors_raised = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        # Forward every frame unconditionally -- including StartFrame/EndFrame,
        # which ParallelPipeline (LLMSwitcher's base) needs to see reach the
        # end of *every* branch, including the inactive one, before it will
        # consider the pipeline started/stopped. A stub that only reacts to
        # LLMContextFrame and swallows everything else deadlocks pipeline
        # startup -- confirmed by hitting exactly that hang before adding
        # this line.
        await self.push_frame(frame, direction)
        if not (isinstance(frame, LLMContextFrame) and direction is FrameDirection.DOWNSTREAM):
            return
        if self._fail_first_n > 0:
            self._fail_first_n -= 1
            self.errors_raised += 1
            await self.push_error(
                f"{self.name} synthetic failure",
                exception=RuntimeError("connection refused"),
                force_treat_as_permanent=self._force_permanent,
            )
            return
        self.turns_answered += 1
        await self.push_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await self.push_frame(LLMTextFrame(text=self._reply_text), FrameDirection.DOWNSTREAM)
        await self.push_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)


def _t(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(text=text, user_id="test", timestamp=str(time.time()))


class _Run:
    """Runs a built pipeline in the background for the duration of a test.
    Mirrors tests/unit/test_pipeline.py's harness (same proven pattern)."""

    def __init__(self, llm) -> None:
        self.transport = CaptureTransport()
        self.session = JarvisSession(active_tab=1)
        self.built = build_pipeline(self.transport, self.session, llm=llm, config=FAST, vad=False)
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


async def test_switcher_composes_as_a_single_node_and_the_happy_path_answers():
    primary = _StubLLM("from primary", name="primary")
    backup = _StubLLM("from backup", name="backup")
    switcher = LLMSwitcher(llms=[primary, backup], strategy_type=ServiceSwitcherStrategyFailover)

    async with _Run(switcher) as run:
        await run.say("hello there jarvis")

        texts = [f.text for f in run.transport.frames_of(LLMTextFrame)]
        assert "from primary" in texts
        assert primary.turns_answered == 1
        assert backup.turns_answered == 0


async def test_switcher_syncs_tool_handlers_to_the_inactive_member_too():
    """The inactive member sits behind a branch filter and never sees the
    LLMContextFrame directly -- LLMSwitcher.process_frame must sync it
    anyway, or its tool handlers would drift out of step across a switch."""
    primary = _StubLLM("from primary", name="primary")
    backup = _StubLLM("from backup", name="backup")
    switcher = LLMSwitcher(llms=[primary, backup], strategy_type=ServiceSwitcherStrategyFailover)

    async with _Run(switcher) as run:
        await run.say("hello there jarvis")

        # jarvis/pipeline.py's build_tools(session) advertises real tools
        # (list_tabs, switch_tab, ...) on the shared LLMContext; both switcher
        # members should have handlers registered for the same set.
        assert primary._functions.keys() == backup._functions.keys()
        assert len(primary._functions) > 0


async def test_a_plain_error_from_the_primary_does_not_fail_over():
    """Locks in the gap this feature's design fixes: an unforced error
    (mirroring stock BaseOpenAILLMService's own error handling) leaves the
    switcher stuck on the now-broken primary."""
    primary = _StubLLM("from primary", name="primary", fail_first_n=1, force_permanent=False)
    backup = _StubLLM("from backup", name="backup")
    switcher = LLMSwitcher(llms=[primary, backup], strategy_type=ServiceSwitcherStrategyFailover)

    async with _Run(switcher) as run:
        await run.say("hello there jarvis")

        assert primary.errors_raised == 1
        assert switcher.active_llm is primary  # stuck: no failover happened
        assert primary.is_usable is True  # a non-permanent error never revokes usability
        texts = [f.text for f in run.transport.frames_of(LLMTextFrame)]
        assert texts == []  # the call got no answer at all


async def test_a_forced_permanent_error_fails_over_and_the_call_keeps_going():
    """The fix: the same scenario with force_treat_as_permanent=True (what
    jarvis's _FailoverGroqLLMService does for every Groq error) fails over,
    and a second utterance in the same still-running pipeline is answered by
    the backup -- proving the call is not dropped, not just that a pointer
    changed."""
    primary = _StubLLM("from primary", name="primary", fail_first_n=1, force_permanent=True)
    backup = _StubLLM("from backup", name="backup")
    switcher = LLMSwitcher(llms=[primary, backup], strategy_type=ServiceSwitcherStrategyFailover)

    async with _Run(switcher) as run:
        await run.say("hello there jarvis")
        assert switcher.active_llm is backup
        assert primary.is_usable is False

        await run.say("are you still there jarvis")
        texts = [f.text for f in run.transport.frames_of(LLMTextFrame)]
        assert texts == ["from backup"]
        assert backup.turns_answered == 1
        assert primary.turns_answered == 0  # never got a chance to answer either turn

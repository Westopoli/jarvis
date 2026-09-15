"""Regression tests for the two bugs found investigating a silent phone call:

1. Every call rebuilt Whisper/Kokoro from scratch (~11-15 s of dead air
   before Jarvis said a word -- long enough that the caller hung up).
   ``get_warm_services`` must build once and reuse the result.
2. The announcer could queue a backlog of old hook events at the same
   moment as, or before, the greeting. ``announce_loop`` must wait for the
   pipeline to be ready plus its own margin before its first poll.

Neither test touches real audio/model code: ``build_services`` is
monkeypatched, and ``announce_loop``/its collaborators are driven with
plain fakes.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis import voice


@pytest.fixture(autouse=True)
def _reset_warm_singleton(monkeypatch):
    """Every test starts from an unwarmed state, regardless of test order."""
    monkeypatch.setattr(voice, "_warm_services", None)
    yield
    monkeypatch.setattr(voice, "_warm_services", None)


class _FakeConfig:
    pass


async def test_get_warm_services_builds_exactly_once_even_when_raced(monkeypatch):
    calls = []

    def fake_build(cfg):
        calls.append(cfg)
        return ("stt", "llm", "tts")

    monkeypatch.setattr(voice, "build_services", fake_build)
    cfg = _FakeConfig()

    results = await asyncio.gather(*[voice.get_warm_services(cfg) for _ in range(8)])

    assert len(calls) == 1, "build_services must run once, not once per caller"
    assert all(r == ("stt", "llm", "tts") for r in results)
    assert all(r is results[0] for r in results), "every caller gets the same instances"


async def test_get_warm_services_reused_across_sequential_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(voice, "build_services", lambda cfg: calls.append(1) or ("s", "l", "t"))
    cfg = _FakeConfig()

    first = await voice.get_warm_services(cfg)
    second = await voice.get_warm_services(cfg)  # simulates a second phone call

    assert len(calls) == 1
    assert first is second


class _FakeWorker:
    def __init__(self) -> None:
        self.queued: list[str] = []

    async def queue_frame(self, frame) -> None:
        self.queued.append(frame)


class _FakeAnnouncer:
    def __init__(self, sentences: list[str]) -> None:
        self._sentences = sentences
        self.poll_calls = 0

    def poll(self) -> list[str]:
        self.poll_calls += 1
        out, self._sentences = self._sentences, []
        return out


async def test_announce_loop_waits_for_ready_before_first_poll():
    worker = _FakeWorker()
    announcer = _FakeAnnouncer(["backlog item"])
    ready = asyncio.Event()

    task = asyncio.create_task(
        voice.announce_loop(worker, announcer, ready=ready, initial_delay=0.05)
    )
    try:
        await asyncio.sleep(0.15)
        assert announcer.poll_calls == 0, "must not poll before the pipeline is ready"
        assert worker.queued == []

        ready.set()
        await asyncio.sleep(0.15)
        assert announcer.poll_calls >= 1
        assert [f.text for f in worker.queued] == ["backlog item"]
    finally:
        task.cancel()


async def test_announce_loop_never_queues_ahead_of_a_greeting_at_the_same_ready_moment():
    """The scenario that produced the flood: ready fires and a backlog
    exists. announce_loop's initial_delay must be long enough that a
    greeting queued right after ``ready`` fires is always first."""
    worker = _FakeWorker()
    announcer = _FakeAnnouncer(["old news"])
    ready = asyncio.Event()

    announce_task = asyncio.create_task(
        voice.announce_loop(worker, announcer, ready=ready, initial_delay=0.1)
    )

    async def greet() -> None:
        await ready.wait()
        await worker.queue_frame("greeting")

    greet_task = asyncio.create_task(greet())
    try:
        ready.set()
        await asyncio.sleep(0.2)
        texts = [f if isinstance(f, str) else f.text for f in worker.queued]
        assert texts[0] == "greeting"
        assert "old news" in texts
        assert texts.index("greeting") < texts.index("old news")
    finally:
        announce_task.cancel()
        greet_task.cancel()

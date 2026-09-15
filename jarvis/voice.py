"""Shared voice-session runner used by the desktop loop and the phone route.

``build_services`` loads whisper, the Ollama LLM and Kokoro. ``run_session``
builds the pipeline on a given transport, runs it to completion, and keeps
the side tasks alive meanwhile (greeting, hook announcements, scripted
transcripts for testing).
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from loguru import logger

from jarvis import cuda_libs

cuda_libs.preload()  # must precede the faster-whisper import below

from pipecat.frames.frames import TranscriptionFrame, TTSSpeakFrame  # noqa: E402
from pipecat.pipeline.worker import PipelineParams, PipelineWorker  # noqa: E402
from pipecat.services.ollama.llm import OLLamaLLMService  # noqa: E402
from pipecat.services.whisper.stt import WhisperSTTService  # noqa: E402
from pipecat.workers.runner import WorkerRunner  # noqa: E402

from jarvis.announcer import Announcer  # noqa: E402
from jarvis.config import Config  # noqa: E402
from jarvis.pipeline import TurnConfig, build_pipeline  # noqa: E402
from jarvis.session import JarvisSession  # noqa: E402
from jarvis.tools import tmux as tmux_tools  # noqa: E402
from jarvis.tts.kokoro import build_kokoro_tts_service  # noqa: E402

WHISPER_MODEL = "Systran/faster-distil-whisper-large-v3"
GREETING = "Jarvis online."
ANNOUNCE_POLL_SECS = 2.0


def build_services(cfg: Config):
    """Construct fresh STT/LLM/TTS service instances.

    Blocking: ``WhisperSTTService.__init__`` and Kokoro's constructor load
    their models synchronously (~10-15 s combined). Call via
    ``get_warm_services`` from async code so this never runs on the event
    loop thread; call this directly only at process start or in a test.
    """
    stt = WhisperSTTService(
        settings=WhisperSTTService.Settings(model=WHISPER_MODEL, no_speech_prob=0.4),
        device="auto",
        compute_type="int8",
    )
    llm = OLLamaLLMService(
        base_url=cfg.ollama_host.rstrip("/") + "/v1",
        settings=OLLamaLLMService.Settings(
            model=cfg.ollama_model,
            temperature=0.2,
            # Ollama's OpenAI-compatible endpoint ignores `think`; it honours
            # reasoning_effort. Without this qwen3 burns 100-170 hidden
            # reasoning tokens per call (~2.5 s on the 3060) before answering.
            extra={"extra_body": {"reasoning_effort": "none"}},
        ),
    )
    tts = build_kokoro_tts_service()
    return stt, llm, tts


_warm_services: tuple | None = None
_warm_lock = asyncio.Lock()


async def get_warm_services(cfg: Config):
    """Build STT/LLM/TTS once and reuse them for every session after.

    Both ``WhisperSTTService`` and ``KokoroTTSService`` load their models in
    ``__init__``, not in Pipecat's per-run ``setup()`` hook, so a single
    instance is safe to re-link into a fresh ``Pipeline`` on every call
    (``FrameProcessor.link`` just overwrites its neighbour pointers; nothing
    guards against relinking). Without this, every phone call paid the ~11 s
    Whisper-plus-Kokoro load cost before saying a word — long enough that
    the caller hangs up before hearing anything (observed: a 16 s call where
    the greeting wasn't queued until second 15).

    Built inside ``run_in_executor`` so the blocking load never stalls the
    event loop — important the first time this is called from inside a live
    call's WebSocket handler, if the server-startup warmup hasn't finished
    yet.
    """
    global _warm_services
    async with _warm_lock:
        if _warm_services is None:
            loop = asyncio.get_event_loop()
            _warm_services = await loop.run_in_executor(None, build_services, cfg)
    return _warm_services


async def announce_loop(
    worker: PipelineWorker, announcer: Announcer, *, ready: asyncio.Event, initial_delay: float
) -> None:
    """Poll ``announcer`` and speak whatever it returns.

    Waits for ``ready`` (the pipeline can actually accept frames) plus
    ``initial_delay`` before its first poll, so a backlog of old
    announcements can never be queued ahead of or on top of the greeting.
    """
    await ready.wait()
    await asyncio.sleep(initial_delay)
    while True:
        try:
            for sentence in announcer.poll():
                logger.info(f"announce: {sentence}")
                await worker.queue_frame(TTSSpeakFrame(text=sentence))
        except Exception as exc:  # tmux gone, etc. Keep the loop alive.
            logger.warning(f"announcer: {exc}")
        await asyncio.sleep(ANNOUNCE_POLL_SECS)


async def scripted_input(worker: PipelineWorker, lines: list[str], gap_secs: float = 15.0) -> None:
    await asyncio.sleep(5.0)  # after the greeting: half-duplex drops transcripts while it plays
    for line in lines:
        logger.info(f"scripted transcript: {line!r}")
        await worker.queue_frame(
            TranscriptionFrame(text=line, user_id="script", timestamp=str(time.time()))
        )
        await asyncio.sleep(gap_secs)


async def run_session(
    transport,
    session: JarvisSession,
    cfg: Config,
    *,
    turn_config: TurnConfig,
    audio_in_sample_rate: int = 16000,
    audio_out_sample_rate: int = 24000,
    greeting: str | None = GREETING,
    announce: bool = True,
    extra_tasks: list[Callable[[PipelineWorker], Awaitable[None]]] | None = None,
    handle_sigint: bool = True,
) -> None:
    """Run one voice session on ``transport`` until it ends."""
    if cfg.jarvis_rename_windows:
        try:
            for index, old, new in tmux_tools.unique_window_names(session.tmux_session):
                logger.info(f"tmux window {index}: {old!r} -> {new!r}")
        except Exception as exc:
            logger.warning(f"could not rename tmux windows: {exc}")
    stt, llm, tts = await get_warm_services(cfg)
    built = build_pipeline(transport, session, llm=llm, stt=stt, tts=tts, config=turn_config)
    worker = PipelineWorker(
        built.pipeline,
        params=PipelineParams(
            audio_in_sample_rate=audio_in_sample_rate,
            audio_out_sample_rate=audio_out_sample_rate,
        ),
        idle_timeout_secs=None,
    )

    # Both greet() and announce_loop() gate on the pipeline actually being
    # able to accept frames (StartFrame reaching the end of the pipeline),
    # not on wall-clock time since run_session() was entered -- with warm
    # services this is now near-instant, but gating on the real signal keeps
    # it correct even if a cold build ever happens again. announce_loop's
    # extra margin keeps a backlog of old announcements from ever queuing
    # ahead of or on top of the greeting.
    pipeline_ready = asyncio.Event()

    @worker.event_handler("on_pipeline_started")
    async def _on_pipeline_started(worker, frame) -> None:
        pipeline_ready.set()

    background: list[asyncio.Task] = []
    if announce:
        background.append(
            asyncio.create_task(
                announce_loop(worker, Announcer(session), ready=pipeline_ready, initial_delay=2.5)
            )
        )
    for factory in extra_tasks or []:
        background.append(asyncio.create_task(factory(worker)))
    if greeting:

        async def greet() -> None:
            await pipeline_ready.wait()
            await asyncio.sleep(0.5)
            await worker.queue_frame(TTSSpeakFrame(text=greeting))

        background.append(asyncio.create_task(greet()))

    try:
        await WorkerRunner(handle_sigint=handle_sigint).run(worker)
    finally:
        for task in background:
            task.cancel()

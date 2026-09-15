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
from jarvis.tts.kokoro import build_kokoro_tts_service  # noqa: E402

WHISPER_MODEL = "Systran/faster-distil-whisper-large-v3"
GREETING = "Jarvis online."
ANNOUNCE_POLL_SECS = 2.0


def build_services(cfg: Config):
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


async def announce_loop(worker: PipelineWorker, announcer: Announcer) -> None:
    while True:
        await asyncio.sleep(ANNOUNCE_POLL_SECS)
        try:
            for sentence in announcer.poll():
                logger.info(f"announce: {sentence}")
                await worker.queue_frame(TTSSpeakFrame(text=sentence))
        except Exception as exc:  # tmux gone, etc. Keep the loop alive.
            logger.warning(f"announcer: {exc}")


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
    stt, llm, tts = build_services(cfg)
    built = build_pipeline(transport, session, llm=llm, stt=stt, tts=tts, config=turn_config)
    worker = PipelineWorker(
        built.pipeline,
        params=PipelineParams(
            audio_in_sample_rate=audio_in_sample_rate,
            audio_out_sample_rate=audio_out_sample_rate,
        ),
        idle_timeout_secs=None,
    )

    background: list[asyncio.Task] = []
    if announce:
        background.append(asyncio.create_task(announce_loop(worker, Announcer(session))))
    for factory in extra_tasks or []:
        background.append(asyncio.create_task(factory(worker)))
    if greeting:

        async def greet() -> None:
            await asyncio.sleep(1.0)
            await worker.queue_frame(TTSSpeakFrame(text=greeting))

        background.append(asyncio.create_task(greet()))

    try:
        await WorkerRunner(handle_sigint=handle_sigint).run(worker)
    finally:
        for task in background:
            task.cancel()

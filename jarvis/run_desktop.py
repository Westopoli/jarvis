"""Desktop voice loop: mic + speaker on this machine, everything else real.

    uv sync --extra audio --extra audio-local --extra server
    uv run python -m jarvis.run_desktop

Say "Jarvis" to wake it, then talk: "what tabs are open", "switch to tab
four", "read it", "update me", "tell it to use pydantic", "send".

Options:
  --say "jarvis, what tabs are open"   inject that transcript ~3 s after start
                                       (repeatable) to exercise the loop
                                       without a microphone
  --no-server                          don't host the hook ingest server
                                       in-process (no proactive announcements)
  --full-duplex                        keep listening while Jarvis speaks.
                                       Headphones only: on speakers he hears
                                       himself and cuts himself off. Default
                                       is half duplex (mic muted while he
                                       talks), which means no barge-in.

The hook ingest server (``/events``) runs in-process on ``JARVIS_PORT`` so
Claude Code's Stop/Notification hooks can wake Jarvis: "Tab two has
finished. Want me to read it?"
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time

from loguru import logger

from jarvis import cuda_libs

cuda_libs.preload()  # must precede the faster-whisper import below

from pipecat.frames.frames import TranscriptionFrame, TTSSpeakFrame  # noqa: E402
from pipecat.pipeline.worker import PipelineParams, PipelineWorker  # noqa: E402
from pipecat.services.ollama.llm import OLLamaLLMService  # noqa: E402
from pipecat.services.whisper.stt import WhisperSTTService  # noqa: E402
from pipecat.transports.local.audio import (  # noqa: E402
    LocalAudioTransport,
    LocalAudioTransportParams,
)
from pipecat.workers.runner import WorkerRunner  # noqa: E402

from jarvis.announcer import Announcer  # noqa: E402
from jarvis.config import load_config  # noqa: E402
from jarvis.pipeline import TurnConfig, build_pipeline  # noqa: E402
from jarvis.server import HOST, create_app  # noqa: E402
from jarvis.session import JarvisSession  # noqa: E402
from jarvis.tts.kokoro import build_kokoro_tts_service  # noqa: E402

WHISPER_MODEL = "Systran/faster-distil-whisper-large-v3"
GREETING = "Jarvis online."
ANNOUNCE_POLL_SECS = 2.0


def build_services(cfg):
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


async def _serve_hooks(app, port: int) -> None:
    import uvicorn

    config = uvicorn.Config(app, host=HOST, port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


async def _announce_loop(worker: PipelineWorker, announcer: Announcer) -> None:
    while True:
        await asyncio.sleep(ANNOUNCE_POLL_SECS)
        try:
            for sentence in announcer.poll():
                logger.info(f"announce: {sentence}")
                await worker.queue_frame(TTSSpeakFrame(text=sentence))
        except Exception as exc:  # tmux gone, etc. Keep the loop alive.
            logger.warning(f"announcer: {exc}")


async def _scripted_input(worker: PipelineWorker, lines: list[str]) -> None:
    await asyncio.sleep(3.0)
    for line in lines:
        logger.info(f"scripted transcript: {line!r}")
        await worker.queue_frame(
            TranscriptionFrame(text=line, user_id="script", timestamp=str(time.time()))
        )
        await asyncio.sleep(15.0)


async def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--say", action="append", default=[], help="inject a transcript")
    parser.add_argument("--no-server", action="store_true")
    parser.add_argument("--wake-timeout", type=float, default=20.0)
    parser.add_argument("--min-words", type=int, default=3)
    parser.add_argument(
        "--full-duplex",
        action="store_true",
        help="keep the mic open while Jarvis speaks (headphones only; on "
        "speakers he hears himself and interrupts himself)",
    )
    args = parser.parse_args(argv)

    cfg = load_config()
    session = JarvisSession(tmux_session=cfg.jarvis_tmux_session)
    stt, llm, tts = build_services(cfg)

    transport = LocalAudioTransport(
        LocalAudioTransportParams(audio_in_enabled=True, audio_out_enabled=True)
    )
    built = build_pipeline(
        transport,
        session,
        llm=llm,
        stt=stt,
        tts=tts,
        config=TurnConfig(
            wake_timeout_secs=args.wake_timeout,
            min_words=args.min_words,
            half_duplex=not args.full_duplex,
        ),
    )

    worker = PipelineWorker(
        built.pipeline,
        params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=24000),
        idle_timeout_secs=None,
    )

    background: list[asyncio.Task] = []
    if not args.no_server:
        app = create_app(session.event_store)
        background.append(asyncio.create_task(_serve_hooks(app, cfg.jarvis_port)))
        background.append(asyncio.create_task(_announce_loop(worker, Announcer(session))))
    if args.say:
        background.append(asyncio.create_task(_scripted_input(worker, args.say)))

    async def greet() -> None:
        await asyncio.sleep(1.0)
        await worker.queue_frame(TTSSpeakFrame(text=GREETING))

    background.append(asyncio.create_task(greet()))

    try:
        await WorkerRunner().run(worker)
    finally:
        for task in background:
            task.cancel()


if __name__ == "__main__":
    os.environ.setdefault("OLLAMA_KEEP_ALIVE", "-1")
    asyncio.run(main())

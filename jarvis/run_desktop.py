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

from jarvis import voice  # loads CUDA libs before faster-whisper
from pipecat.transports.local.audio import (  # noqa: E402
    LocalAudioTransport,
    LocalAudioTransportParams,
)

from jarvis.config import load_config
from jarvis.pipeline import TurnConfig
from jarvis.server import HOST, create_app
from jarvis.session import JarvisSession


async def _serve_hooks(app, port: int) -> None:
    import uvicorn

    config = uvicorn.Config(app, host=HOST, port=port, log_level="warning")
    await uvicorn.Server(config).serve()


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
    transport = LocalAudioTransport(
        LocalAudioTransportParams(audio_in_enabled=True, audio_out_enabled=True)
    )

    server_task = None
    if not args.no_server:
        server_task = asyncio.create_task(
            _serve_hooks(create_app(session.event_store), cfg.jarvis_port)
        )

    extra = []
    if args.say:
        lines = list(args.say)
        extra.append(lambda worker: voice.scripted_input(worker, lines))

    try:
        await voice.run_session(
            transport,
            session,
            cfg,
            turn_config=TurnConfig(
                wake_timeout_secs=args.wake_timeout,
                min_words=args.min_words,
                half_duplex=not args.full_duplex,
            ),
            announce=not args.no_server,
            extra_tasks=extra,
        )
    finally:
        if server_task:
            server_task.cancel()


if __name__ == "__main__":
    os.environ.setdefault("OLLAMA_KEEP_ALIVE", "-1")
    asyncio.run(main())

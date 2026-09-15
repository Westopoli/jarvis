"""HTTP daemon: Claude Code hook ingest, health check, Telnyx phone calls.

Bound to loopback only. The hook scripts POST to ``/events`` from the same
machine. Only ``/texml``, ``/ws/telnyx`` and ``/health`` are meant to be
published through Tailscale Funnel (path rules, see deploy/); ``/events``
must never be routed publicly.

Phone call flow (Telnyx):

1. You dial the Telnyx number. Telnyx POSTs to ``/texml``; we answer with
   TeXML that tells it to open a bidirectional media stream to
   ``wss://<JARVIS_PUBLIC_HOSTNAME>/ws/telnyx``.
2. Telnyx connects the WebSocket and sends ``connected`` then ``start``.
   The ``start`` event carries the caller number; anything not in
   ``TELNYX_ALLOWED_CALLER`` is closed immediately.
3. A Pipecat pipeline runs over the socket for the duration of the call
   (full duplex: the phone does echo cancellation, so barge-in works).
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response
from loguru import logger
from starlette.websockets import WebSocketDisconnect

from jarvis.config import load_config
from jarvis.events import EventStore
from jarvis.transports.telnyx import build_telnyx_serializer, is_allowed_caller

HOST = "127.0.0.1"
MAX_HANDSHAKE_MESSAGES = 3


def texml_for(hostname: str) -> str:
    """TeXML that answers the call and streams both ways to our WebSocket."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        "  <Connect>\n"
        f'    <Stream url="wss://{hostname}/ws/telnyx" '
        'bidirectionalMode="rtp" codec="PCMU" bidirectionalCodec="PCMU" />\n'
        "  </Connect>\n"
        "</Response>\n"
    )


async def run_call(websocket: WebSocket, serializer, store: EventStore) -> None:
    """Run a full voice session over a Telnyx media-stream WebSocket."""
    from pipecat.transports.websocket.fastapi import (
        FastAPIWebsocketParams,
        FastAPIWebsocketTransport,
    )

    from jarvis import voice
    from jarvis.pipeline import TurnConfig
    from jarvis.session import JarvisSession

    cfg = load_config()
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
        ),
    )
    session = JarvisSession(tmux_session=cfg.jarvis_tmux_session, event_store=store)
    await voice.run_session(
        transport,
        session,
        cfg,
        # Phone: the handset cancels its own echo, so keep the mic open.
        turn_config=TurnConfig(half_duplex=False),
        audio_in_sample_rate=16000,  # whisper's native rate; serializer resamples 8k -> 16k
        audio_out_sample_rate=8000,  # one resample (Kokoro 24k -> 8k) before mu-law
        handle_sigint=False,
    )


def create_app(store: EventStore, *, call_runner=None) -> FastAPI:
    app = FastAPI()
    app.state.store = store
    runner = call_runner or run_call

    @app.post("/events")
    async def post_events(request: Request) -> Response:
        payload = await request.json()
        store.record(payload)
        return Response(status_code=200, content=b"")

    @app.get("/health")
    async def get_health() -> dict:
        return {"status": "ok"}

    @app.post("/texml")
    async def post_texml() -> Response:
        hostname = os.environ.get("JARVIS_PUBLIC_HOSTNAME", "")
        if not hostname:
            return Response(status_code=503, content=b"JARVIS_PUBLIC_HOSTNAME not set")
        return Response(content=texml_for(hostname), media_type="application/xml")

    @app.websocket("/ws/telnyx")
    async def ws_telnyx(websocket: WebSocket) -> None:
        await websocket.accept()

        # Telnyx sends {"event": "connected"} then {"event": "start", ...}.
        start = None
        for _ in range(MAX_HANDSHAKE_MESSAGES):
            try:
                payload = await websocket.receive_json()
            except (WebSocketDisconnect, ValueError):
                return
            if payload.get("event") == "start" or "start" in payload:
                start = payload
                break
        if start is None:
            await websocket.close()
            return

        info = start.get("start") or {}
        from_number = info.get("from", "")
        stream_id = info.get("stream_id") or start.get("stream_id")
        call_control_id = info.get("call_control_id")

        if not is_allowed_caller(from_number):
            logger.warning(f"rejected call from ...{from_number[-4:] if from_number else '?'}")
            await websocket.close()
            return

        outbound = (info.get("media_format") or {}).get("encoding") or "PCMU"
        serializer = build_telnyx_serializer(
            stream_id, call_control_id=call_control_id, outbound_encoding=outbound
        )
        logger.info(f"call accepted from ...{from_number[-4:]}, stream {stream_id}")
        await runner(websocket, serializer, store)

    return app


store = EventStore()
app = create_app(store)


def main() -> None:
    import uvicorn

    port = int(os.environ.get("JARVIS_PORT", "8000"))
    uvicorn.run(app, host=HOST, port=port)


if __name__ == "__main__":
    main()

"""HTTP daemon: Claude Code hook ingest, health check, Telnyx media stream.

Bound to loopback only. The hook scripts POST to ``/events`` from the same
machine. Anything that must be reachable from the internet (the Telnyx
WebSocket, ``/health``) is exposed through Tailscale Funnel/serve with a
path rule; ``/events`` must never be routed publicly.

``create_app(store)`` builds an app around a given ``EventStore`` so the
desktop runner can host the same routes in-process and announce events.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response
from starlette.websockets import WebSocketDisconnect

from jarvis.events import EventStore
from jarvis.transports.telnyx import build_telnyx_serializer, is_allowed_caller

HOST = "127.0.0.1"


def create_app(store: EventStore) -> FastAPI:
    app = FastAPI()
    app.state.store = store

    @app.post("/events")
    async def post_events(request: Request) -> Response:
        payload = await request.json()
        store.record(payload)
        return Response(status_code=200, content=b"")

    @app.get("/health")
    async def get_health() -> dict:
        return {"status": "ok"}

    @app.websocket("/ws/telnyx")
    async def ws_telnyx(websocket: WebSocket) -> None:
        # Handshake + caller allow-list only. Running a voice pipeline over
        # this socket (FastAPIWebsocketTransport) is not wired yet; the route
        # closes after validating the caller. See plan slice 7.
        await websocket.accept()
        try:
            payload = await websocket.receive_json()
        except (WebSocketDisconnect, ValueError):
            return

        start = payload.get("start") or {}
        from_number = start.get("from", "")
        stream_id = start.get("stream_id") or payload.get("stream_id")
        call_control_id = start.get("call_control_id")

        if not is_allowed_caller(from_number):
            await websocket.close()
            return

        build_telnyx_serializer(stream_id, call_control_id=call_control_id)

    return app


store = EventStore()
app = create_app(store)


def main() -> None:
    import uvicorn

    port = int(os.environ.get("JARVIS_PORT", "8000"))
    uvicorn.run(app, host=HOST, port=port)


if __name__ == "__main__":
    main()

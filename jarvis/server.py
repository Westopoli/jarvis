# spec: specs/cascade-a.md::Acceptance criteria::AC-31
# spec: specs/cascade-c.md::Acceptance criteria::AC-3,AC-4
"""HTTP daemon that receives Claude Code hook events (leaf-06, spec_lines 59)
and, from leaf-01 (spec_lines 12-13), the Telnyx media-stream WebSocket route
and a health-check route.

Bound to loopback only: the hook scripts POST here from the same machine and
nothing else should ever be able to reach it.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response
from starlette.websockets import WebSocketDisconnect

from jarvis.events import EventStore
from jarvis.transports.telnyx import build_telnyx_serializer, is_allowed_caller

HOST = "127.0.0.1"

app = FastAPI()
store = EventStore()


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


def main() -> None:
    import uvicorn

    port = int(os.environ.get("JARVIS_PORT", "8765"))
    uvicorn.run(app, host=HOST, port=port)


if __name__ == "__main__":
    main()

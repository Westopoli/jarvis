# spec: specs/cascade-a.md::Acceptance criteria::AC-31
"""HTTP daemon that receives Claude Code hook events (leaf-06, spec_lines 59).

Bound to loopback only: the hook scripts POST here from the same machine and
nothing else should ever be able to reach it.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import Response

from jarvis.events import EventStore

HOST = "127.0.0.1"

app = FastAPI()
store = EventStore()


@app.post("/events")
async def post_events(request: Request) -> Response:
    payload = await request.json()
    store.record(payload)
    return Response(status_code=200, content=b"")


def main() -> None:
    import uvicorn

    port = int(os.environ.get("JARVIS_PORT", "8765"))
    uvicorn.run(app, host=HOST, port=port)


if __name__ == "__main__":
    main()

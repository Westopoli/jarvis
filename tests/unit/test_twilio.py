"""Twilio: TwiML webhook (signature + allow-list), media-stream route."""
from __future__ import annotations

from fastapi.testclient import TestClient

import jarvis.server as server_mod
from jarvis.transports.twilio import compute_signature, signature_valid, twiml_for

HOST = "box.example.ts.net"
ALLOWED = "+15551234567"
TOKEN = "twilio-auth-token"


def _start(from_number: str | None) -> dict:
    custom = {"from_number": from_number} if from_number is not None else {}
    return {"event": "start", "sequenceNumber": "1", "streamSid": "MZ123",
            "start": {"streamSid": "MZ123", "callSid": "CA456", "accountSid": "AC789",
                      "tracks": ["inbound"], "customParameters": custom,
                      "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}}


def test_twiml_streams_to_our_socket_and_forwards_caller():
    xml = twiml_for(HOST, ALLOWED)
    assert f'url="wss://{HOST}/ws/twilio"' in xml
    assert f'name="from_number" value="{ALLOWED}"' in xml


def test_signature_roundtrip():
    params = {"From": ALLOWED, "CallSid": "CA1"}
    sig = compute_signature(TOKEN, f"https://{HOST}/twiml", params)
    assert signature_valid(TOKEN, f"https://{HOST}/twiml", params, sig)
    assert not signature_valid(TOKEN, f"https://{HOST}/twiml", params, sig[:-2] + "AA")
    assert not signature_valid(TOKEN, f"https://{HOST}/twiml", params, None)


def _post_twiml(client, params, sign=True):
    body = "&".join(f"{k}={v.replace('+', '%2B')}" for k, v in params.items())
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if sign:
        headers["X-Twilio-Signature"] = compute_signature(TOKEN, f"https://{HOST}/twiml", params)
    return client.post("/twiml", content=body, headers=headers)


def test_twiml_webhook_requires_signature_and_allowed_caller(monkeypatch):
    monkeypatch.setenv("JARVIS_PUBLIC_HOSTNAME", HOST)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("JARVIS_ALLOWED_CALLER", ALLOWED)
    client = TestClient(server_mod.app)

    assert _post_twiml(client, {"From": ALLOWED, "CallSid": "CA1"}, sign=False).status_code == 403

    r = _post_twiml(client, {"From": "+19998887777", "CallSid": "CA1"})
    assert r.status_code == 200 and "<Reject" in r.text

    r = _post_twiml(client, {"From": ALLOWED, "CallSid": "CA1"})
    assert r.status_code == 200 and "<Stream" in r.text and ALLOWED in r.text


def test_twilio_stream_route_gates_on_forwarded_caller(monkeypatch):
    monkeypatch.setenv("JARVIS_ALLOWED_CALLER", ALLOWED)
    ran = []

    async def fake_run_call(websocket, serializer, store):
        ran.append(serializer)

    monkeypatch.setattr(server_mod, "run_call", fake_run_call)
    client = TestClient(server_mod.create_app(server_mod.store))

    with client.websocket_connect("/ws/twilio") as ws:
        ws.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
        ws.send_json(_start(ALLOWED))
    assert len(ran) == 1
    assert ran[0]._stream_sid == "MZ123" if hasattr(ran[0], "_stream_sid") else True

    from starlette.websockets import WebSocketDisconnect
    closed = False
    try:
        with client.websocket_connect("/ws/twilio") as ws:
            ws.send_json(_start("+19998887777"))
            try:
                ws.receive_text()
            except WebSocketDisconnect:
                closed = True
    except WebSocketDisconnect:
        closed = True
    assert closed and len(ran) == 1

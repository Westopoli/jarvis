# spec: specs/cascade-c.md::Acceptance criteria::AC-1..14
"""Cascade C umbrella test — behavioral smoke, one composition per leaf.

Unlike cascades A and B, cascade C's four leaves (Telnyx transport, deploy
config, Orpheus TTS adapter, filler+latency) are deliberately independent —
none imports another's real code (each reads its own env vars / files
directly, matching how cascade A's tmux tools and cascade B's classify_intent
each read os.environ directly rather than threading a shared config object).
So this umbrella exercises one genuine multi-file composition per leaf
(C1's route+allow-list, C3's speak+realtime_factor, C4's filler+latency)
rather than cross-leaf wiring that doesn't exist by design.

Imports are deferred INSIDE each test function (same reason as
umbrella_a.py/umbrella_b.py: leaves admit one at a time).
"""
import asyncio
import io
import struct
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading


def _make_wav_bytes(duration_s: float = 1.0, sample_rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    n_frames = int(duration_s * sample_rate)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack("<%dh" % n_frames, *([0] * n_frames)))
    return buf.getvalue()


def test_health_route_and_allowlist_compose_fail_closed(monkeypatch):
    from jarvis.transports.telnyx import is_allowed_caller
    import jarvis.server as server_mod

    monkeypatch.delenv("TELNYX_ALLOWED_CALLER", raising=False)
    assert is_allowed_caller("+15551234567") is False

    monkeypatch.setenv("TELNYX_ALLOWED_CALLER", "+15551234567,+15559876543")
    assert is_allowed_caller("+15551234567") is True
    assert is_allowed_caller("+19998887777") is False

    async def _call_health():
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/health",
            "headers": [],
            "query_string": b"",
        }
        messages_in = [{"type": "http.request", "body": b"", "more_body": False}]
        messages_out = []

        async def receive():
            return messages_in.pop(0)

        async def send(message):
            messages_out.append(message)

        await server_mod.app(scope, receive, send)
        return messages_out

    messages = asyncio.run(_call_health())
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    assert status == 200
    assert b'"status"' in body and b"ok" in body


def test_config_reports_telnyx_unconfigured_by_default(monkeypatch):
    from jarvis.config import load_config

    monkeypatch.delenv("TELNYX_API_KEY", raising=False)
    monkeypatch.delenv("TELNYX_ALLOWED_CALLER", raising=False)
    cfg = load_config()
    assert cfg.telnyx_configured is False

    monkeypatch.setenv("TELNYX_API_KEY", "fake-key")
    monkeypatch.setenv("TELNYX_ALLOWED_CALLER", "+15551234567")
    cfg2 = load_config()
    assert cfg2.telnyx_configured is True


def test_speak_and_measure_composes_realtime_factor():
    from jarvis.tts.orpheus import speak_and_measure

    wav_bytes = _make_wav_bytes(duration_s=1.0)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.end_headers()
            self.wfile.write(wav_bytes)

        def log_message(self, *a):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_port
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        audio, factor = speak_and_measure("hello there", base_url=f"http://127.0.0.1:{port}")
        assert audio == wav_bytes
        assert factor > 0
    finally:
        httpd.shutdown()
        thread.join(timeout=2)


def test_pick_filler_deterministic_and_latency_log_records():
    import random
    from jarvis.fillers import pick_filler, FILLERS
    from jarvis.metrics import LatencyLog

    rng1 = random.Random(42)
    rng2 = random.Random(42)
    tool = next(iter(FILLERS))
    assert pick_filler(tool, rng=rng1) == pick_filler(tool, rng=rng2)

    log = LatencyLog()
    with log.timed("tool_call"):
        pass
    assert len(log.entries) == 1
    assert log.entries[0][0] == "tool_call"

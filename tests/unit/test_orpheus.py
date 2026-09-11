# spec: specs/cascade-c.md::Acceptance criteria::AC-9..11
"""Tests for the Orpheus TTS HTTP adapter (leaf-03, AC 9-11):
``jarvis/tts/orpheus.py``.

Three deliberate choices:

* The llama.cpp/Orpheus server is a throwaway loopback ``http.server`` on an
  ephemeral port — the same pattern cascade A uses for the hook listener and
  cascade B uses for Ollama.  Nothing inside ``speak`` is mocked, so the JSON
  body it actually puts on the wire is what gets asserted (spec L33 forbids a
  real Orpheus process here).
* The WAV bodies the fake server returns are built by the stdlib ``wave``
  module at a *known* duration and a *non-default* sample rate, so an
  implementation that hardcodes 8000 Hz instead of reading the header cannot
  pass.
* ``speak_and_measure``'s composition is proved by spying on the module-global
  ``realtime_factor`` (AC-10 pins that call) and asserting the arguments it
  receives.  Wall-clock is asserted only as ``> 0`` — leaves spawn in
  parallel, so any timing threshold would flake both ways.
"""
from __future__ import annotations

import inspect
import io
import json
import struct
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from jarvis.tts import orpheus as orpheus_mod
from jarvis.tts.orpheus import realtime_factor, speak, speak_and_measure


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _make_wav(duration_s: float, sample_rate: int = 8000) -> bytes:
    """A real, parseable mono 16-bit WAV of exactly ``duration_s`` seconds."""
    frames = int(round(duration_s * sample_rate))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(struct.pack("<%dh" % frames, *([0] * frames)))
    return buffer.getvalue()


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):  # a client that timed out
        pass


@pytest.fixture
def orpheus():
    """A loopback stand-in for the llama.cpp OpenAI-compatible TTS server."""
    state = {"wav": _make_wav(1.0), "delay": 0.0}
    received: list[SimpleNamespace] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            received.append(SimpleNamespace(path=self.path, body=raw))
            if state["delay"]:
                time.sleep(state["delay"])
            body = state["wav"]
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence stderr noise
            pass

    server = _QuietServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield SimpleNamespace(
            base_url=f"http://127.0.0.1:{server.server_address[1]}",
            received=received,
            state=state,
        )
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------
# AC-9: speak's request shape
# --------------------------------------------------------------------------

def test_speak_posts_the_openai_tts_request_and_returns_the_raw_body(orpheus):
    audio = speak("all green on the deploy", base_url=orpheus.base_url)

    assert audio == orpheus.state["wav"]
    # External call budget (spec L43): exactly one HTTP call, no retry loop.
    assert len(orpheus.received) == 1
    assert orpheus.received[0].path == "/v1/audio/speech"
    payload = json.loads(orpheus.received[0].body)
    assert payload["input"] == "all green on the deploy"
    assert payload["response_format"] == "wav"
    assert isinstance(payload.get("voice"), str) and payload["voice"].strip()


@pytest.mark.parametrize(
    "text",
    [
        "<sigh> that's odd",
        "<laugh>okay<groan>",
        "  <sigh>  leading and trailing space  ",
        "plain text, no tags at all",
        "",
    ],
)
def test_paralinguistic_tags_reach_the_wire_byte_for_byte(orpheus, text):
    speak(text, base_url=orpheus.base_url)

    assert json.loads(orpheus.received[0].body)["input"] == text


# --------------------------------------------------------------------------
# AC-9: base_url resolution when the caller omits it entirely
# --------------------------------------------------------------------------
#
# Neither test above ever calls speak() without base_url — every call passes
# orpheus.base_url explicitly, so the resolution order pinned in leaf-03's
# brief (env var, else the literal "http://127.0.0.1:8080") had zero
# coverage (TEST-AUDIT.md finding 2). These two prove it. Rather than mock
# whatever HTTP client speak() uses internally, the first binds a real
# throwaway server to the exact literal default port so any implementation
# (urllib, http.client, ...) is exercised identically to the rest of this
# file; the second reuses the `orpheus` fixture's ephemeral-port server via
# the env var, so it never touches the literal default.

def test_speak_targets_the_literal_default_base_url_when_env_and_arg_are_unset(
    monkeypatch,
):
    monkeypatch.delenv("ORPHEUS_BASE_URL", raising=False)
    received: list[SimpleNamespace] = []
    wav = _make_wav(1.0)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length") or 0)
            received.append(SimpleNamespace(path=self.path, body=self.rfile.read(length)))
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav)))
            self.end_headers()
            self.wfile.write(wav)

        def log_message(self, *args):  # silence stderr noise
            pass

    server = _QuietServer(("127.0.0.1", 8080), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        speak("hi")
    finally:
        server.shutdown()
        server.server_close()

    assert len(received) == 1
    assert received[0].path == "/v1/audio/speech"


def test_speak_targets_the_orpheus_base_url_env_var_when_arg_is_unset(orpheus, monkeypatch):
    monkeypatch.setenv("ORPHEUS_BASE_URL", orpheus.base_url)

    speak("hi")

    assert len(orpheus.received) == 1
    assert orpheus.received[0].path == "/v1/audio/speech"


# --------------------------------------------------------------------------
# AC-10: realtime_factor
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "audio_duration_s, generation_time_s, expected",
    [
        (1.0, 0.5, 0.5),
        (1.0, 1.0, 1.0),
        (1.0, 1.5, 1.5),          # the plan's "< 1.5" threshold, from above
        (2.0, 1.0, 0.5),          # discriminates against the reciprocal
        (0.5, 2.0, 4.0),
        (4.0, 0.0, 0.0),
        (0.25, 0.375, 1.5),
    ],
)
def test_realtime_factor_is_generation_time_over_audio_duration(
    audio_duration_s, generation_time_s, expected
):
    assert realtime_factor(audio_duration_s, generation_time_s) == pytest.approx(expected)


# --------------------------------------------------------------------------
# AC-10: speak_and_measure composes speak + realtime_factor
# --------------------------------------------------------------------------

def test_speak_and_measure_feeds_the_wav_duration_and_wall_clock_into_realtime_factor(
    orpheus, monkeypatch
):
    orpheus.state["wav"] = _make_wav(duration_s=2.0, sample_rate=8000)
    seen: list[tuple[float, float]] = []

    def _spy(audio_duration_s, generation_time_s):
        seen.append((audio_duration_s, generation_time_s))
        return 0.4242

    monkeypatch.setattr(orpheus_mod, "realtime_factor", _spy)

    audio, factor = speak_and_measure("hello there", base_url=orpheus.base_url)

    assert len(orpheus.received) == 1
    assert audio == orpheus.state["wav"]
    assert factor == 0.4242
    assert len(seen) == 1
    assert seen[0][0] == pytest.approx(2.0)
    assert seen[0][1] > 0.0


def test_the_audio_duration_is_read_from_the_wav_header_not_assumed(orpheus, monkeypatch):
    # 0.5 s at 24 kHz: an implementation that divides frames by a hardcoded
    # 8000 would report 1.5 s here.
    orpheus.state["wav"] = _make_wav(duration_s=0.5, sample_rate=24000)
    seen: list[tuple[float, float]] = []
    monkeypatch.setattr(
        orpheus_mod, "realtime_factor", lambda a, g: (seen.append((a, g)), 1.0)[1]
    )

    speak_and_measure("short one", base_url=orpheus.base_url)

    assert seen[0][0] == pytest.approx(0.5)


def test_speak_and_measure_reports_a_real_positive_factor_unpatched(orpheus):
    orpheus.state["wav"] = _make_wav(duration_s=1.0)

    audio, factor = speak_and_measure("status report", base_url=orpheus.base_url)

    assert audio == orpheus.state["wav"]
    assert isinstance(factor, float) and factor > 0.0


# --------------------------------------------------------------------------
# AC-11: TimeoutError
# --------------------------------------------------------------------------

def test_speak_raises_timeout_error_when_the_server_hangs(orpheus):
    orpheus.state["delay"] = 5.0

    with pytest.raises(TimeoutError):
        speak("this will never come back", base_url=orpheus.base_url, timeout_s=0.3)


def test_the_default_timeout_is_thirty_seconds():
    assert inspect.signature(speak).parameters["timeout_s"].default == 30.0

"""Orpheus TTS HTTP adapter (llama.cpp OpenAI-compatible TTS server).

``speak`` posts an OpenAI-compatible ``/v1/audio/speech`` request and returns
the raw WAV bytes. ``realtime_factor``/``speak_and_measure`` derive a
realtime-factor measurement (generation time / audio duration) so callers can
check Orpheus is keeping up with real-time playback.
"""

from __future__ import annotations

import io
import json
import os
import time
import urllib.error
import urllib.request
import wave

DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_VOICE = "tara"


def _resolve_base_url(base_url: str | None) -> str:
    if base_url is not None:
        return base_url
    env_url = os.environ.get("ORPHEUS_BASE_URL")
    if env_url:
        return env_url
    return DEFAULT_BASE_URL


def speak(text: str, base_url: str | None = None, timeout_s: float = 30.0) -> bytes:
    """POST ``text`` to the Orpheus TTS server and return the raw WAV bytes."""
    resolved_base_url = _resolve_base_url(base_url)
    payload = json.dumps(
        {"input": text, "voice": DEFAULT_VOICE, "response_format": "wav"}
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{resolved_base_url}/v1/audio/speech",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return response.read()
    except (TimeoutError, urllib.error.URLError) as exc:
        if isinstance(exc, TimeoutError) or isinstance(exc.reason, TimeoutError):
            raise TimeoutError(
                f"Orpheus TTS request to {resolved_base_url} timed out after {timeout_s}s"
            ) from exc
        raise


def realtime_factor(audio_duration_s: float, generation_time_s: float) -> float:
    """Ratio of generation time to audio duration (< 1.0 is faster than realtime)."""
    return generation_time_s / audio_duration_s


def speak_and_measure(
    text: str, base_url: str | None = None
) -> tuple[bytes, float]:
    """Call ``speak``, measure wall-clock time, and compute the realtime factor."""
    start = time.monotonic()
    audio_bytes = speak(text, base_url=base_url)
    generation_time_s = time.monotonic() - start

    with wave.open(io.BytesIO(audio_bytes), "rb") as handle:
        audio_duration_s = handle.getnframes() / handle.getframerate()

    return audio_bytes, realtime_factor(audio_duration_s, generation_time_s)

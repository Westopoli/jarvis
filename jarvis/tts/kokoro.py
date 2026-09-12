"""Kokoro TTS wiring for Jarvis's real desktop voice pipeline.

Kept separate from ``jarvis/pipeline.py`` deliberately: constructing a real
``KokoroTTSService`` auto-downloads ~326MB of model files from GitHub on
first use (cached under ``~/.cache/pipecat/kokoro-onnx/`` after that), which
the automated test suite must never trigger. Call
``build_kokoro_tts_service()`` yourself and pass the result to
``jarvis.pipeline.build_pipeline``'s ``tts_service`` argument when actually
running the voice loop.
"""
from __future__ import annotations

from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.transcriptions.language import Language

# bm_lewis: British male, Kokoro-82M. Picked to match Jarvis's "concise, dry,
# British" persona (jarvis/prompt.py).
DEFAULT_VOICE = "bm_lewis"


def build_kokoro_tts_service(voice: str = DEFAULT_VOICE) -> KokoroTTSService:
    """Construct a real Kokoro TTS service. Downloads model files on first
    call if they aren't already cached — do not call this from a test."""
    return KokoroTTSService(
        settings=KokoroTTSService.Settings(voice=voice, language=Language.EN_GB),
    )

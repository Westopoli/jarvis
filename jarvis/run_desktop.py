"""Real desktop voice loop for Jarvis (plan slice 6's manual smoke check).

Wires the real ``LocalAudioTransport`` (mic + speaker), Silero VAD, a real
Whisper STT service, real Kokoro TTS (``bm_lewis``), and cascade A's
``Conversation``/``Reader``/``InterruptGate`` into the tested pipeline from
``jarvis.pipeline.build_pipeline``.

Run it:

    uv sync --extra audio --extra audio-local
    uv run python -m jarvis.run_desktop

Needs, one-time: ``sudo apt-get install -y portaudio19-dev`` (for pyaudio,
already done per project notes) and network access on first run to download
Kokoro's ONNX model files (~326MB) and the Whisper model from Hugging Face.
Both are cached afterwards (``~/.cache/pipecat/kokoro-onnx/`` and the
faster-whisper/huggingface cache) and every later run is offline.

GPU note: faster-whisper's ctranslate2 backend needs cuBLAS + cuDNN (CUDA
12) to run on the GPU. This machine has neither on the system library path
-- only Ollama's own private copies, which ctranslate2 can't see. The
``audio-local`` extra installs the standalone ``nvidia-cublas-cu12``/
``nvidia-cudnn-cu12`` wheels for this; the ``_preload_cuda_libs()`` call
below loads them with ``ctypes``/``RTLD_GLOBAL`` before any pipecat/whisper
import, so ctranslate2's own later ``dlopen`` finds the symbols already
resolved -- no ``LD_LIBRARY_PATH`` needed. Confirmed working: STT of a 6.9s
clip took 0.62s on GPU (vs. multiple seconds on CPU) -- comfortably inside
the plan's latency budget.

Current scope: on start, Jarvis immediately narrates ``NARRATION_TEXT``
below. Say "jarvis" plus a few more words to interrupt it -- the real
``InterruptGate``/``Conversation`` rules (cascade A) decide whether that
counts. Talking without the wake word, or too few words, does not interrupt.

NOT yet wired here: routing ordinary speech through
``jarvis.intent.classify_intent`` + ``jarvis.tools.registry.dispatch`` while
IDLE/LISTENING (e.g. asking "what tabs are open" and getting dispatched to a
real tool call) -- that loop is proven working as text-only in
``jarvis/repl.py`` (cascade B) but has not been integrated into this live
audio pipeline. That integration is the natural next slice, not attempted
here blind and unverified against real hardware.
"""
from __future__ import annotations

import asyncio
import ctypes
import glob
import importlib.util


def _preload_cuda_libs() -> None:
    """Load the nvidia-cublas-cu12 / nvidia-cudnn-cu12 wheels' shared
    libraries with RTLD_GLOBAL before ctranslate2 (via faster-whisper, via
    WhisperSTTService) ever tries to dlopen libcublas/libcudnn itself.
    No-ops quietly if the packages aren't installed (e.g. CPU-only use) --
    WhisperSTTService's own device="cuda" attempt will then fail with a
    clear error instead, per its own error path."""
    for pkg in ("nvidia.cublas", "nvidia.cudnn"):
        spec = importlib.util.find_spec(pkg)
        if spec is None or not spec.submodule_search_locations:
            continue
        for lib_dir in spec.submodule_search_locations:
            for path in sorted(glob.glob(f"{lib_dir}/lib/*.so*")):
                try:
                    ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


_preload_cuda_libs()

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams as PipecatVADParams
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams

from jarvis.audio.gate import InterruptGate
from jarvis.config import load_config
from jarvis.conversation import Conversation
from jarvis.pipeline import build_pipeline
from jarvis.reader import Reader
from jarvis.tts.kokoro import build_kokoro_tts_service
from jarvis.types import VADParams

# Real Whisper model id -- Systran's faster-whisper conversion of
# distil-whisper large-v3, matching the plan's "distil-large-v3" choice.
# Downloaded from Hugging Face on first use.
WHISPER_MODEL = "Systran/faster-distil-whisper-large-v3"

NARRATION_TEXT = (
    "Hello, I'm Jarvis. This is a manual smoke test of the desktop voice "
    "loop. Say my name plus a few words if you'd like to interrupt me."
)


def _wake_word_detector(text: str) -> bool:
    """Text-based wake-word check -- the same simplification cascades A/B
    already use in place of a real audio-level model (openWakeWord)."""
    return "jarvis" in text.lower()


def build_desktop_pipeline():
    """Assemble the real desktop pipeline. Touches real audio hardware and
    may download model files over the network -- never call from a test."""
    cfg = load_config()

    vad_params = VADParams(confidence=0.7, start_secs=0.3, stop_secs=0.8, min_volume=0.6)

    transport = LocalAudioTransport(
        LocalAudioTransportParams(audio_in_enabled=True, audio_out_enabled=True)
    )
    vad_processor = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(
            params=PipecatVADParams(
                confidence=vad_params.confidence,
                start_secs=vad_params.start_secs,
                stop_secs=vad_params.stop_secs,
                min_volume=vad_params.min_volume,
            )
        )
    )
    stt = WhisperSTTService(
        settings=WhisperSTTService.Settings(model=WHISPER_MODEL, no_speech_prob=0.4),
        device="cuda",
        compute_type="int8_float16",
    )
    tts = build_kokoro_tts_service()

    conversation = Conversation(active_tab=None, min_words=3)
    conversation.speech_start()
    conversation.llm_intent("narrate")

    reader = Reader(NARRATION_TEXT)
    gate = InterruptGate(
        vad_params=vad_params, min_words=3, wake_word_detector=_wake_word_detector
    )

    pipeline = build_pipeline(
        transport,
        conversation,
        reader,
        gate,
        llm_model=cfg.ollama_model,
        vad_processor=vad_processor,
        stt_service=stt,
        tts_service=tts,
        wake_word_detector=_wake_word_detector,
    )
    return pipeline


async def main() -> None:
    pipeline = build_desktop_pipeline()
    worker = PipelineWorker(pipeline, params=PipelineParams(audio_out_sample_rate=24000))
    runner = PipelineRunner()
    await runner.run(worker)


if __name__ == "__main__":
    asyncio.run(main())

# spec: N/A (post-cascade follow-up — user-selected voice wiring, not a manager-mode leaf)
"""Verifies jarvis.tts.kokoro's wiring without ever downloading the real
Kokoro model files (patches the two things that would trigger a real
network call / real ONNX load: _ensure_model_files and the Kokoro class
itself)."""
from unittest.mock import MagicMock, patch


def test_build_kokoro_tts_service_defaults_to_bm_lewis():
    from jarvis.tts.kokoro import DEFAULT_VOICE, build_kokoro_tts_service

    assert DEFAULT_VOICE == "bm_lewis"

    with (
        patch("pipecat.services.kokoro.tts._ensure_model_files"),
        patch("pipecat.services.kokoro.tts.Kokoro", return_value=MagicMock()),
    ):
        service = build_kokoro_tts_service()

    assert service._settings.voice == "bm_lewis"
    # TTSService resolves the Language enum to Kokoro's own locale string
    # (via language_to_kokoro_language) during settings application.
    assert service._settings.language == "en-gb"


def test_build_kokoro_tts_service_accepts_a_different_voice():
    from jarvis.tts.kokoro import build_kokoro_tts_service

    with (
        patch("pipecat.services.kokoro.tts._ensure_model_files"),
        patch("pipecat.services.kokoro.tts.Kokoro", return_value=MagicMock()),
    ):
        service = build_kokoro_tts_service(voice="bm_george")

    assert service._settings.voice == "bm_george"


def test_build_pipeline_orders_stt_llm_narrator_tts():
    """source -> stt -> user aggregator -> llm -> narrator -> tts -> sink -> assistant aggregator."""
    from jarvis.pipeline import build_pipeline
    from jarvis.session import JarvisSession
    from tests.harness.frame_capture import CaptureTransport

    fake_stt = MagicMock(name="stt_service")
    fake_llm = MagicMock(name="llm_service")
    fake_tts = MagicMock(name="tts_service")
    session = JarvisSession()
    built = build_pipeline(
        CaptureTransport(), session, llm=fake_llm, stt=fake_stt, tts=fake_tts, vad=False
    )
    procs = built.pipeline.processors
    assert procs.index(fake_stt) < procs.index(built.user_aggregator)
    assert procs.index(built.user_aggregator) < procs.index(fake_llm)
    assert procs.index(fake_llm) < procs.index(built.narrator) < procs.index(fake_tts)
    assert procs.index(fake_tts) < procs.index(built.assistant_aggregator)

    built_no_tts = build_pipeline(CaptureTransport(), JarvisSession(), vad=False)
    assert fake_tts not in built_no_tts.pipeline.processors

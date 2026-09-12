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


def test_build_pipeline_wires_a_real_tts_service_into_the_processor_chain():
    from jarvis.pipeline import build_pipeline
    from jarvis.conversation import Conversation
    from jarvis.reader import Reader
    from jarvis.audio.gate import InterruptGate
    from jarvis.types import VADParams
    from tests.harness.fake_transport import FakeTransport

    transport = FakeTransport()
    convo = Conversation(active_tab=1)
    reader = Reader("Hello there.")
    gate = InterruptGate(
        vad_params=VADParams(confidence=0.7, start_secs=0.3, stop_secs=0.8, min_volume=0.6),
        min_words=3,
        wake_word_detector=lambda text: "jarvis" in text.lower(),
    )
    fake_tts = MagicMock(name="tts_service")

    pipeline = build_pipeline(transport, convo, reader, gate, tts_service=fake_tts)

    assert fake_tts in pipeline.processors

    # default (no tts_service) behavior is unchanged — the fake stays out of
    # the chain and existing tests/umbrella keep passing.
    pipeline_no_tts = build_pipeline(transport, convo, reader, gate)
    assert fake_tts not in pipeline_no_tts.processors


def test_build_pipeline_wires_an_stt_service_before_narration():
    from jarvis.pipeline import build_pipeline
    from jarvis.conversation import Conversation
    from jarvis.reader import Reader
    from jarvis.audio.gate import InterruptGate
    from jarvis.types import VADParams
    from tests.harness.fake_transport import FakeTransport

    transport = FakeTransport()
    convo = Conversation(active_tab=1)
    reader = Reader("Hello there.")
    gate = InterruptGate(
        vad_params=VADParams(confidence=0.7, start_secs=0.3, stop_secs=0.8, min_volume=0.6),
        min_words=3,
        wake_word_detector=lambda text: "jarvis" in text.lower(),
    )
    fake_stt = MagicMock(name="stt_service")
    fake_tts = MagicMock(name="tts_service")

    pipeline = build_pipeline(
        transport, convo, reader, gate, stt_service=fake_stt, tts_service=fake_tts
    )

    # stt runs before narration (which is before tts): source -> stt -> narrator -> tts -> sink
    stt_index = pipeline.processors.index(fake_stt)
    tts_index = pipeline.processors.index(fake_tts)
    assert 0 < stt_index < tts_index < len(pipeline.processors) - 1


def test_build_pipeline_wires_a_vad_processor_before_stt():
    from jarvis.pipeline import build_pipeline
    from jarvis.conversation import Conversation
    from jarvis.reader import Reader
    from jarvis.audio.gate import InterruptGate
    from jarvis.types import VADParams
    from tests.harness.fake_transport import FakeTransport

    transport = FakeTransport()
    convo = Conversation(active_tab=1)
    reader = Reader("Hello there.")
    gate = InterruptGate(
        vad_params=VADParams(confidence=0.7, start_secs=0.3, stop_secs=0.8, min_volume=0.6),
        min_words=3,
        wake_word_detector=lambda text: "jarvis" in text.lower(),
    )
    fake_vad = MagicMock(name="vad_processor")
    fake_stt = MagicMock(name="stt_service")

    pipeline = build_pipeline(
        transport, convo, reader, gate, vad_processor=fake_vad, stt_service=fake_stt
    )

    vad_index = pipeline.processors.index(fake_vad)
    stt_index = pipeline.processors.index(fake_stt)
    assert 0 < vad_index < stt_index

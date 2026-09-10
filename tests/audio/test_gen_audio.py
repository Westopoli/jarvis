# spec: specs/cascade-a.md::Acceptance criteria::AC-19
"""Tests for the audio fixture generators in ``tests/fixtures/gen_audio.py``
(leaf-04, AC 19).

``write_noisy_speech`` is the orchestrating entry point: it must actually call
the noise generator and hand the mixed samples to ``soundfile.write`` (import
soundfile as a module, e.g. ``import soundfile as sf`` / ``sf.write(...)``, so
the collaborator stays patchable), rather than writing bytes some other way.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile

from tests.fixtures import gen_audio

SAMPLE_RATE = 16_000


def _band_power(samples: np.ndarray, low_hz: float, high_hz: float) -> float:
    spectrum = np.abs(np.fft.rfft(samples)) ** 2
    freqs = np.fft.rfftfreq(len(samples), d=1.0 / SAMPLE_RATE)
    band = spectrum[(freqs >= low_hz) & (freqs < high_hz)]
    return float(band.mean())


@pytest.mark.parametrize("duration_s", [0.0, 0.25, 1.0])
def test_white_noise_has_the_requested_length_and_is_float(duration_s):
    samples = gen_audio.white_noise(duration_s, sample_rate=SAMPLE_RATE, seed=1)

    assert len(samples) == int(duration_s * SAMPLE_RATE)
    assert np.issubdtype(samples.dtype, np.floating)


def test_white_noise_is_reproducible_for_a_fixed_seed():
    assert np.array_equal(
        gen_audio.white_noise(0.1, sample_rate=SAMPLE_RATE, seed=7),
        gen_audio.white_noise(0.1, sample_rate=SAMPLE_RATE, seed=7),
    )


def test_white_noise_spectrum_is_flat_across_bands():
    samples = gen_audio.white_noise(1.0, sample_rate=SAMPLE_RATE, seed=3)
    ratio = _band_power(samples, 20, 500) / _band_power(samples, 2_000, 6_000)

    assert 0.5 < ratio < 2.0


def test_pink_noise_carries_more_low_frequency_energy_than_high():
    samples = gen_audio.pink_noise(1.0, sample_rate=SAMPLE_RATE, seed=3)
    ratio = _band_power(samples, 20, 500) / _band_power(samples, 2_000, 6_000)

    assert ratio > 3.0


@pytest.mark.parametrize(
    "snr_db, expected_sample",
    [
        # speech and noise both unit power, so the noise is scaled by
        # 10 ** (-snr_db / 20) and every output sample is 1 + that scale.
        (0.0, 2.0),
        (20.0, 1.1),
        (-10.0, 1.0 + 10.0 ** 0.5),
    ],
)
def test_mix_at_snr_scales_noise_to_the_requested_ratio(snr_db, expected_sample):
    speech = np.ones(256, dtype=np.float64)
    noise = np.ones(256, dtype=np.float64)

    mixed = gen_audio.mix_at_snr(speech, noise, snr_db)

    assert np.allclose(mixed, expected_sample, rtol=1e-6)


def test_mix_at_snr_preserves_the_speech_length():
    speech = np.linspace(-0.5, 0.5, 400)
    noise = gen_audio.white_noise(400 / SAMPLE_RATE, sample_rate=SAMPLE_RATE, seed=2)

    assert len(gen_audio.mix_at_snr(speech, noise, 10.0)) == 400


def test_write_noisy_speech_mixes_generated_noise_and_writes_a_wav(monkeypatch):
    speech = np.linspace(-0.5, 0.5, 320)
    stub_noise = np.full(320, 0.25)
    generated: list[tuple] = []
    written: list[tuple] = []

    def fake_white_noise(duration_s, sample_rate=SAMPLE_RATE, seed=None):
        generated.append((duration_s, sample_rate, seed))
        return stub_noise

    monkeypatch.setattr(gen_audio, "white_noise", fake_white_noise)
    monkeypatch.setattr(
        soundfile,
        "write",
        lambda path, data, samplerate, *a, **k: written.append((path, data, samplerate)),
    )

    result = gen_audio.write_noisy_speech(
        "noisy-speech", speech, snr_db=6.0, noise_kind="white", sample_rate=SAMPLE_RATE
    )

    assert len(generated) == 1
    assert len(written) == 1

    path, data, samplerate = written[0]
    assert (Path(path).parts[-4:], samplerate) == (
        ("tests", "fixtures", "audio", "noisy-speech.wav"),
        SAMPLE_RATE,
    )
    assert np.allclose(data, gen_audio.mix_at_snr(speech, stub_noise, 6.0))
    assert Path(result) == Path(path)


def test_write_noisy_speech_uses_the_pink_generator_when_asked(monkeypatch):
    calls: list[str] = []

    def fake_pink_noise(*args, **kwargs):
        calls.append("pink")
        return np.full(320, 0.1)

    monkeypatch.setattr(gen_audio, "pink_noise", fake_pink_noise)
    monkeypatch.setattr(soundfile, "write", lambda *a, **k: None)

    gen_audio.write_noisy_speech(
        "pinky",
        np.linspace(-0.5, 0.5, 320),
        snr_db=3.0,
        noise_kind="pink",
        sample_rate=SAMPLE_RATE,
    )

    assert calls == ["pink"]

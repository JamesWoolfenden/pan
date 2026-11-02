import numpy as np

from backend.tagging.audio_features import (
    _MAJOR_PROFILE,
    _MINOR_PROFILE,
    _estimate_key,
    _estimate_tempo,
    _measure_loudness,
)


def test_measure_loudness_returns_a_finite_value_for_a_real_signal():
    sr = 22050
    t = np.linspace(0, 2.0, int(sr * 2.0), endpoint=False)
    y = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

    loudness = _measure_loudness(y, sr)

    assert loudness is not None
    assert np.isfinite(loudness)


def test_measure_loudness_returns_none_for_silence():
    sr = 22050
    y = np.zeros(int(sr * 2.0), dtype=np.float32)

    assert _measure_loudness(y, sr) is None


def test_measure_loudness_returns_none_for_input_too_short_to_measure():
    sr = 22050
    y = np.zeros(10, dtype=np.float32)

    assert _measure_loudness(y, sr) is None


def _click_track(bpm: float, seconds: float = 30.0, sr: int = 22050) -> np.ndarray:
    rng = np.random.default_rng(0)
    y = rng.standard_normal(int(sr * seconds)).astype(np.float32) * 0.02
    step = 60.0 / bpm
    length = int(0.08 * sr)
    thump = (
        np.sin(2 * np.pi * 60 * np.arange(length) / sr)
        * np.exp(-np.arange(length) / (0.02 * sr))
    ).astype(np.float32)
    for i in range(int(seconds / step)):
        start = int(i * step * sr)
        if start + length < y.size:
            y[start : start + length] += thump
    return y


def test_estimate_tempo_finds_a_clear_pulse():
    for bpm in (80, 100, 120, 140):
        assert abs(_estimate_tempo(_click_track(bpm), 22050) - bpm) < 3


def test_estimate_tempo_returns_none_for_silence():
    assert _estimate_tempo(np.zeros(22050 * 5, dtype=np.float32), 22050) is None


def test_estimate_key_distinguishes_major_from_minor():
    # The profiles themselves are the ideal chroma for their key: tonic 0 = C.
    assert _estimate_key(_MAJOR_PROFILE) == "C"
    assert _estimate_key(_MINOR_PROFILE) == "Cm"
    # Rotated to tonic 4 (E) - and E minor must not be reported as its relative major, G.
    assert _estimate_key(np.roll(_MINOR_PROFILE, 4)) == "Em"

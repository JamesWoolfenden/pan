import numpy as np

from backend.tagging.audio_features import _measure_loudness


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

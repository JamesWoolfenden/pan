from __future__ import annotations

import logging
from pathlib import Path

import librosa
import numpy as np

log = logging.getLogger(__name__)

KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def extract_features(file_path: str | Path) -> dict | None:
    """Extract tempo (BPM), energy (RMS, normalized 0-1) and estimated musical key from an
    audio file. Returns None if the file can't be loaded (missing, corrupt, unreadable)."""
    try:
        y, sr = librosa.load(file_path, sr=22050, mono=True, duration=60)
    except Exception:
        log.debug("Could not load audio for feature extraction: %s", file_path, exc_info=True)
        return None

    if y.size == 0:
        return None

    # hop_length=512 (the default) quantizes tempo to a coarse lag-bin grid - e.g. 60*sr/512/lag
    # for integer lag - so hundreds of unrelated tracks collide on bit-identical BPM values
    # (verified: 1302 of 19191 tracks landed on exactly 143.5546875). hop_length=128 gives 4x
    # finer resolution and separates them into genuinely distinct values.
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr, hop_length=128)
    rms = librosa.feature.rms(y=y)
    energy = float(np.clip(np.mean(rms) * 10, 0.0, 1.0))

    chroma = librosa.feature.chroma_stft(y=y, sr=sr)
    key_index = int(np.argmax(np.mean(chroma, axis=1)))

    return {
        "tempo": float(tempo if np.isscalar(tempo) else tempo[0]),
        "energy": energy,
        "musical_key": KEY_NAMES[key_index],
    }

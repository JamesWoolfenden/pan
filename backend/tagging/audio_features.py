from __future__ import annotations

import logging
from pathlib import Path

import audioread
import librosa
import numpy as np
import pyloudnorm

log = logging.getLogger(__name__)

KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _load_with_audioread(
    file_path: str | Path, sr: int, mono: bool, duration: float
) -> tuple[np.ndarray, int]:
    """librosa.load() (1.0+) only uses soundfile/libsndfile, which has never supported AAC/M4A -
    every .m4a in the library fails here with "Format not recognised" regardless of whether the
    file is actually fine. audioread (with ffmpeg as its backend) decodes those where soundfile
    can't; used as a fallback rather than the default since soundfile is faster for formats it
    does support."""
    with audioread.audio_open(str(file_path)) as f:
        native_sr = f.samplerate
        channels = f.channels
        buf = bytearray()
        max_bytes = int(duration * native_sr * channels * 2) if duration else None
        for block in f:
            buf.extend(block)
            if max_bytes and len(buf) >= max_bytes:
                break
    y = np.frombuffer(bytes(buf), dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        y = y[: (len(y) // channels) * channels].reshape(-1, channels)
        y = y.mean(axis=1) if mono else y.T
    if sr is not None and sr != native_sr:
        y = librosa.resample(y, orig_sr=native_sr, target_sr=sr)
        native_sr = sr
    return y, native_sr


def extract_features(file_path: str | Path) -> dict | None:
    """Extract tempo (BPM), energy (RMS, normalized 0-1) and estimated musical key from an
    audio file. Returns None if the file can't be loaded (missing, corrupt, unreadable)."""
    try:
        y, sr = librosa.load(file_path, sr=22050, mono=True, duration=60)
    except Exception:  # noqa: BLE001 - soundfile/audioread's failure modes aren't a stable,
        # enumerable set (format/codec/corruption errors vary); the fallback below needs to run
        # regardless of which one it was, and the final except below is the real backstop.
        try:
            y, sr = _load_with_audioread(file_path, sr=22050, mono=True, duration=60)
        except Exception:
            log.debug(
                "Could not load audio for feature extraction: %s",
                file_path,
                exc_info=True,
            )
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
        "loudness_lufs": _measure_loudness(y, sr),
    }


def _measure_loudness(y: np.ndarray, sr: int) -> float | None:
    """Integrated loudness (LUFS, ITU-R BS.1770) for volume normalization (see ui/app.js) -
    measured from the same 60s decode as tempo/energy above, not a separate file read. Returns
    None for anything pyloudnorm can't give a real number for: too short for its gating-block
    window, or pure/near silence (-inf)."""
    try:
        loudness = pyloudnorm.Meter(sr).integrated_loudness(y)
    except Exception:  # noqa: BLE001 - pyloudnorm raises on too-short input; not worth
        # enumerating every case, a missing loudness value just disables normalization for
        # this one track rather than failing feature extraction altogether.
        return None
    return float(loudness) if np.isfinite(loudness) else None

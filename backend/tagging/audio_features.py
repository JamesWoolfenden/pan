from __future__ import annotations

import logging
from pathlib import Path

import audioread
import librosa
import librosa.feature.rhythm  # lazily loaded submodule - attribute access alone fails
import numpy as np
import pyloudnorm

log = logging.getLogger(__name__)

KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


SR = 22050
HOP = 128
# Short windows spread across the track instead of one block from 0:00. Intros are routinely
# unrepresentative (a sparse acoustic opening, a drum-less fade-in, a spoken count-in), and
# long tracks change tempo, so a single minute from the start gave confidently wrong BPMs.
WINDOW_SECONDS = 30
WINDOW_FRACTIONS = (0.25, 0.5, 0.75)
# Tracks this short are just analysed whole from the start (no point sampling a 70s track).
WHOLE_TRACK_MAX_SECONDS = 75

# Krumhansl-Schmuckler key profiles (index 0 = tonic), correlated against the track's mean
# chroma rotated to each possible tonic. Picking the loudest pitch class alone (what this used
# to do) can't tell G major from E minor or C major - it just reports whichever note rings most.
_MAJOR_PROFILE = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
_MINOR_PROFILE = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)


def _load_with_audioread(
    file_path: str | Path, sr: int, offset: float, duration: float
) -> np.ndarray:
    """librosa.load() (1.0+) only uses soundfile/libsndfile, which has never supported AAC/M4A -
    every .m4a in the library fails here with "Format not recognised" regardless of whether the
    file is actually fine. audioread (with ffmpeg as its backend) decodes those where soundfile
    can't; used as a fallback rather than the default since soundfile is faster for formats it
    does support. It can't seek, so an offset means decoding and discarding up to that point."""
    with audioread.audio_open(str(file_path)) as f:
        native_sr = f.samplerate
        channels = f.channels
        bytes_per_second = native_sr * channels * 2
        skip = int(offset * bytes_per_second) // (channels * 2) * (channels * 2)
        want = int(duration * bytes_per_second)
        buf = bytearray()
        for block in f:
            if skip:
                if len(block) <= skip:
                    skip -= len(block)
                    continue
                block = block[skip:]
                skip = 0
            buf.extend(block)
            if len(buf) >= want:
                break
    y = np.frombuffer(bytes(buf), dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        y = y[: (len(y) // channels) * channels].reshape(-1, channels).mean(axis=1)
    if sr != native_sr:
        y = librosa.resample(y, orig_sr=native_sr, target_sr=sr)
    return y


def _load_segment(file_path: str | Path, offset: float, duration: float) -> np.ndarray:
    try:
        y, _ = librosa.load(
            file_path, sr=SR, mono=True, offset=offset, duration=duration
        )
        return y
    except Exception:  # noqa: BLE001 - soundfile/audioread's failure modes aren't a stable,
        # enumerable set (format/codec/corruption errors vary); the fallback needs to run
        # regardless of which one it was.
        return _load_with_audioread(file_path, SR, offset, duration)


def _track_duration(file_path: str | Path) -> float | None:
    try:
        return float(librosa.get_duration(path=str(file_path)))
    except Exception:  # noqa: BLE001 - same reasoning as _load_segment.
        try:
            with audioread.audio_open(str(file_path)) as f:
                return float(f.duration)
        except Exception:  # noqa: BLE001
            return None


def _segments(file_path: str | Path) -> list[np.ndarray]:
    duration = _track_duration(file_path)
    if duration is None or duration <= WHOLE_TRACK_MAX_SECONDS:
        return [_load_segment(file_path, 0.0, 60.0)]
    segments = []
    for fraction in WINDOW_FRACTIONS:
        offset = max(0.0, duration * fraction - WINDOW_SECONDS / 2)
        offset = min(offset, duration - WINDOW_SECONDS)
        segments.append(_load_segment(file_path, offset, WINDOW_SECONDS))
    return segments


def _estimate_tempo(y: np.ndarray, sr: int) -> float | None:
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    if not np.any(onset_env):
        return None
    # librosa.feature.rhythm.tempo() is the actual tempo estimator (autocorrelation of the onset
    # envelope under a log-normal prior); beat_track() just returns a by-product of placing
    # beats and favours whatever periodicity it can chain together, often at half/double time.
    # The prior is centred on 110 BPM, where most music's perceived pulse sits, which resolves
    # the half/double ambiguity toward the musically natural octave.
    tempo = librosa.feature.rhythm.tempo(
        onset_envelope=onset_env, sr=sr, hop_length=HOP, start_bpm=110, max_tempo=200
    )
    return float(np.atleast_1d(tempo)[0])


def _estimate_key(chroma_mean: np.ndarray) -> str:
    if not np.any(chroma_mean):
        return KEY_NAMES[0]
    best_score, best_key = -2.0, KEY_NAMES[0]
    for tonic in range(12):
        rotated = np.roll(chroma_mean, -tonic)
        for profile, suffix in ((_MAJOR_PROFILE, ""), (_MINOR_PROFILE, "m")):
            score = float(np.corrcoef(rotated, profile)[0, 1])
            if score > best_score:
                best_score, best_key = score, KEY_NAMES[tonic] + suffix
    return best_key


def extract_features(file_path: str | Path) -> dict | None:
    """Extract tempo (BPM), energy (RMS, normalized 0-1), estimated musical key and loudness
    from an audio file, sampled from several windows spread through the track. Returns None if
    the file can't be loaded (missing, corrupt, unreadable)."""
    try:
        segments = [seg for seg in _segments(file_path) if seg.size]
    except Exception:
        log.debug(
            "Could not load audio for feature extraction: %s", file_path, exc_info=True
        )
        return None
    if not segments:
        return None

    # Per-window tempo then the median, so one odd section (a tempo change, a quiet bridge)
    # can't drag the whole track's BPM. hop_length=HOP (128, not librosa's 512) keeps BPM from
    # quantizing onto a coarse grid where unrelated tracks collide on identical values
    # (verified once: 1302 of 19191 tracks at exactly 143.5546875).
    tempos = [t for t in (_estimate_tempo(seg, SR) for seg in segments) if t]
    tempo = float(np.median(tempos)) if tempos else 0.0

    energy = float(
        np.clip(
            np.mean([np.mean(librosa.feature.rms(y=seg)) for seg in segments]) * 10,
            0.0,
            1.0,
        )
    )
    chroma_mean = np.mean(
        [
            np.mean(librosa.feature.chroma_stft(y=seg, sr=SR), axis=1)
            for seg in segments
        ],
        axis=0,
    )

    return {
        "tempo": tempo,
        "energy": energy,
        "musical_key": _estimate_key(chroma_mean),
        "loudness_lufs": _measure_loudness(np.concatenate(segments), SR),
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

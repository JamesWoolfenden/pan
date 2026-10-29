from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import mutagen

from backend.db import Track
from backend.paths import resolve, to_relative

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".ogg", ".wav", ".aac", ".wma"}
# Concert/music-video files, ingested from the same shared-drive walk as audio. mutagen has no
# Matroska/WebM module at all (confirmed by inspecting the installed package), so these go through
# ffprobe (_read_video_tags below) instead of mutagen for every extension here, not just .mkv -
# one code path, and ffprobe reads every container's tag/format structure more consistently than
# mutagen's per-format modules do anyway.
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv"}

_FFPROBE_AVAILABLE = shutil.which("ffprobe") is not None
if not _FFPROBE_AVAILABLE:
    log.warning(
        "ffprobe not found on PATH - video files will be ingested with filename-only metadata "
        "(no duration/tags) until ffmpeg is installed"
    )

# A scene-release filename this is read off of separates words with underscores or dots as often
# as with real spaces (e.g. "..._-_04._Episode_4_..."), and underscore is a \w character - a
# plain \b right after/before one of these words fails to anchor at all (no boundary exists
# between two \w characters), silently missing real matches. (?:^|[^A-Za-z]) / (?!\d) stand in
# for the leading/trailing \b, treating underscore (and digits, punctuation, start-of-string) as
# valid separators instead of only whitespace/non-word characters.
_WORD_BOUNDARY_START = r"(?:^|[^A-Za-z])"

# SxxExx (e.g. "S01E03", "s1e3") - checked first since it unambiguously gives both numbers from
# one match.
_SEASON_EPISODE_RE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})(?!\d)")
# "1x03" alt format.
_SEASON_EPISODE_X_RE = re.compile(_WORD_BOUNDARY_START + r"(\d{1,2})x(\d{2,3})(?!\d)")
# Spelled-out fallbacks, usually when season/episode aren't both encoded in one compact token -
# "Season 01"/"Series 2" (commonly the containing folder, not the filename itself) for season;
# "Episode 03"/"Ep 3"/"Ep.03"/"Episode_04" for episode.
_SEASON_WORD_RE = re.compile(
    _WORD_BOUNDARY_START + r"(?:Season|Series)[\s_.]*#?[\s_.]*(\d{1,2})(?!\d)",
    re.IGNORECASE,
)
_EPISODE_WORD_RE = re.compile(
    _WORD_BOUNDARY_START + r"Ep(?:isode)?[\s_.]*#?[\s_.]*(\d{1,3})(?!\d)", re.IGNORECASE
)
# Bare "E04" with no season marker anywhere (seen in the wild for single-season documentary
# series, e.g. "The.1619.Project.E04.Capitalism...") - 2-3 digits specifically required, since a
# lone letter+digit is otherwise too easy to collide with an unrelated filename token.
_EPISODE_BARE_RE = re.compile(_WORD_BOUNDARY_START + r"E(\d{2,3})(?!\d)")


def _parse_season_episode(file_path: Path) -> tuple[int | None, int | None]:
    """Season/episode for a TV episode, read off the filename and its immediate containing
    folder (e.g. .../Show Name/Season 01/Show Name S01E03 Title.mkv) rather than file metadata -
    ripped/downloaded TV episodes essentially never carry season/episode as embedded tags, only
    as filename/folder convention. Tries the compact SxxExx / NxNN forms first since they
    unambiguously give both numbers from one match; falls back to the spelled-out "Season"/
    "Episode" words (often split across the folder and filename) for anything that doesn't match
    those. Returns (None, None) for a movie or anything that matches neither - there's no
    reliable way to tell "this genuinely has no season/episode" apart from "the naming just
    doesn't match", so this stays conservative rather than guessing from a bare number that
    could just as easily be a year or resolution."""
    haystack = f"{file_path.parent.name} {file_path.stem}"

    match = _SEASON_EPISODE_RE.search(haystack)
    if match:
        return int(match.group(1)), int(match.group(2))

    match = _SEASON_EPISODE_X_RE.search(haystack)
    if match:
        return int(match.group(1)), int(match.group(2))

    season_match = _SEASON_WORD_RE.search(haystack)
    episode_match = _EPISODE_WORD_RE.search(haystack) or _EPISODE_BARE_RE.search(
        haystack
    )
    return (
        int(season_match.group(1)) if season_match else None,
        int(episode_match.group(1)) if episode_match else None,
    )


def normalize_path(path: str, source: str | None = None) -> str:
    """Normalize a filesystem path for cross-source matching (case/separator-insensitive).
    Accepts either an absolute path, or one stored relative to SHARED_DRIVE_PATH or (for
    local_video rows, when `source` is passed) VIDEO_DRIVE_PATH - see resolve()'s docstring.
    `source` only matters for a relative path read back from the DB; during an active walk the
    path is already absolute (resolve() short-circuits on that), so callers here that process
    paths fresh off disk don't need to pass it."""
    resolved = resolve(path, source)
    return str(resolved.resolve()).lower() if resolved else ""


def _stable_id(path: str) -> str:
    return hashlib.sha1(normalize_path(path).encode("utf-8")).hexdigest()


def iter_local_tracks(
    root: str,
    skip_paths: set[str] | None = None,
    extensions: set[str] | None = None,
) -> Iterator[Track]:
    """Walk `root` for audio and video files and yield a Track per file, skipping any path
    already known (e.g. because Plex already reports it) via its normalized form in
    `skip_paths`. Video files (concert/music-video rips) live in the same walk as audio by
    default - they naturally share library folders - and come out with source='local_video'
    instead of 'local', tagged via ffprobe (_read_video_tags) instead of mutagen (_read_tags).

    `extensions` restricts which files this walk considers (default: AUDIO_EXTENSIONS |
    VIDEO_EXTENSIONS, i.e. everything) - used to scan VIDEO_DRIVE_PATH, when configured, for
    video only, since that root's whole purpose is holding video the main library walk
    (SHARED_DRIVE_PATH) wouldn't otherwise reach."""
    skip_paths = skip_paths or set()
    allowed_extensions = (
        extensions if extensions is not None else AUDIO_EXTENSIONS | VIDEO_EXTENSIONS
    )
    root_path = Path(root)
    if not root_path.exists():
        log.warning("Shared drive path does not exist: %s", root)
        return

    for file_path in root_path.rglob("*"):
        suffix = file_path.suffix.lower()
        is_video = suffix in VIDEO_EXTENSIONS
        if suffix not in allowed_extensions or not file_path.is_file():
            continue

        normalized = normalize_path(str(file_path))
        if normalized in skip_paths:
            continue

        tags = _read_video_tags(file_path) if is_video else _read_tags(file_path)
        season, episode = _parse_season_episode(file_path) if is_video else (None, None)
        yield Track(
            external_id=f"local:{_stable_id(str(file_path))}",
            source="local_video" if is_video else "local",
            file_path=to_relative(str(file_path), root),
            title=tags.get("title", file_path.stem),
            # Still read from ffprobe tags when present - VIDEO_EXTENSIONS also covers concert/
            # music-video files (see this module's docstring), where artist/album are genuine,
            # relevant metadata. For a movie or TV episode these tags are essentially always
            # empty in practice anyway; the UI (trackSubtitle in app.js) is what actually stops
            # treating a video with no artist as needing an "Unknown artist" filler, not this.
            artist=tags.get("artist", ""),
            album=tags.get("album", ""),
            genre=tags.get("genre"),
            year=tags.get("year"),
            season=season,
            episode=episode,
            disc_number=tags.get("disc_number"),
            track_number=tags.get("track_number"),
            duration_ms=tags.get("duration_ms"),
            tag_source="metadata" if tags.get("genre") else "none",
        )


def _position(value: str | None) -> int | None:
    """A track/disc number tag - "3", "3/12" (of 12), sometimes "03" - as just the number."""
    if not value:
        return None
    try:
        number = int(str(value).split("/")[0].strip())
    except ValueError:
        return None
    return number if number > 0 else None


def read_track_position(file_path: Path) -> tuple[int | None, int | None]:
    """(disc_number, track_number) from a file's own tags - see merge.py's backfill."""
    tags = _read_tags(file_path)
    return tags.get("disc_number"), tags.get("track_number")


def _read_tags(file_path: Path) -> dict:
    try:
        audio = mutagen.File(file_path, easy=True)
    except Exception:
        log.debug("Failed to read tags for %s", file_path, exc_info=True)
        return {}
    if audio is None:
        return {}

    def first(key: str) -> str | None:
        values = audio.get(key)
        return values[0] if values else None

    year_raw = first("date") or first("year")
    year = None
    if year_raw:
        try:
            year = int(str(year_raw)[:4])
        except ValueError:
            year = None

    duration_ms = (
        int(audio.info.length * 1000) if getattr(audio, "info", None) else None
    )

    return {
        "title": first("title"),
        "artist": first("artist"),
        "album": first("album"),
        "genre": first("genre"),
        "year": year,
        "disc_number": _position(first("discnumber")),
        "track_number": _position(first("tracknumber")),
        "duration_ms": duration_ms,
    }


def _read_video_tags(file_path: Path) -> dict:
    """ffprobe-based metadata for video containers - mutagen has no Matroska/WebM support at
    all, and ffprobe handles every container's tag/format structure more consistently than
    mutagen's per-format modules, so this is used for all video extensions, not just .mkv.
    Returns {} on any failure (ffprobe missing, corrupt file, timeout) - enrichment, never
    something that should break an ingest scan, same philosophy as backend/lastfm.py."""
    if not _FFPROBE_AVAILABLE:
        return {}
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        fmt = json.loads(result.stdout).get("format", {})
        tags = {k.lower(): v for k, v in fmt.get("tags", {}).items()}
        duration_s = fmt.get("duration")
        return {
            "duration_ms": int(float(duration_s) * 1000) if duration_s else None,
            "title": tags.get("title"),
            "artist": tags.get("artist"),
            "album": tags.get("album"),
            "genre": tags.get("genre"),
        }
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError, ValueError):
        log.warning(
            "ffprobe failed for %s - ingesting with filename-only metadata", file_path
        )
        return {}

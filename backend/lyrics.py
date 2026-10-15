from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

import mutagen

from backend.paths import resolve as resolve_file_path

log = logging.getLogger(__name__)

LRCLIB_URL = "https://lrclib.net/api/get"


def extract_embedded_lyrics(file_path: str) -> str | None:
    """Pull embedded lyrics straight out of the audio file's own tags - an ID3 USLT frame (MP3),
    a Vorbis comment LYRICS/UNSYNCEDLYRICS field (FLAC/OGG), or an MP4 (c)lyr atom (M4A/AAC) -
    mirroring extract_album_art's approach of reading what's already in the file rather than a
    separate store. Returns None if the file can't be read or has no embedded lyrics."""
    path = resolve_file_path(file_path)
    if path is None or not path.exists():
        return None
    try:
        audio = mutagen.File(path)
    except Exception:
        log.debug("Failed to read audio tags for lyrics: %s", path, exc_info=True)
        return None
    if audio is None:
        return None

    tags = getattr(audio, "tags", None)
    if tags is None:
        return None

    get_all = getattr(tags, "getall", None)
    if get_all:
        uslts = get_all("USLT")
        if uslts:
            return uslts[0].text or None

    for key in ("LYRICS", "UNSYNCEDLYRICS", "\xa9lyr"):
        # Vorbis comments (FLAC/OGG) raise ValueError rather than KeyError for a key they
        # consider invalid - e.g. the MP4-only "\xa9lyr" atom name isn't valid-looking as a
        # Vorbis field - so both must be caught, not just the KeyError .get() normally handles.
        try:
            value = tags[key]
        except (LookupError, ValueError):
            continue
        text = value[0] if isinstance(value, list) else value
        text = str(text).strip()
        if text:
            return text

    return None


def fetch_online_lyrics(
    artist: str, title: str, album: str | None = None, duration_ms: int | None = None
) -> str | None:
    """Looks up plain (unsynced) lyrics from lrclib.net's free, keyless API - used when the file
    itself has none embedded. Returns None on any failure (not found, network error, etc.) rather
    than raising, since lyrics are a nice-to-have that shouldn't break the track detail view."""
    if not artist or not title:
        return None

    params = {"artist_name": artist, "track_name": title}
    if album:
        params["album_name"] = album
    if duration_ms:
        params["duration"] = str(round(duration_ms / 1000))
    url = f"{LRCLIB_URL}?{urllib.parse.urlencode(params)}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pan (personal music library)"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
    except Exception:
        log.debug("lrclib lookup failed for %s - %s", artist, title, exc_info=True)
        return None

    return data.get("plainLyrics") or None


def get_lyrics(row) -> tuple[str | None, str]:
    """Embedded tags first, then lrclib as a fallback. Returns (text, source) where source is
    "embedded", "online", or "none" (no lyrics found anywhere)."""
    if row["file_path"]:
        embedded = extract_embedded_lyrics(row["file_path"])
        if embedded:
            return embedded, "embedded"

    online = fetch_online_lyrics(row["artist"], row["title"], row["album"], row["duration_ms"])
    if online:
        return online, "online"

    return None, "none"

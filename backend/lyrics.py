from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

import mutagen

from backend.paths import resolve as resolve_file_path

log = logging.getLogger(__name__)

LRCLIB_URL = "https://lrclib.net/api/get"

_SORT_SUFFIX_RE = re.compile(r"^(.+),\s*The$", re.IGNORECASE)


def _normalize_artist_name(artist: str) -> str | None:
    """"Beatles, The" -> "The Beatles" - a sort-friendly inversion this library's catalog uses
    for a handful of artists, which lrclib's own matching doesn't undo: looking it up verbatim
    reports a well-known track as not found. Returns None if the name isn't in that form."""
    match = _SORT_SUFFIX_RE.match(artist.strip())
    if not match:
        return None
    return f"The {match.group(1)}"


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


def _lrclib_get(
    artist: str, title: str, album: str | None, duration_ms: int | None
) -> tuple[str | None, str | None, bool, bool]:
    """A single lrclib lookup attempt. Returns (plain_text, synced_lrc, transient_error,
    not_found) - not_found is a clean 404 (no such track), as opposed to transient_error
    (rate limited, network/timeout, 5xx)."""
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
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, None, False, True
        log.debug("lrclib lookup failed for %s - %s", artist, title, exc_info=True)
        return None, None, True, False
    except Exception:
        log.debug("lrclib lookup failed for %s - %s", artist, title, exc_info=True)
        return None, None, True, False

    return data.get("plainLyrics") or None, data.get("syncedLyrics") or None, False, False


def fetch_online_lyrics(
    artist: str, title: str, album: str | None = None, duration_ms: int | None = None
) -> tuple[str | None, str | None, bool]:
    """Looks up lyrics from lrclib.net's free, keyless API - used when the file itself has none
    embedded. Returns (plain_text, synced_lrc, transient_error): synced_lrc is lrclib's own
    timestamped "[mm:ss.xx] line" format when it has one (not every track does), for a karaoke-
    style display; transient_error is True for a retry-able failure (rate limited, network/
    timeout, 5xx) as opposed to lrclib's own 404 "no such track" - callers need that distinction
    so a rate limit doesn't get cached forever as "no lyrics found". A clean 404 gets one retry
    with a normalized artist name (see _normalize_artist_name) before giving up."""
    if not artist or not title:
        return None, None, False

    plain, synced, transient_error, not_found = _lrclib_get(artist, title, album, duration_ms)
    if plain or transient_error or not not_found:
        return plain, synced, transient_error

    normalized = _normalize_artist_name(artist)
    if not normalized:
        return None, None, False

    plain, synced, transient_error, _ = _lrclib_get(normalized, title, album, duration_ms)
    return plain, synced, transient_error


def get_lyrics(row) -> tuple[str | None, str, str | None]:
    """Embedded tags first, then lrclib as a fallback. Returns (text, source, synced) where
    source is "embedded", "online", "none" (no lyrics found anywhere), or "error" (the online
    lookup failed transiently - callers should not treat this the same as a confirmed "none").
    synced is only ever set alongside source="online" - embedded tags don't carry timestamps."""
    if row["file_path"]:
        embedded = extract_embedded_lyrics(row["file_path"])
        if embedded:
            return embedded, "embedded", None

    online, synced, transient_error = fetch_online_lyrics(
        row["artist"], row["title"], row["album"], row["duration_ms"]
    )
    if online:
        return online, "online", synced
    if transient_error:
        return None, "error", None

    return None, "none", None

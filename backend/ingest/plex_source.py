from __future__ import annotations

import logging
from collections.abc import Iterator

from plexapi.server import PlexServer

from backend.config import settings
from backend.db import Track

log = logging.getLogger(__name__)


def remap_plex_path(path: str | None) -> str | None:
    """Translate a Plex-reported file path (the server's own filesystem view, e.g. a NAS path
    like /volume1/plexdata/music/...) into a path relative to SHARED_DRIVE_PATH, using
    PLEX_REMOTE_MUSIC_PATH as the prefix to strip. Stored relative (not re-prefixed with the
    local root) so the catalog stays portable across machines with different roots. Leaves the
    path untouched if no mapping is configured or it doesn't match the configured prefix."""
    if (
        not path
        or not settings.plex_remote_music_path
        or not settings.shared_drive_path
    ):
        return path
    remote_prefix = settings.plex_remote_music_path.rstrip("/\\")
    normalized = path.replace("\\", "/")
    if not normalized.startswith(remote_prefix.replace("\\", "/")):
        return path
    return normalized[len(remote_prefix) :].lstrip("/")


def iter_plex_tracks(baseurl: str, token: str) -> Iterator[Track]:
    """Yield a Track for every audio track in every music library section on the Plex server."""
    plex = PlexServer(baseurl, token)
    music_sections = [s for s in plex.library.sections() if s.type == "artist"]

    for section in music_sections:
        for plex_track in section.searchTracks():
            file_path = None
            try:
                file_path = remap_plex_path(plex_track.media[0].parts[0].file)
            except (IndexError, AttributeError):
                log.debug("No file path for Plex track %s", plex_track.ratingKey)

            yield Track(
                external_id=f"plex:{plex_track.ratingKey}",
                source="plex",
                plex_rating_key=str(plex_track.ratingKey),
                file_path=file_path,
                title=plex_track.title or "",
                artist=plex_track.grandparentTitle or plex_track.originalTitle or "",
                album=plex_track.parentTitle or "",
                genre=_first_tag(plex_track, "genres"),
                mood=_first_tag(plex_track, "moods"),
                year=getattr(plex_track, "parentYear", None)
                or getattr(plex_track, "year", None),
                # Plex's own track/disc position within the album (index/parentIndex).
                disc_number=getattr(plex_track, "parentIndex", None),
                track_number=getattr(plex_track, "index", None),
                duration_ms=plex_track.duration,
                tag_source="metadata" if _first_tag(plex_track, "genres") else "none",
            )


def _first_tag(plex_track, attr: str) -> str | None:
    tags = getattr(plex_track, attr, None) or []
    if not tags:
        return None
    return tags[0].tag

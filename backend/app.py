from __future__ import annotations

import base64
import json
import logging
import secrets
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from backend.art import extract_album_art
from backend.config import settings
from backend.db import (
    add_client_log,
    add_track_to_playlist,
    connection,
    create_playlist,
    delete_playlist,
    get_now_playing,
    get_playlist,
    get_track,
    init_db,
    list_client_logs,
    list_playlists,
    list_recommendations,
    record_play_event,
    rename_playlist,
    set_ai_background,
    set_album,
    set_artist,
    set_favorite,
    set_lyrics,
    set_now_playing,
    set_playlist_tracks,
    set_recommendation_status,
    set_tags,
    set_tags_list,
    set_title,
    set_youtube_video_id,
    tags_list,
    tracks_missing_identity,
)
from backend.info import get_track_background
from backend.lyrics import get_lyrics
from backend.youtube import rank_matches as rank_youtube_matches
from backend.paths import resolve as resolve_file_path
from backend.search.embeddings import reindex_embeddings
from backend.search.engine import album_tracks as run_album_tracks
from backend.search.engine import album_tracks_by_name as run_album_tracks_by_name
from backend.search.engine import discography as run_discography
from backend.search.engine import playlist_tracks as run_playlist_tracks
from backend.search.engine import search as run_search
from backend.search.engine import search_by_tag as run_search_by_tag
from backend.search.engine import similar as run_similar
from backend.search.engine import suggest_for_playlist as run_suggest_for_playlist
from backend.search.engine import versions as run_versions
from backend.stats import (
    favorites,
    most_played,
    most_skipped,
    never_played,
    recently_added,
    recently_played,
)
from backend.tagging.cloud_tagger import suggest_tags

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

# Python's stdlib mimetypes module guesses legacy/non-standard types for some of these
# (e.g. .flac -> audio/x-flac) that Chrome's <audio> element won't recognize as playable,
# silently hanging in the "loading" state forever instead of erroring. Override with the
# modern IANA-registered types browsers actually expect.
MEDIA_TYPE_OVERRIDES = {
    ".flac": "audio/flac",
    ".wma": "audio/x-ms-wma",
    ".aac": "audio/aac",
}

app = FastAPI(title="pan")


@app.middleware("http")
async def require_auth(request, call_next):
    """Single-user HTTP Basic auth gate - a no-op when PAN_AUTH_USER/PASSWORD aren't set, so
    LAN-only use (`make run`) is unaffected. Once the app is reachable beyond the LAN (e.g. over
    Tailscale), set both so a visitor can't browse the library or trigger the Claude-billed
    reindex. /stream/* is exempt: Chromecast's receiver fetches it directly and can't supply
    credentials, and it's already LAN-trust-only per the Makefile's port-8080 plain-HTTP path."""
    if (
        settings.pan_auth_user
        and settings.pan_auth_password
        and not request.url.path.startswith("/stream/")
    ):
        valid = False
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8")
                user, _, password = decoded.partition(":")
                valid = secrets.compare_digest(
                    user, settings.pan_auth_user
                ) and secrets.compare_digest(password, settings.pan_auth_password)
            except Exception:
                valid = False
        if not valid:
            return Response(
                status_code=401, headers={"WWW-Authenticate": 'Basic realm="pan"'}
            )
    return await call_next(request)


_reindex_lock = threading.Lock()
_reindex_status = {"running": False, "last_result": None, "error": None}


with connection() as _conn:
    init_db(_conn)


@app.get("/search")
def search_endpoint(q: str, limit: int = 25, offset: int = 0):
    with connection() as conn:
        return {"results": run_search(conn, q, limit=limit, offset=offset)}


@app.get("/search/by-tag")
def search_by_tag_endpoint(field: str, value: str, limit: int = 25, offset: int = 0):
    if field not in ("genre", "mood", "tag"):
        raise HTTPException(
            status_code=400, detail="field must be one of: genre, mood, tag"
        )
    with connection() as conn:
        return {
            "results": run_search_by_tag(conn, field, value, limit=limit, offset=offset)
        }


@app.get("/track/{track_id}/similar")
def similar_endpoint(track_id: int, limit: int = 25):
    with connection() as conn:
        if get_track(conn, track_id) is None:
            raise HTTPException(status_code=404, detail="Track not found")
        return {"results": run_similar(conn, track_id, limit=limit)}


@app.get("/track/{track_id}/album")
def album_endpoint(track_id: int):
    with connection() as conn:
        if get_track(conn, track_id) is None:
            raise HTTPException(status_code=404, detail="Track not found")
        return {"results": run_album_tracks(conn, track_id)}


@app.get("/artist/discography")
def discography_endpoint(artist: str):
    with connection() as conn:
        return {"albums": run_discography(conn, artist)}


@app.get("/artist/album")
def artist_album_endpoint(artist: str, album: str):
    with connection() as conn:
        return {"results": run_album_tracks_by_name(conn, artist, album)}


@app.get("/track/{track_id}/versions")
def versions_endpoint(track_id: int):
    with connection() as conn:
        if get_track(conn, track_id) is None:
            raise HTTPException(status_code=404, detail="Track not found")
        return {"results": run_versions(conn, track_id)}


class PlaylistCreate(BaseModel):
    name: str
    track_ids: list[int]


class PlaylistRename(BaseModel):
    name: str


@app.get("/playlists")
def list_playlists_endpoint():
    with connection() as conn:
        rows = list_playlists(conn)
        return {"playlists": [dict(row) for row in rows]}


@app.post("/playlists")
def create_playlist_endpoint(body: PlaylistCreate):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Playlist name is required")
    with connection() as conn:
        playlist_id = create_playlist(conn, name, body.track_ids)
        conn.commit()
    return {"id": playlist_id, "name": name}


class PlaylistAddTrack(BaseModel):
    track_id: int


@app.post("/playlists/{playlist_id}/tracks")
def add_track_to_playlist_endpoint(playlist_id: int, body: PlaylistAddTrack):
    with connection() as conn:
        if get_playlist(conn, playlist_id) is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        if get_track(conn, body.track_id) is None:
            raise HTTPException(status_code=404, detail="Track not found")
        add_track_to_playlist(conn, playlist_id, body.track_id)
        conn.commit()
    return {"status": "ok"}


class PlaylistTracksUpdate(BaseModel):
    track_ids: list[int]


@app.put("/playlists/{playlist_id}/tracks")
def update_playlist_tracks_endpoint(playlist_id: int, body: PlaylistTracksUpdate):
    """Replaces the playlist's full track list - used by "Save as playlist" when the current
    queue was loaded from this same playlist, to update it in place rather than always creating
    a new one."""
    with connection() as conn:
        if get_playlist(conn, playlist_id) is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        set_playlist_tracks(conn, playlist_id, body.track_ids)
        conn.commit()
    return {"id": playlist_id, "track_count": len(body.track_ids)}


@app.get("/playlists/{playlist_id}")
def get_playlist_endpoint(playlist_id: int):
    with connection() as conn:
        playlist = get_playlist(conn, playlist_id)
        if playlist is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        return {
            "id": playlist["id"],
            "name": playlist["name"],
            "results": run_playlist_tracks(conn, playlist_id),
        }


@app.get("/playlists/{playlist_id}/suggest")
def suggest_for_playlist_endpoint(playlist_id: int, limit: int = 25):
    with connection() as conn:
        if get_playlist(conn, playlist_id) is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        return {"results": run_suggest_for_playlist(conn, playlist_id, limit=limit)}


@app.patch("/playlists/{playlist_id}")
def rename_playlist_endpoint(playlist_id: int, body: PlaylistRename):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Playlist name is required")
    with connection() as conn:
        if get_playlist(conn, playlist_id) is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        rename_playlist(conn, playlist_id, name)
        conn.commit()
    return {"id": playlist_id, "name": name}


@app.delete("/playlists/{playlist_id}")
def delete_playlist_endpoint(playlist_id: int):
    with connection() as conn:
        if get_playlist(conn, playlist_id) is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        delete_playlist(conn, playlist_id)
        conn.commit()
    return {"status": "deleted"}


class PlayEvent(BaseModel):
    percent_listened: float


@app.post("/track/{track_id}/play-event")
def play_event_endpoint(track_id: int, event: PlayEvent):
    with connection() as conn:
        if get_track(conn, track_id) is None:
            raise HTTPException(status_code=404, detail="Track not found")
        record_play_event(conn, track_id, event.percent_listened)
        conn.commit()
    return {"status": "ok"}


@app.get("/stats/most-played")
def most_played_endpoint(limit: int = 25, offset: int = 0):
    with connection() as conn:
        return {"results": most_played(conn, limit, offset)}


@app.get("/stats/never-played")
def never_played_endpoint(limit: int = 25, offset: int = 0):
    with connection() as conn:
        return {"results": never_played(conn, limit, offset)}


@app.get("/stats/most-skipped")
def most_skipped_endpoint(limit: int = 25, offset: int = 0):
    with connection() as conn:
        return {"results": most_skipped(conn, limit, offset)}


@app.get("/stats/recently-played")
def recently_played_endpoint(limit: int = 25, offset: int = 0):
    with connection() as conn:
        return {"results": recently_played(conn, limit, offset)}


@app.get("/stats/recently-added")
def recently_added_endpoint(limit: int = 25, offset: int = 0):
    with connection() as conn:
        return {"results": recently_added(conn, limit, offset)}


@app.get("/stats/favorites")
def favorites_endpoint(limit: int = 25, offset: int = 0):
    with connection() as conn:
        return {"results": favorites(conn, limit, offset)}


@app.get("/now-playing")
def now_playing_endpoint():
    with connection() as conn:
        row = get_now_playing(conn)
    if row is None:
        return {"state": None, "updated_at": None}
    return {"state": json.loads(row["state"]), "updated_at": row["updated_at"]}


@app.put("/now-playing")
def update_now_playing_endpoint(state: dict):
    with connection() as conn:
        set_now_playing(conn, json.dumps(state))
        conn.commit()
    return {"status": "ok"}


client_log = logging.getLogger("client")


@app.post("/client-log")
def client_log_endpoint(entry: dict):
    """Diagnostic sink for the player's own event log (see ui/app.js's logClient). Persisted
    (not just printed to the container's own log output) since reaching that on a NAS deployment
    means hunting through Container Manager - the admin panel's "Client logs" button reads this
    same table directly instead. Fire-and-forget on the client side; never blocks or breaks
    playback if this fails."""
    event = entry.get("event", "?")
    detail = entry.get("detail", {})
    client_log.info("%s %s", event, detail)
    with connection() as conn:
        add_client_log(conn, event, json.dumps(detail))
        conn.commit()
    return {"status": "ok"}


@app.get("/admin/client-logs")
def client_logs_endpoint(limit: int = 500):
    with connection() as conn:
        rows = list_client_logs(conn, limit)
    return {
        "logs": [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "event": row["event"],
                "detail": json.loads(row["detail"]) if row["detail"] else {},
            }
            for row in rows
        ]
    }


@app.get("/admin/client-logs/download")
def download_client_logs():
    with connection() as conn:
        rows = list_client_logs(conn, limit=5000)
    # Chronological (oldest first) for the download, unlike the viewer's newest-first list -
    # that's the order a log file is normally read in.
    lines = [
        f"{row['created_at']}  {row['event']}  {row['detail']}"
        for row in reversed(rows)
    ]
    return Response(
        content="\n".join(lines),
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=pan-client-logs.txt"},
    )


@app.get("/admin/recommendations")
def recommendations_endpoint():
    """Library-improvement suggestions (backend/recommend.py) from the most recent reindex -
    there's no separate refresh here; see that module's docstring for why."""
    with connection() as conn:
        rows = list_recommendations(conn)
    return {
        "generated_at": rows[0]["created_at"] if rows else None,
        "recommendations": [
            {
                "id": row["id"],
                "category": row["category"],
                "artist": row["artist"],
                "reasoning": row["reasoning"],
                "detail": json.loads(row["detail"]) if row["detail"] else None,
                "status": row["status"],
            }
            for row in rows
        ],
    }


class RecommendationStatusUpdate(BaseModel):
    status: str


@app.put("/admin/recommendations/{rec_id}/status")
def update_recommendation_status(rec_id: int, body: RecommendationStatusUpdate):
    if body.status not in ("pending", "accepted", "dismissed"):
        raise HTTPException(status_code=400, detail="Invalid status")
    with connection() as conn:
        set_recommendation_status(conn, rec_id, body.status)
        conn.commit()
    return {"ok": True}


@app.post("/admin/reindex")
def trigger_reindex():
    if _reindex_lock.locked():
        return {"status": "already_running"}

    def _run():
        with _reindex_lock:
            _reindex_status["running"] = True
            _reindex_status["error"] = None
            try:
                from scripts.reindex import main as reindex_main

                reindex_main()
                _reindex_status["last_result"] = "ok"
            except Exception as exc:
                log.exception("Reindex failed")
                _reindex_status["error"] = str(exc)
            finally:
                _reindex_status["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


@app.get("/admin/reindex/status")
def reindex_status():
    return _reindex_status


@app.get("/admin/missing-metadata")
def missing_metadata_endpoint():
    """Tracks with no usable title or artist - otherwise invisible in the app, since semantic
    search has no title/artist text to match a query against. Lets them be browsed and fixed
    via the normal Edit tags panel instead of only being found by querying the database."""
    with connection() as conn:
        rows = tracks_missing_identity(conn)
        return {
            "results": [
                {
                    "id": row["id"],
                    "title": row["title"],
                    "artist": row["artist"],
                    "album": row["album"],
                    "genre": row["genre"],
                    "mood": row["mood"],
                    "tags": tags_list(row),
                    "year": row["year"],
                    "source": row["source"],
                    "file_path": row["file_path"],
                    "plex_rating_key": row["plex_rating_key"],
                    "favorite": bool(row["favorited_at"]),
                }
                for row in rows
            ]
        }


@app.get("/stream/{track_id}")
def stream(track_id: int):
    """Stream a track's audio. Only works for tracks with a locally-reachable file path
    (local-drive tracks, or Plex tracks whose underlying file happens to be reachable at the
    same path on this machine). Plex-only tracks without a reachable path aren't playable here
    yet - see README for the Plex direct-play limitation."""
    with connection() as conn:
        row = get_track(conn, track_id)
    if row is None or not row["file_path"]:
        raise HTTPException(status_code=404, detail="No playable file for this track")
    path = resolve_file_path(row["file_path"])
    media_type = MEDIA_TYPE_OVERRIDES.get(path.suffix.lower())
    return FileResponse(path, media_type=media_type)


class FavoriteUpdate(BaseModel):
    favorite: bool


@app.patch("/track/{track_id}/favorite")
def update_favorite(track_id: int, update: FavoriteUpdate):
    with connection() as conn:
        if get_track(conn, track_id) is None:
            raise HTTPException(status_code=404, detail="Track not found")
        set_favorite(conn, track_id, update.favorite)
        conn.commit()
    return {"id": track_id, "favorite": update.favorite}


@app.get("/track/{track_id}/art")
def track_art(track_id: int):
    with connection() as conn:
        row = get_track(conn, track_id)
    if row is None or not row["file_path"]:
        raise HTTPException(status_code=404, detail="No art available")
    art = extract_album_art(row["file_path"])
    if art is None:
        raise HTTPException(status_code=404, detail="No embedded art")
    data, mime = art
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "public, max-age=86400"},
    )


class TagUpdate(BaseModel):
    genre: str | None = None
    mood: str | None = None
    tags: list[str] | None = None
    artist: str | None = None
    title: str | None = None
    album: str | None = None


@app.get("/track/{track_id}/suggest-tags")
def suggest_tags_endpoint(track_id: int):
    with connection() as conn:
        row = get_track(conn, track_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Track not found")
        return suggest_tags(row)


@app.get("/track/{track_id}")
def track_detail(track_id: int):
    with connection() as conn:
        row = get_track(conn, track_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Track not found")

    file_size = None
    file_format = None
    resolved_path = None
    if row["file_path"]:
        path = resolve_file_path(row["file_path"])
        file_format = path.suffix.lstrip(".").upper() or None
        resolved_path = str(path)
        try:
            file_size = path.stat().st_size
        except OSError:
            pass

    return {
        "id": row["id"],
        "title": row["title"],
        "artist": row["artist"],
        "album": row["album"],
        "year": row["year"],
        "genre": row["genre"],
        "mood": row["mood"],
        "tags": tags_list(row),
        "favorite": bool(row["favorited_at"]),
        "tag_source": row["tag_source"],
        "tempo": row["tempo"],
        "energy": row["energy"],
        "musical_key": row["musical_key"],
        "duration_ms": row["duration_ms"],
        "source": row["source"],
        "file_format": file_format,
        "file_size_bytes": file_size,
        "file_path": resolved_path,
    }


@app.get("/track/{track_id}/about")
def track_about(track_id: int, refresh: bool = False):
    """Cached indefinitely once generated (ai_background), since it costs an API call - refresh
    bypasses that cache and overwrites it, for when the cached text is stale (e.g. it was
    generated before a mislabeled artist/title got corrected) or just wasn't a good writeup."""
    with connection() as conn:
        row = get_track(conn, track_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Track not found")
        if row["ai_background"] and not refresh:
            return {"text": row["ai_background"], "cached": True}

        text = get_track_background(row)
        if text:
            set_ai_background(conn, track_id, text)
            conn.commit()
        return {"text": text, "cached": False}


class AboutUpdate(BaseModel):
    text: str


@app.patch("/track/{track_id}/about")
def update_track_about(track_id: int, update: AboutUpdate):
    """Lets a user hand-write or correct the "tell me more" writeup directly, same as a manual
    tag correction - useful when there's no ANTHROPIC_API_KEY configured, or the AI's answer was
    close but not quite right."""
    with connection() as conn:
        if get_track(conn, track_id) is None:
            raise HTTPException(status_code=404, detail="Track not found")
        set_ai_background(conn, track_id, update.text)
        conn.commit()
    return {"text": update.text}


@app.get("/track/{track_id}/lyrics")
def track_lyrics(track_id: int, refresh: bool = False):
    """Cached indefinitely once looked up (lyrics column: NULL = not yet looked up, '' = looked
    up and none found anywhere) - refresh bypasses that cache to retry, e.g. after a mislabeled
    artist/title got corrected."""
    with connection() as conn:
        row = get_track(conn, track_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Track not found")
        if row["lyrics"] is not None and not refresh:
            return {
                "text": row["lyrics"] or None,
                "cached": True,
                "synced": row["synced_lyrics"],
            }

        text, source, synced = get_lyrics(row)
        if source != "error":
            set_lyrics(conn, track_id, text or "", synced)
            conn.commit()
        return {"text": text, "cached": False, "source": source, "synced": synced}


@app.get("/track/{track_id}/video")
def track_video(track_id: int, refresh: bool = False):
    """Cached indefinitely once looked up (youtube_video_id column: NULL = not yet looked up,
    '' = looked up, the text search itself found nothing) - refresh bypasses that cache to retry,
    e.g. after a mislabeled artist/title got corrected.

    video_id is for video mode's embedded player - may go empty over time as dismiss works
    through youtube_candidates (some tracks have every candidate blocked from embedding by a
    rights holder's Content ID policy, undetectable in advance). watch_id is the original best
    match regardless, for a direct link to the right video on youtube.com even once video_id
    has given up - embedding restrictions don't affect watching on YouTube's own site."""
    with connection() as conn:
        row = get_track(conn, track_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Track not found")
        if row["youtube_video_id"] is not None and not refresh:
            return {
                "video_id": row["youtube_video_id"] or None,
                "watch_id": row["youtube_watch_id"] or None,
                "cached": True,
            }

        ranked = rank_youtube_matches(row)
        video_id = ranked[0] if ranked else ""
        set_youtube_video_id(
            conn, track_id, video_id, json.dumps(ranked[1:]), watch_id=video_id
        )
        conn.commit()
        return {
            "video_id": video_id or None,
            "watch_id": video_id or None,
            "cached": False,
        }


@app.delete("/track/{track_id}/video")
def dismiss_track_video(track_id: int):
    """The current match's actual embed turned out broken (the YouTube API's "embeddable" flag
    isn't fully reliable - see backend/youtube.py) - tries the next-best candidate from that same
    lookup (cached in youtube_candidates, so this costs no extra search.list call), or settles on
    "no good match" once those run out too."""
    with connection() as conn:
        row = get_track(conn, track_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Track not found")
        candidates = json.loads(row["youtube_candidates"] or "[]")
        next_id = candidates[0] if candidates else ""
        set_youtube_video_id(conn, track_id, next_id, json.dumps(candidates[1:]))
        conn.commit()
    return {"video_id": next_id or None}


class LyricsUpdate(BaseModel):
    text: str


@app.patch("/track/{track_id}/lyrics")
def update_track_lyrics(track_id: int, update: LyricsUpdate):
    """Lets a user hand-paste lyrics directly - useful when lrclib doesn't have the track, or is
    rate-limited. Clears any previously cached synced (LRC) lyrics, since hand-pasted plain text
    has no timestamps and would otherwise display alongside stale ones from an earlier lookup."""
    with connection() as conn:
        if get_track(conn, track_id) is None:
            raise HTTPException(status_code=404, detail="Track not found")
        set_lyrics(conn, track_id, update.text, None)
        conn.commit()
    return {"text": update.text}


@app.patch("/track/{track_id}/tags")
def update_tags(track_id: int, update: TagUpdate):
    """User-corrected tags always win over local/cloud inference - future reindex passes never
    touch a track that already has a non-null genre/mood, so this sticks. Re-embeds immediately
    so the correction affects search right away rather than waiting for the next full reindex."""
    with connection() as conn:
        row = get_track(conn, track_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Track not found")
        genre = update.genre if update.genre is not None else row["genre"]
        mood = update.mood if update.mood is not None else row["mood"]
        set_tags(conn, track_id, genre, mood, "manual")
        if update.tags is not None:
            set_tags_list(conn, track_id, update.tags)
        # Blank/whitespace means "didn't type a correction", not "clear the field" - a
        # mislabeled/missing artist, title, or album (e.g. a compilation track credited to
        # "Various Artists", or named only from a messy filename) is worth fixing by hand, but
        # there's no reason to ever want any of them wiped to empty.
        if update.artist and update.artist.strip():
            set_artist(conn, track_id, update.artist.strip())
        if update.title and update.title.strip():
            set_title(conn, track_id, update.title.strip())
        if update.album and update.album.strip():
            set_album(conn, track_id, update.album.strip())
        conn.commit()
        updated = get_track(conn, track_id)
        reindex_embeddings(conn, [updated])
    return {
        "id": track_id,
        "genre": updated["genre"],
        "mood": updated["mood"],
        "tags": tags_list(updated),
        "artist": updated["artist"],
        "title": updated["title"],
        "album": updated["album"],
    }


app.mount("/static", StaticFiles(directory="ui"), name="static")


@app.middleware("http")
async def no_store_static(request, call_next):
    """This is a local personal tool whose ui/ files are edited directly on disk - never let
    the browser cache them, so a UI change is guaranteed to show up on next reload instead of
    silently serving a stale cached app.js/style.css."""
    response = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def index():
    html = Path("ui/index.html").read_text(encoding="utf-8")
    version = str(int(Path("ui/app.js").stat().st_mtime))
    html = html.replace('src="/static/app.js"', f'src="/static/app.js?v={version}"')
    return HTMLResponse(html)


@app.get("/cert.pem")
def download_cert():
    """Serves the self-signed TLS cert's PUBLIC half only (never the private key) so a phone on
    the LAN can grab it over the same connection it's already using, instead of emailing/AirDropping
    a file around. Installing this as a trusted CA (not just clicking through the browser warning)
    is what PWA installability actually requires - see the conversation for why."""
    pem = Path("certs/cert.pem").read_text(encoding="utf-8")
    return Response(
        content=pem,
        media_type="application/x-pem-file",
        headers={"Content-Disposition": "attachment; filename=pan-cert.pem"},
    )


@app.get("/sw.js")
def service_worker():
    """Served from the root path (not /static/) so its default scope covers the whole app, not
    just /static/* - otherwise it would never see fetches made by the "/" page itself. Browsers
    are supposed to always revalidate a service worker script, but Cache-Control: no-cache makes
    that explicit rather than relying on that behavior being consistent across browsers."""
    js = Path("ui/sw.js").read_text(encoding="utf-8")
    return Response(
        content=js,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )

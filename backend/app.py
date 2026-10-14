from __future__ import annotations

import logging
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from backend.art import extract_album_art
from backend.db import (
    add_track_to_playlist,
    connection,
    create_playlist,
    delete_playlist,
    get_playlist,
    get_track,
    init_db,
    list_playlists,
    record_play_event,
    rename_playlist,
    set_ai_background,
    set_artist,
    set_favorite,
    set_tags,
    set_tags_list,
    set_title,
    tags_list,
)
from backend.info import get_track_background
from backend.paths import resolve as resolve_file_path
from backend.search.embeddings import reindex_embeddings
from backend.search.engine import album_tracks as run_album_tracks
from backend.search.engine import album_tracks_by_name as run_album_tracks_by_name
from backend.search.engine import discography as run_discography
from backend.search.engine import playlist_tracks as run_playlist_tracks
from backend.search.engine import search as run_search
from backend.search.engine import similar as run_similar
from backend.search.engine import versions as run_versions
from backend.stats import favorites, most_played, most_skipped, never_played, recently_played
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

_reindex_lock = threading.Lock()
_reindex_status = {"running": False, "last_result": None, "error": None}


with connection() as _conn:
    init_db(_conn)


@app.get("/search")
def search_endpoint(q: str, limit: int = 25, offset: int = 0):
    with connection() as conn:
        return {"results": run_search(conn, q, limit=limit, offset=offset)}


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


@app.get("/playlists/{playlist_id}")
def get_playlist_endpoint(playlist_id: int):
    with connection() as conn:
        playlist = get_playlist(conn, playlist_id)
        if playlist is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        return {"id": playlist["id"], "name": playlist["name"], "results": run_playlist_tracks(conn, playlist_id)}


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
def most_played_endpoint(limit: int = 25):
    with connection() as conn:
        return {"results": most_played(conn, limit)}


@app.get("/stats/never-played")
def never_played_endpoint(limit: int = 25):
    with connection() as conn:
        return {"results": never_played(conn, limit)}


@app.get("/stats/most-skipped")
def most_skipped_endpoint(limit: int = 25):
    with connection() as conn:
        return {"results": most_skipped(conn, limit)}


@app.get("/stats/recently-played")
def recently_played_endpoint(limit: int = 25):
    with connection() as conn:
        return {"results": recently_played(conn, limit)}


@app.get("/stats/favorites")
def favorites_endpoint(limit: int = 200):
    with connection() as conn:
        return {"results": favorites(conn, limit)}


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
    return Response(content=data, media_type=mime, headers={"Cache-Control": "public, max-age=86400"})


class TagUpdate(BaseModel):
    genre: str | None = None
    mood: str | None = None
    tags: list[str] | None = None
    artist: str | None = None
    title: str | None = None


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
def track_about(track_id: int):
    with connection() as conn:
        row = get_track(conn, track_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Track not found")
        if row["ai_background"]:
            return {"text": row["ai_background"], "cached": True}

        text = get_track_background(row)
        if text:
            set_ai_background(conn, track_id, text)
            conn.commit()
        return {"text": text, "cached": False}


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
        # mislabeled/missing artist or title (e.g. a compilation track credited to "Various
        # Artists", or named only from a messy filename) is worth fixing by hand, but there's
        # no reason to ever want either wiped to empty.
        if update.artist and update.artist.strip():
            set_artist(conn, track_id, update.artist.strip())
        if update.title and update.title.strip():
            set_title(conn, track_id, update.title.strip())
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

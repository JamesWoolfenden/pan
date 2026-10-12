from __future__ import annotations

import logging
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from backend.db import connection, get_track, init_db, set_tags
from backend.info import get_track_background
from backend.search.embeddings import reindex_embeddings
from backend.search.engine import album_tracks as run_album_tracks
from backend.search.engine import search as run_search
from backend.search.engine import similar as run_similar
from backend.search.engine import versions as run_versions
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
def search_endpoint(q: str, limit: int = 25):
    with connection() as conn:
        return {"results": run_search(conn, q, limit=limit)}


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


@app.get("/track/{track_id}/versions")
def versions_endpoint(track_id: int):
    with connection() as conn:
        if get_track(conn, track_id) is None:
            raise HTTPException(status_code=404, detail="Track not found")
        return {"results": run_versions(conn, track_id)}


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
    media_type = MEDIA_TYPE_OVERRIDES.get(Path(row["file_path"]).suffix.lower())
    return FileResponse(row["file_path"], media_type=media_type)


class TagUpdate(BaseModel):
    genre: str | None = None
    mood: str | None = None


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
    if row["file_path"]:
        path = Path(row["file_path"])
        file_format = path.suffix.lstrip(".").upper() or None
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
        "tag_source": row["tag_source"],
        "tempo": row["tempo"],
        "energy": row["energy"],
        "musical_key": row["musical_key"],
        "duration_ms": row["duration_ms"],
        "source": row["source"],
        "file_format": file_format,
        "file_size_bytes": file_size,
    }


@app.get("/track/{track_id}/about")
def track_about(track_id: int):
    with connection() as conn:
        row = get_track(conn, track_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Track not found")
        return {"text": get_track_background(row)}


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
        conn.commit()
        updated = get_track(conn, track_id)
        reindex_embeddings(conn, [updated])
    return {"id": track_id, "genre": updated["genre"], "mood": updated["mood"]}


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

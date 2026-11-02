from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    plex_baseurl: str = ""
    plex_token: str = ""
    shared_drive_path: str = ""
    # Optional second root, scanned for video files only (concert footage, music videos - see
    # backend/ingest/local_source.py's VIDEO_EXTENSIONS) when they live on a separate drive/share
    # from the music library rather than in a subfolder under SHARED_DRIVE_PATH. Leave blank if
    # your videos already live somewhere under SHARED_DRIVE_PATH - that's scanned regardless and
    # needs no separate config, this is only for a genuinely different root.
    video_drive_path: str = ""
    # Plex reports file paths using the server's own (often different-OS) filesystem view,
    # e.g. a Synology NAS's "/volume1/plexdata/music/...". When that prefix corresponds to
    # SHARED_DRIVE_PATH as seen from this machine, set it here so Plex-sourced tracks get a
    # locally-reachable file_path (for playback) and correctly dedupe against the local scan
    # instead of being cataloged as unplayable duplicates.
    plex_remote_music_path: str = ""
    anthropic_api_key: str = ""
    cloud_tagging_max_per_run: int = 200
    # Grounds backend/recommend.py's discovery suggestions in real listener co-occurrence data
    # (Last.fm's artist.getSimilar) instead of purely Claude's own associations. Free, instant
    # signup at https://www.last.fm/api/account/create. Recommendations still work without it -
    # falls back to asking Claude directly, see recommend.py.
    lastfm_api_key: str = ""
    # Scrobbling (backend/scrobble.py) additionally needs the API account's shared secret and a
    # session key for your Last.fm user - get the key once with `uv run python scripts/lastfm_auth.py`.
    # Scrobbling is off unless all three are set.
    lastfm_api_secret: str = ""
    lastfm_session_key: str = ""
    # Grounds backend/youtube.py's video-mode matching in a real duration check rather than
    # trusting the top text-search result. Free Google Cloud API key with YouTube Data API v3
    # enabled - note its free quota is 10,000 units/day and a search costs 100, so ~100 track
    # lookups/day. Looked up lazily per-track (like lyrics/AI background), not backfilled.
    youtube_api_key: str = ""
    db_path: str = "pan.db"
    embedding_model: str = "all-MiniLM-L6-v2"
    # HTTP Basic Auth gate (backend/app.py) - leave both blank for LAN-only use where the
    # network itself is the trust boundary (e.g. `make run`). Set both once the app is reachable
    # beyond the LAN (e.g. over Tailscale), since otherwise nothing stops a visitor from
    # triggering the Claude-billed reindex or browsing the library.
    pan_auth_user: str = ""
    pan_auth_password: str = ""
    # Chromecast's receiver fetches /stream/{id} itself, so it needs a URL on the home LAN - the
    # tailnet address the page is served from isn't reachable by it. Set to the LAN-published
    # stream-only container (docker-compose.yml's pan-stream), e.g. http://192.168.1.50:8080.
    # Blank = the UI falls back to guessing from the page's own origin (fine for `make run`).
    cast_stream_origin: str = ""
    # Serve nothing but /stream/* (404 for everything else) and don't touch the database at
    # startup. For the LAN-published pan-stream container, which must expose only audio streaming.
    pan_stream_only: bool = False

    @property
    def db_file(self) -> Path:
        return Path(self.db_path)


settings = Settings()

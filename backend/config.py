from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    plex_baseurl: str = ""
    plex_token: str = ""
    shared_drive_path: str = ""
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

    @property
    def db_file(self) -> Path:
        return Path(self.db_path)


settings = Settings()

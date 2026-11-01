"""One-time setup for scrobbling: exchanges your Last.fm username + password for a permanent
session key and prints the line to add to .env. Needs LASTFM_API_KEY and LASTFM_API_SECRET
already set there (https://www.last.fm/api/accounts). The password is only sent to Last.fm for
this exchange and is not stored.

    uv run python scripts/lastfm_auth.py
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import lastfm  # noqa: E402
from backend.config import settings  # noqa: E402


def main() -> int:
    if not settings.lastfm_api_key or not settings.lastfm_api_secret:
        print("Set LASTFM_API_KEY and LASTFM_API_SECRET in .env first.")
        return 1
    username = input("Last.fm username: ").strip()
    password = getpass.getpass("Last.fm password: ")
    try:
        key = lastfm.get_mobile_session(username, password)
    except Exception as exc:  # noqa: BLE001
        print(f"Authentication failed: {exc}")
        return 1
    print("\nAdd this to .env, then restart pan:\n")
    print(f"LASTFM_SESSION_KEY={key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

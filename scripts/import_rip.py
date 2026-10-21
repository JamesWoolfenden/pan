"""Run this after EAC (Exact Audio Copy) finishes ripping a CD to SHARED_DRIVE_PATH (Z:\\music).

EAC already does the actual ripping, FLAC encoding, and CDDB/MusicBrainz tagging - this script's
only job is telling the already-running pan server to pick up the new files, instead of waiting
for the next scheduled reindex or clicking "Reindex library" in the admin panel by hand.

Usage:
    uv run python -m scripts.import_rip
    uv run python -m scripts.import_rip --url http://localhost:8080
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

from backend.config import settings

DEFAULT_URL = "http://localhost:8080"
POLL_INTERVAL_SECONDS = 2


def trigger_reindex(base_url: str) -> None:
    auth = None
    if settings.pan_auth_user and settings.pan_auth_password:
        auth = (settings.pan_auth_user, settings.pan_auth_password)

    with httpx.Client(base_url=base_url, auth=auth, timeout=10) as client:
        response = client.post("/admin/reindex")
        response.raise_for_status()
        status = response.json()["status"]
        if status == "already_running":
            print("A reindex is already running - waiting for it to finish...")
        else:
            print("Reindex started...")

        while True:
            time.sleep(POLL_INTERVAL_SECONDS)
            status = client.get("/admin/reindex/status").json()
            if not status["running"]:
                break

        if status["error"]:
            print(f"Reindex failed: {status['error']}", file=sys.stderr)
            sys.exit(1)
        print("Reindex complete - the new rip should now show up in search.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"Base URL of the running pan server (default: {DEFAULT_URL})",
    )
    args = parser.parse_args()

    try:
        trigger_reindex(args.url)
    except httpx.ConnectError:
        print(
            f"Couldn't reach {args.url} - is `make run` (or the server) actually running?",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()

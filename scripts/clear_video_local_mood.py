"""One-off cleanup: clear the mood tag on local_video rows that got it from
backend.tagging.local_tagger's infer_mood() - a tempo/energy heuristic meant for music, which
scripts/reindex.py used to run on every track regardless of source (fixed alongside this script -
see reindex.py's to_process filter). extract_features() only reads the first 60s of a file, so
for a movie/TV episode that's almost always a quiet non-musical intro (logos, credits, dialogue):
low RMS energy plus whatever tempo the beat tracker guesses from non-rhythmic audio lands
squarely in infer_mood's "driving" bucket (tempo<=1000, energy<=0.5) nearly every time -
mechanically, not because the content has anything to do with driving.

Only clears mood where tag_source == "local" (this heuristic's own marker), so a cloud-tagged or
manually-corrected mood is left untouched. Genre is untouched either way - this heuristic never
set it. Clearing (rather than leaving it) makes the row eligible for cloud tagging again on the
next reindex (see db.tracks_missing_tags: mood IS NULL and tag_source != 'cloud'), which actually
looks at title/artist text and stands a real chance of describing a movie sensibly.

Safe to re-run: rows already cleared, or never set by this heuristic, are simply skipped.
"""

from __future__ import annotations

import logging

from backend.db import connection, init_db, set_tags

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("clear_video_local_mood")


def clear(conn) -> int:
    rows = conn.execute(
        "SELECT id, genre FROM tracks WHERE source = 'local_video' AND tag_source = 'local'"
    ).fetchall()
    for row in rows:
        set_tags(conn, row["id"], row["genre"], None, "none")
    return len(rows)


def main() -> None:
    with connection() as conn:
        init_db(conn)
        cleared = clear(conn)
        conn.commit()
    log.info("Cleared the heuristic 'driving' mood from %d video track(s)", cleared)


if __name__ == "__main__":
    main()

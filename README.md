# pan

AI-enabled search and tagging for a music library spread across a Plex server and a shared
drive of local files.

- **Ingests** your catalog from Plex (`python-plexapi`) and scans a shared drive for anything
  Plex doesn't already know about, deduplicating by file path.
- **Tags** gaps in genre/mood: first for free using local audio-feature analysis
  (`librosa`: tempo/energy → mood), then via the Claude API for anything still missing,
  capped per run to control cost.
- **Searches** with natural language: simple queries run against a local embedding index
  (`sentence-transformers` + `sqlite-vec`, fully offline); queries with negation or
  multi-constraint logic ("upbeat but not metal", "before 1990") get parsed into a structured
  filter by Claude first, then combined with the embedding search.

## Setup

```powershell
uv sync
copy .env.example .env
```

Edit `.env`:

- `PLEX_BASEURL` / `PLEX_TOKEN` — your Plex server URL and an auth token
  ([how to find your Plex token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)).
  Leave blank to skip Plex entirely.
- `SHARED_DRIVE_PATH` — folder to scan for local audio files not in Plex. Leave blank to skip.
- `ANTHROPIC_API_KEY` — enables cloud tagging fallback and complex query parsing. Without it,
  tagging stops after the local pass and search falls back to plain semantic matching.
- `CLOUD_TAGGING_MAX_PER_RUN` — safety cap on how many tracks get sent to Claude per reindex.

## Running

```powershell
make install   # uv sync
make reindex   # pull Plex + shared drive, extract features, tag, build search index
make run       # start the web app at http://localhost:8000
```

Or trigger a reindex from the UI itself ("Reindex library" button) — it runs in the
background and the button shows progress.

## Testing

```powershell
make test
```

Unit tests cover ingest merge/dedupe and the local tagging heuristics without hitting Plex,
the filesystem, or the Claude API.

## Known limitations

- **Playback**: only tracks with a locally-reachable file path can be streamed in the browser
  player. Plex-only tracks (no accessible file path from this machine) show up in search but
  aren't playable yet — adding direct-play via Plex's streaming API is a natural next step.
- **Audio feature extraction** only runs for tracks whose file is reachable from this machine;
  Plex tracks are only feature-analyzed if their underlying file path also resolves locally
  (e.g. the Plex library and the shared drive are the same mount).

## Deploying elsewhere

A `Dockerfile` is included for running this on a home server later — `docker build` it and
mount your shared drive + a volume for `pan.db`, passing the same env vars as `.env`.

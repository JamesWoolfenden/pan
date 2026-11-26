# Voice Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user control the pan player by voice (tap-to-talk, plus an optional hands-free mode gated by the wake word "pan") while riding a Zwift bike with Bluetooth headphones.

**Architecture:** Speech recognition runs in the browser (Web Speech API). A pure, unit-tested function turns a transcript into an intent; a small controller in the page maps intents onto the existing player controls. Anything that is not a local command goes through the existing `/search` flow and becomes the new queue. No server changes.

**Tech Stack:** Vanilla JS in `ui/` (classic scripts, no bundler), Node `node:test` for the pure parser, pytest wrapper so CI (`uv run pytest`) runs it.

**Spec:** `docs/superpowers/specs/2026-11-26-voice-control-design.md`

## Global Constraints

- Player stays browser-only; no server-to-player command channel, no new backend routes.
- No spoken replies in v1; feedback is an on-screen toast only.
- Hands-free mode is off by default and only acts on utterances starting with the wake word "pan".
- Existing keyboard/mouse control of the player must keep working unchanged.
- UI changes are run locally and shown to the user before anything under `ui/` is committed (project preference). Commit steps below are gated on that; never add attribution lines to commits.
- `ui/app.js` top-level `const`/`let` are visible to later classic scripts; new code goes in new files, with only the minimal refactor in Task 2 inside `app.js`.

## Review Focus

- Hands-free utterance without the wake word ("next" said to someone else) must do nothing.
- Wake word misheard as "pam" should still work; bare "pan" with no command does nothing.
- Browser without `SpeechRecognition`/`webkitSpeechRecognition`, or mic permission denied: mic button hidden or shows a toast, no thrown errors.
- Recognition ending by itself in hands-free mode (Chrome stops after silence) restarts; in tap mode it must not loop.
- "volume 150" or "volume -5" clamps to 0-100; "volume" alone is not a set-volume command.
- A voice search with zero results must not clear the current queue; toast says nothing was found.
- Empty or whitespace transcript is ignored.

---

### Task 1: Intent parser (pure function)

**Files:**

- Create: `ui/voice-intents.js`
- Create: `tests/voice_intents.test.mjs`
- Create: `tests/test_voice_intents.py` (pytest wrapper)

**Interfaces:**

- Produces: `parseIntent(transcript: string, opts?: { handsFree?: boolean }) -> Intent`, where
  `Intent` is one of `{type: "play"} | {type: "pause"} | {type: "next"} | {type: "prev"} |
  {type: "volume-delta", delta: number} | {type: "volume-set", percent: number} |
  {type: "mute"} | {type: "shuffle"} | {type: "repeat"} | {type: "favorite"} |
  {type: "dislike"} | {type: "search", query: string} | {type: "ignore"}`.
  Exposed as `window.PanVoice.parseIntent` in the browser and `module.exports.parseIntent` under Node
  (single file, UMD-style guard on `typeof module`).
- Constant `WAKE_WORDS = ["pan", "pam"]` in the same file.

- [ ] **Step 1: Write failing tests** in `tests/voice_intents.test.mjs` using `node:test` + `node:assert`, importing via `createRequire`. Assertions (all with `{handsFree:false}` unless stated):
  - `"next"`, `"skip"` -> `{type:"next"}`; `"previous"`, `"go back"` -> `{type:"prev"}`
  - `"pause"`, `"stop"` -> `{type:"pause"}`; `"play"`, `"resume"` -> `{type:"play"}`
  - `"louder"`, `"volume up"` -> `{type:"volume-delta", delta:10}`; `"quieter"`, `"volume down"` -> `delta:-10`
  - `"volume 60"`, `"set volume to 60"` -> `{type:"volume-set", percent:60}`; `"volume 150"` -> percent 100; `"volume"` alone -> not volume-set (falls to search)
  - `"mute"`, `"shuffle"`, `"repeat"`, `"love this"`/`"favorite"` -> favorite, `"dislike"` -> dislike
  - `"play something upbeat but not metal"` -> `{type:"search", query:"something upbeat but not metal"}`
  - `"  "` and `""` -> `{type:"ignore"}`; transcript is case-insensitive and trailing punctuation is ignored (`"Next."`)
  - `{handsFree:true}`: `"next"` -> ignore; `"pan next"` and `"Pam, next"` -> next; `"pan"` alone -> ignore; `"pan play some jazz"` -> search "some jazz"

- [ ] **Step 2: Run to confirm failure:** `node --test tests/voice_intents.test.mjs` — Expected: FAIL (module not found).

- [ ] **Step 3: Implement `parseIntent` in `ui/voice-intents.js`.** Normalise (lowercase, strip punctuation, collapse spaces); in hands-free mode require and strip a leading wake word first; match local intents by exact phrase tables before falling back to `search`. A leading "play " followed by more words becomes the search query; bare "play"/"resume" is `play`.

- [ ] **Step 4: Run tests** — `node --test tests/voice_intents.test.mjs` — Expected: all PASS.

- [ ] **Step 5: Add the pytest wrapper** `tests/test_voice_intents.py`: `test_voice_intents_node_suite()` runs `subprocess.run(["node","--test","tests/voice_intents.test.mjs"], check=True)`, skipping via `pytest.skip` if `shutil.which("node")` is None. Run `uv run pytest tests/test_voice_intents.py -v` — Expected: PASS.

- [ ] **Step 6: Commit** (parser and tests only; no UI behaviour yet, so not gated): `git add ui/voice-intents.js tests/voice_intents.test.mjs tests/test_voice_intents.py && git commit -m "feat: voice intent parser"`.

### Task 2: Make search awaitable

**Files:**

- Modify: `ui/app.js:1158-1180` (the `form` submit handler)

**Interfaces:**

- Produces: `async function runSearch(q: string): Promise<number>` (top-level in `app.js`), performing exactly what the submit handler does today for a non-empty trimmed `q`, resolving to the number of results. The submit handler becomes `e.preventDefault(); const q = input.value.trim(); if (q) runSearch(q);`.

- [ ] **Step 1:** Move the handler body into `runSearch(q)`, returning `data.results.length`. Behaviour of the form must be identical.
- [ ] **Step 2: Verify:** `uv run pytest` passes; run the app (`make run`) and confirm a typed search still renders results, Load more works, and history still records the query.

### Task 3: Voice controller, mic button, toast

**Files:**

- Create: `ui/voice.js`
- Modify: `ui/index.html` (add `<button id="voice-btn">` near `#queue-prev/#queue-playpause/#queue-next` at line ~119, a `<span id="voice-toast" role="status" aria-live="polite">`, and `<script src="/static/voice-intents.js"></script>` then `<script src="/static/voice.js"></script>` after the existing `app.js` script at line 332)
- Modify: `ui/style.css` (mic button, large touch target at least 48px; `.is-listening` state; toast that fades after ~2s)

**Interfaces:**

- Consumes: `PanVoice.parseIntent` (Task 1); `runSearch` (Task 2); existing globals from `app.js`: `playPauseBtn`, `nextBtn`, `prevBtn`, `shuffleBtn`, `repeatBtn`, `playAllBtn`, `queuePlayer`, `queue`, `currentIndex`, `lastResults`, `adjustVolumeBy(deltaPercent)`, `setQueueVolume(percent)`, `toggleMute()`.
- Produces: `executeIntent(intent: Intent): Promise<void>` and `showVoiceToast(text: string): void` in `voice.js`.

- [ ] **Step 1: Implement `executeIntent`.** Mapping: `play` clicks `playPauseBtn` only if `queuePlayer.paused`; `pause` only if not paused; `next`/`prev`/`shuffle`/`repeat` click the matching button; `volume-delta` -> `adjustVolumeBy`; `volume-set` -> `setQueueVolume`; `mute` -> `toggleMute`; `favorite`/`dislike` -> POST the same request `appendDiscoveryActions` sends (see `ui/app.js:2159-2163`) for `queue[currentIndex].id`, toast if nothing is playing; `search` -> `const n = await runSearch(query)`; if `n === 0` toast `No results for "<query>"` and leave the queue untouched, else `playAllBtn.click()`; `ignore` does nothing. Each action shows a toast ("Skipped", "Volume 60%", ...).
- [ ] **Step 2: Implement tap-to-talk.** `SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition`; if absent, hide `#voice-btn`. On click: `recognition.continuous = false`, `lang = navigator.language`, start; on `result`, pass the final transcript to `parseIntent(t, {handsFree:false})` then `executeIntent`; on `error` with `not-allowed`, toast "Microphone blocked"; `.is-listening` class follows start/end. Tapping while listening stops it.
- [ ] **Step 3: Verify locally** (no commit yet): run `make run`, open on the user's device, tap the mic and try "pause", "next", "volume 40", "play something upbeat". Expected: each acts and toasts; denied-mic shows the toast without console errors.

### Task 4: Hands-free mode

**Files:**

- Modify: `ui/voice.js`, `ui/index.html` (a toggle next to `#voice-btn`, `id="voice-handsfree"`, `aria-pressed`), `ui/style.css`

**Interfaces:**

- Consumes: Task 3's `executeIntent`, `showVoiceToast`, recogniser setup.
- Produces: persisted preference `localStorage["pan.voiceHandsFree"]` (read/write inside try/catch, like `QUEUE_VOLUME_KEY` at `ui/app.js:779`).

- [ ] **Step 1: Implement.** When on, use `recognition.continuous = true`, `parseIntent(t, {handsFree:true})`, and restart on `end` after a short delay unless the user turned it off or permission was denied. Off by default. Turning it off stops recognition; tap-to-talk keeps working either way and never auto-restarts.
- [ ] **Step 2: Verify locally:** with hands-free on, "next" alone does nothing; "pan next" skips; leaving it idle for a minute then saying "pan pause" still works (restart works); turning it off stops listening. Then test with the Bluetooth headset on: note whether audio drops to call quality while the mic is open and report back to the user (fallback options in the spec).

### Task 5: Preview and commit gate

- [ ] **Step 1:** Leave the app running locally and ask the user to try it. Do not commit `ui/` changes until they have looked.
- [ ] **Step 2:** After their go-ahead: `uv run pytest`, then commit `ui/` changes (e.g. `feat: voice control (tap-to-talk and hands-free)`). Deploy stays the manual `make deploy`.

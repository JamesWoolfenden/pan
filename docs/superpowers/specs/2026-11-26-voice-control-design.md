# Voice control for the pan player (design)

Status: approved by the user in conversation ("worth a go"), 2026-11-26.

## Intent and why

The user rides a Zwift bike with Bluetooth headphones on. A keyboard is awkward there, so
they want to control pan hands-free by voice: skip, pause, volume, and "play something like X".

## Goals

- Tap-to-talk mic button in the player; large and touch-friendly. One utterance, one action.
- Optional hands-free toggle (off by default): keeps listening, only acts on utterances that
  start with the wake word "pan" (e.g. "pan, next"). This is my call, to be tried on the bike.
- Local intents handled instantly in the browser, no network round trip to Claude:
  play/pause, next/previous, volume up/down/set, shuffle, repeat, favorite/dislike.
- Anything not matching a local intent goes to the existing `/search` (Claude-parsed for
  negation and multi-constraint queries) and the top results become a new queue.
- Feedback is an on-screen toast ("Skipped", "Volume 60%"). No spoken replies in v1.

## Non-goals

- No remote control from a second device and no server-to-player command channel.
- No Claude-app voice mode or MCP tool server for pan.
- No offline or on-device speech recognition.

## Anti-goals

- Zwift audio, chatter or breathing triggering commands in hands-free mode.
- Voice commands breaking normal keyboard/mouse use of the player.

## Constraints

- The player lives entirely in the browser (`ui/`); the server has no way to command it,
  which is why v1 puts speech recognition in the page itself.
- Browser speech recognition (Web Speech API): needs a secure context (pan already serves
  HTTPS / Tailscale), needs internet in Chrome, and is less reliable on iOS Safari.
- Some Bluetooth headsets drop to low-quality call audio while the mic is open. If seen,
  fall back to the device's built-in mic or keep the mic open only while speaking.
- Per project preference, UI changes are run locally and shown to the user before committing.

## Approaches considered

- **A. Voice inside the pan page** (chosen). Simplest, no new server plumbing; headphones
  remove the main weakness (mic hearing the music).
- **B. Remote-control channel plus a separate voice device.** Better mic placement and works
  when the player is on a TV/PC, but needs a new server-to-player channel and a second device.
  The command layer is structured so B could be added later.
- **C. Claude as the voice interface via MCP.** Best for complex requests, but Claude's phone
  app needs a publicly reachable tool server and pan is private on Tailscale; also needs B.

## Left to the builder

- Exact command phrasing and synonyms; tolerance for recognition mishearings.
- Where the mic button sits in the existing player layout.
- Whether the local-intent parser is a small keyword matcher or table-driven.
- Tests for the intent parser (pure function, easy to unit test).

import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { parseIntent } = require("../ui/voice-intents.js");

const tap = (t) => parseIntent(t, { handsFree: false });
const free = (t) => parseIntent(t, { handsFree: true });

test("next and skip advance", () => {
  assert.deepEqual(tap("next"), { type: "next" });
  assert.deepEqual(tap("skip"), { type: "next" });
});

test("previous and go back go to the previous track", () => {
  assert.deepEqual(tap("previous"), { type: "prev" });
  assert.deepEqual(tap("go back"), { type: "prev" });
});

test("pause and stop pause; play and resume play", () => {
  assert.deepEqual(tap("pause"), { type: "pause" });
  assert.deepEqual(tap("stop"), { type: "pause" });
  assert.deepEqual(tap("play"), { type: "play" });
  assert.deepEqual(tap("resume"), { type: "play" });
});

test("louder and quieter adjust volume by 10", () => {
  assert.deepEqual(tap("louder"), { type: "volume-delta", delta: 10 });
  assert.deepEqual(tap("volume up"), { type: "volume-delta", delta: 10 });
  assert.deepEqual(tap("quieter"), { type: "volume-delta", delta: -10 });
  assert.deepEqual(tap("volume down"), { type: "volume-delta", delta: -10 });
});

test("volume with a number sets volume, clamped to 0-100", () => {
  assert.deepEqual(tap("volume 60"), { type: "volume-set", percent: 60 });
  assert.deepEqual(tap("set volume to 60"), { type: "volume-set", percent: 60 });
  assert.deepEqual(tap("volume 150"), { type: "volume-set", percent: 100 });
});

test("bare 'volume' is not a set-volume command", () => {
  assert.notEqual(tap("volume").type, "volume-set");
});

test("simple toggles", () => {
  assert.deepEqual(tap("mute"), { type: "mute" });
  assert.deepEqual(tap("shuffle"), { type: "shuffle" });
  assert.deepEqual(tap("repeat"), { type: "repeat" });
  assert.deepEqual(tap("love this"), { type: "favorite" });
  assert.deepEqual(tap("favorite"), { type: "favorite" });
  assert.deepEqual(tap("dislike"), { type: "dislike" });
});

test("'play <words>' is a search for the words", () => {
  assert.deepEqual(tap("play something upbeat but not metal"), {
    type: "search",
    query: "something upbeat but not metal",
  });
});

test("anything unrecognised is a search", () => {
  assert.deepEqual(tap("mellow eighties synth"), { type: "search", query: "mellow eighties synth" });
});

test("empty or whitespace transcripts are ignored", () => {
  assert.deepEqual(tap(""), { type: "ignore" });
  assert.deepEqual(tap("   "), { type: "ignore" });
});

test("case and trailing punctuation do not matter", () => {
  assert.deepEqual(tap("Next."), { type: "next" });
});

test("hands-free: utterance without wake word is ignored", () => {
  assert.deepEqual(free("next"), { type: "ignore" });
});

test("hands-free: wake word (and 'pam' mishearing) enables the command", () => {
  assert.deepEqual(free("pan next"), { type: "next" });
  assert.deepEqual(free("Pam, next"), { type: "next" });
});

test("hands-free: bare wake word does nothing", () => {
  assert.deepEqual(free("pan"), { type: "ignore" });
});

test("hands-free: wake word then play <words> searches", () => {
  assert.deepEqual(free("pan play some jazz"), { type: "search", query: "some jazz" });
});

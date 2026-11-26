// Turns a speech transcript into a player intent. Pure (no DOM, no player state) so it can be unit
// tested under Node - see tests/voice_intents.test.mjs. ui/voice.js maps intents onto the player.
(function (root) {
  // Browser speech recognition is shaky on a short word like "pan": these are the usual mishearings.
  const WAKE_WORDS = ["pan", "pam", "pen", "penn", "ben", "pin"];
  // Allowed in front of the wake word ("hey pan, next").
  const WAKE_LEAD_INS = ["hey", "okay", "ok"];
  // Dropped when looking a command up ("skip this song", "pause the music"); never applied to a
  // search query, so "play the beatles" still searches for "the beatles".
  const FILLER_WORDS = new Set(["please", "the", "this", "that", "it", "now", "song", "track", "music", "one"]);

  const PHRASES = {
    next: { type: "next" },
    skip: { type: "next" },
    "next track": { type: "next" },
    "next song": { type: "next" },
    "skip track": { type: "next" },
    previous: { type: "prev" },
    "go back": { type: "prev" },
    back: { type: "prev" },
    "previous track": { type: "prev" },
    "last track": { type: "prev" },
    pause: { type: "pause" },
    stop: { type: "pause" },
    play: { type: "play" },
    resume: { type: "play" },
    continue: { type: "play" },
    louder: { type: "volume-delta", delta: 10 },
    "volume up": { type: "volume-delta", delta: 10 },
    "turn it up": { type: "volume-delta", delta: 10 },
    "turn up": { type: "volume-delta", delta: 10 },
    quieter: { type: "volume-delta", delta: -10 },
    softer: { type: "volume-delta", delta: -10 },
    "volume down": { type: "volume-delta", delta: -10 },
    "turn it down": { type: "volume-delta", delta: -10 },
    "turn down": { type: "volume-delta", delta: -10 },
    mute: { type: "mute" },
    unmute: { type: "mute" },
    shuffle: { type: "shuffle" },
    repeat: { type: "repeat" },
    favorite: { type: "favorite" },
    favourite: { type: "favorite" },
    "love this": { type: "favorite" },
    "like this": { type: "favorite" },
    dislike: { type: "dislike" },
    "hate this": { type: "dislike" },
  };

  function normalise(transcript) {
    return String(transcript || "")
      .toLowerCase()
      .replace(/[^a-z0-9\s-]/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function parseIntent(transcript, opts) {
    let text = normalise(transcript);
    if (opts && opts.handsFree) {
      const words = text.split(" ");
      if (WAKE_LEAD_INS.includes(words[0])) words.shift();
      if (!WAKE_WORDS.includes(words[0])) return { type: "ignore" };
      text = words.slice(1).join(" ");
    }
    if (!text) return { type: "ignore" };

    if (Object.prototype.hasOwnProperty.call(PHRASES, text)) return { ...PHRASES[text] };

    const stripped = text
      .split(" ")
      .filter((w) => !FILLER_WORDS.has(w))
      .join(" ");
    if (stripped !== text && Object.prototype.hasOwnProperty.call(PHRASES, stripped)) {
      return { ...PHRASES[stripped] };
    }

    const setVolume = text.match(/^(?:set )?volume (?:to )?(-?\d{1,4})(?: percent)?$/);
    if (setVolume) {
      return { type: "volume-set", percent: Math.max(0, Math.min(100, Number(setVolume[1]))) };
    }

    const play = text.match(/^play (.+)$/);
    if (play) return { type: "search", query: play[1] };

    return { type: "search", query: text };
  }

  const api = { parseIntent, WAKE_WORDS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PanVoice = api;
})(typeof window !== "undefined" ? window : globalThis);

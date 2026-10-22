// Only registers over HTTPS/localhost - browsers refuse service workers elsewhere, so this is
// a silent no-op when the app happens to be opened over plain HTTP.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

const form = document.getElementById("search-form");
const input = document.getElementById("query");
const historyEl = document.getElementById("search-history");
const resultsEl = document.getElementById("results");
const resultsHeaderEl = document.getElementById("results-header");
const contextBannerEl = document.getElementById("context-banner");
const playAllBtn = document.getElementById("play-all-btn");
const playlistAllBtn = document.getElementById("playlist-all-btn");
const reindexBtn = document.getElementById("reindex-btn");
const reindexStatusEl = document.getElementById("reindex-status");
const adminToggleBtn = document.getElementById("admin-toggle");
const adminPanelEl = document.getElementById("admin-panel");

adminToggleBtn.addEventListener("click", () => {
  adminPanelEl.hidden = !adminPanelEl.hidden;
  adminToggleBtn.setAttribute("aria-expanded", String(!adminPanelEl.hidden));
});

// /version is exempt from auth (see backend/app.py) specifically so this works without a
// login prompt - the whole point is confirming a deploy actually updated before chasing what
// looks like a bug but is really just stale code still running.
fetch("/version")
  .then((res) => res.json())
  .then((data) => {
    document.getElementById("app-version").textContent = data.version;
  })
  .catch(() => {});

const queuePlayer = document.getElementById("queue-player");
const castStatusEl = document.getElementById("cast-status");
const castVolumeControlsEl = document.getElementById("cast-volume-controls");
const castVolumeToggleBtn = document.getElementById("cast-volume-toggle");
const castVolumeSlidersEl = document.getElementById("cast-volume-sliders");
const castMuteBtn = document.getElementById("cast-mute");
const castVolumeSlider = document.getElementById("cast-volume");
const castProgressControlsEl = document.getElementById("cast-progress-controls");
const castProgressSlider = document.getElementById("cast-progress");
const castTimeCurrentEl = document.getElementById("cast-time-current");
const castTimeDurationEl = document.getElementById("cast-time-duration");
const queueListEl = document.getElementById("queue-list");
const nowPlayingEl = document.getElementById("queue-now-playing");
const shuffleBtn = document.getElementById("queue-shuffle");
const prevBtn = document.getElementById("queue-prev");
const playPauseBtn = document.getElementById("queue-playpause");
const nextBtn = document.getElementById("queue-next");
const repeatBtn = document.getElementById("queue-repeat");
const videoModeBtn = document.getElementById("queue-video-mode");
const eqToggleBtn = document.getElementById("eq-toggle");
const eqPanelEl = document.getElementById("eq-panel");
const eqMeterEl = document.getElementById("eq-meter");
const eqFadersEl = document.getElementById("eq-faders");
const eqResetBtn = document.getElementById("eq-reset");
const eqPresetSelectEl = document.getElementById("eq-preset-select");
const eqSavePresetBtn = document.getElementById("eq-save-preset");
const eqBypassBtn = document.getElementById("eq-bypass");
const eqBypassStatusEl = document.getElementById("eq-bypass-status");
const eqPreampSlider = document.getElementById("eq-preamp");
const eqPreampValueEl = document.getElementById("eq-preamp-value");
const eqBalanceKnobEl = document.getElementById("eq-balance-knob");
const eqBalanceValueEl = document.getElementById("eq-balance-value");
const queueVolumeKnobEl = document.getElementById("queue-volume-knob");
const queueVolumeValueEl = document.getElementById("queue-volume-value");
const youtubePlayerWrap = document.getElementById("youtube-player-wrap");
const videoDismissBtn = document.getElementById("video-dismiss-btn");
const saveBtn = document.getElementById("queue-save");
const cloneBtn = document.getElementById("queue-clone");
const addToPlaylistBtn = document.getElementById("queue-add-to-playlist");
const clearBtn = document.getElementById("queue-clear");
const queueListToggleBtn = document.getElementById("queue-list-toggle");
const playlistsListEl = document.getElementById("playlists-list");
const playlistsToggleBtn = document.getElementById("playlists-toggle");
const playlistsFilterEl = document.getElementById("playlists-filter");
const loadMoreBtn = document.getElementById("load-more-btn");
const nowPlayingActionsEl = document.getElementById("queue-now-playing-actions");
const queueArtEl = document.getElementById("queue-art");

// Shared by every collapsible section that uses a chevron toggle (queue list, playlists) -
// always visible, on every screen size. Defaults open (.collapsed absent), same as before this
// toggle existed.
function wireCollapseToggle(toggleBtn, targetEl) {
  toggleBtn.addEventListener("click", () => {
    const collapsed = targetEl.classList.toggle("collapsed");
    toggleBtn.setAttribute("aria-expanded", String(!collapsed));
    toggleBtn.innerHTML = collapsed ? "&#9662;" : "&#9652;";
  });
}
wireCollapseToggle(queueListToggleBtn, queueListEl);
wireCollapseToggle(playlistsToggleBtn, playlistsListEl);

// 10-band graphic equaliser + live spectrum meter, applied via the Web Audio API to the local
// <audio> element only - Chromecast and video mode both play back through an entirely separate
// path (the receiver, and the YouTube iframe) that this graph never touches.
// updateEqAvailability() greys the panel out (and halts the meter) rather than hiding it whenever
// queuePlayer.hidden flips for either of those.
//
// Standard ISO-ish graphic-EQ centre frequencies, one octave apart - matches the layout in the
// reference hardware equaliser/spectrum display this was modelled on.
const EQ_BANDS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];
const EQ_STATE_KEY = "pan.eqState";
const EQ_CUSTOM_PRESETS_KEY = "pan.eqCustomPresets";
const EQ_CUSTOM_PRESET_PREFIX = "custom:";
const EQ_PEAK_DECAY_PER_FRAME = 1.2; // % of meter height per animation frame (~60fps)
const EQ_BUILTIN_PRESETS = {
  Flat: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  "Bass Boost": [7, 6, 4, 2, 0, 0, 0, 0, 0, 0],
  Vocal: [-2, -2, -1, 1, 3, 4, 3, 1, -1, -2],
  Rock: [4, 3, 2, 0, -1, 0, 2, 3, 4, 4],
  "Treble Boost": [0, 0, 0, 0, 0, 1, 2, 4, 5, 6],
  Loudness: [5, 4, 2, 0, -2, -2, 0, 2, 4, 5],
};

let eqAudioCtx = null;
let eqPreampNode = null; // overall level, ahead of the 10 bands
let eqFilterNodes = []; // one BiquadFilterNode per EQ_BANDS entry, chained in order
// Not a user control - boosting several bands at once can push the signal into clipping, so a
// gentle limiter sits at the end of the chain regardless of the bypass/preamp settings above it.
let eqLimiterNode = null;
let eqPannerNode = null; // left/right balance
let eqAnalyser = null;
let eqAnalyserData = null;
let eqBandBinRanges = null; // [lo, hi] FFT bin indices per band, for the meter
let eqMeterRafId = null;
// Zeroes the filters/preamp without moving their sliders or touching saved state (see
// applyEqGains) - a quick A/B against "no EQ", not a second settings profile, so it isn't
// persisted across reloads any more than videoModeEnabled is.
let eqBypassed = false;
let eqBalanceValue = 0; // -100 (full left) .. 100 (full right) - the knob's source of truth

function formatEqFreqLabel(freq) {
  return freq >= 1000 ? `${freq / 1000}k` : String(freq);
}

function dbToGain(db) {
  return Math.pow(10, db / 20);
}

function loadEqState() {
  try {
    const saved = JSON.parse(localStorage.getItem(EQ_STATE_KEY));
    // Pre-preamp/balance format: just the 10 band gains.
    if (Array.isArray(saved) && saved.length === EQ_BANDS.length && saved.every(Number.isFinite)) {
      return { bands: saved, preamp: 0, balance: 0 };
    }
    if (
      saved &&
      Array.isArray(saved.bands) &&
      saved.bands.length === EQ_BANDS.length &&
      saved.bands.every(Number.isFinite) &&
      Number.isFinite(saved.preamp) &&
      Number.isFinite(saved.balance)
    ) {
      return saved;
    }
  } catch {
    // localStorage unavailable (private browsing etc) - the EQ just won't persist across reloads
  }
  return { bands: EQ_BANDS.map(() => 0), preamp: 0, balance: 0 };
}

// Always reads live state rather than taking it as a parameter - a single source of truth
// avoids every call site having to assemble the same object correctly.
function saveEqState() {
  try {
    localStorage.setItem(
      EQ_STATE_KEY,
      JSON.stringify({
        bands: currentBandGains(),
        preamp: Number(eqPreampSlider.value),
        balance: eqBalanceValue / 100,
      }),
    );
  } catch {
    // localStorage unavailable (private browsing etc) - the EQ just won't persist across reloads
  }
}

function loadCustomPresets() {
  try {
    const saved = JSON.parse(localStorage.getItem(EQ_CUSTOM_PRESETS_KEY));
    if (saved && typeof saved === "object") return saved;
  } catch {
    // localStorage unavailable (private browsing etc) - saved presets just won't persist
  }
  return {};
}

function saveCustomPresets(presets) {
  try {
    localStorage.setItem(EQ_CUSTOM_PRESETS_KEY, JSON.stringify(presets));
  } catch {
    // localStorage unavailable (private browsing etc) - saved presets just won't persist
  }
}

const eqSliders = [];
const eqValueEls = [];
const eqMeterFillEls = [];
const eqMeterPeakEls = [];
const eqPeaks = []; // current peak-hold height per band, 0-100

// Builds the meter-bar row and fader row as matching sets of equal-width flex columns (see the
// shared .eq-band/.eq-meter-col CSS) - generated from EQ_BANDS rather than hand-duplicated in
// index.html, so retuning the band list is a one-line change here instead of an HTML edit too.
function buildEqUi() {
  EQ_BANDS.forEach((freq, i) => {
    const meterCol = document.createElement("div");
    meterCol.className = "eq-meter-col";
    const meterFill = document.createElement("div");
    meterFill.className = "eq-meter-fill";
    const meterPeak = document.createElement("div");
    meterPeak.className = "eq-meter-peak";
    meterCol.append(meterFill, meterPeak);
    eqMeterEl.appendChild(meterCol);
    eqMeterFillEls.push(meterFill);
    eqMeterPeakEls.push(meterPeak);
    eqPeaks.push(0);

    const band = document.createElement("div");
    band.className = "eq-band";
    const value = document.createElement("span");
    value.className = "eq-value";
    value.textContent = "0 dB";
    const slider = document.createElement("input");
    slider.type = "range";
    slider.min = "-12";
    slider.max = "12";
    slider.step = "1";
    slider.value = "0";
    slider.id = `eq-band-${i}`;
    slider.dataset.index = String(i);
    // See the .eq-fader-wrap comment in style.css for why this is a rail wrapped in a ticks +
    // rotated-input structure rather than a bare <input>.
    const faderWrap = document.createElement("div");
    faderWrap.className = "eq-fader-wrap";
    const ticks = document.createElement("div");
    ticks.className = "eq-fader-ticks";
    const rail = document.createElement("div");
    rail.className = "eq-fader-rail";
    rail.appendChild(slider);
    faderWrap.append(ticks, rail);
    const label = document.createElement("label");
    label.setAttribute("for", slider.id);
    label.textContent = formatEqFreqLabel(freq);
    band.append(value, faderWrap, label);
    eqFadersEl.appendChild(band);
    eqSliders.push(slider);
    eqValueEls.push(value);
  });
}
buildEqUi();

function renderEqValues(bands) {
  bands.forEach((v, i) => {
    eqValueEls[i].textContent = `${v > 0 ? "+" : ""}${v} dB`;
  });
}

function currentBandGains() {
  return eqSliders.map((s) => Number(s.value));
}

// What a filter/the preamp should actually be set to right now, folding in bypass - the sliders
// themselves never move for bypass, only what the audio graph is told to do with them.
function effectiveBandGain(i) {
  return eqBypassed ? 0 : currentBandGains()[i];
}

function effectivePreampGain() {
  return eqBypassed ? 1 : dbToGain(Number(eqPreampSlider.value));
}

function applyEqGains() {
  eqFilterNodes.forEach((f, i) => {
    f.gain.value = effectiveBandGain(i);
  });
  if (eqPreampNode) eqPreampNode.gain.value = effectivePreampGain();
}

function allPresets() {
  const presets = { ...EQ_BUILTIN_PRESETS };
  for (const [name, gains] of Object.entries(loadCustomPresets())) {
    presets[`${EQ_CUSTOM_PRESET_PREFIX}${name}`] = gains;
  }
  return presets;
}

function matchingPresetKey(gains) {
  for (const [key, presetGains] of Object.entries(allPresets())) {
    if (presetGains.length === gains.length && presetGains.every((v, i) => v === gains[i])) {
      return key;
    }
  }
  return ""; // doesn't match any known preset - the hidden "Custom" placeholder option
}

// Rebuilds the <option>s (built-ins, then an optgroup of anything saved via "Save preset…") and
// selects `selectedKey` - called on load and whenever a preset is saved, since the custom list
// can change.
function populatePresetSelect(selectedKey) {
  eqPresetSelectEl.innerHTML = "";

  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Custom";
  placeholder.hidden = true; // not a real choice - just what shows when nothing else matches
  eqPresetSelectEl.appendChild(placeholder);

  for (const name of Object.keys(EQ_BUILTIN_PRESETS)) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    eqPresetSelectEl.appendChild(opt);
  }

  const customNames = Object.keys(loadCustomPresets());
  if (customNames.length) {
    const group = document.createElement("optgroup");
    group.label = "Your presets";
    for (const name of customNames) {
      const opt = document.createElement("option");
      opt.value = `${EQ_CUSTOM_PRESET_PREFIX}${name}`;
      opt.textContent = name;
      group.appendChild(opt);
    }
    eqPresetSelectEl.appendChild(group);
  }

  eqPresetSelectEl.value = selectedKey ?? "";
}

eqPresetSelectEl.addEventListener("change", () => {
  const gains = allPresets()[eqPresetSelectEl.value];
  if (!gains) return; // the hidden "Custom" placeholder, or a stale value - nothing to apply
  eqSliders.forEach((s, i) => {
    s.value = gains[i];
  });
  renderEqValues(gains);
  applyEqGains();
  saveEqState();
});

eqSavePresetBtn.addEventListener("click", () => {
  const name = prompt("Save the current EQ curve as:");
  if (!name || !name.trim()) return;
  const trimmed = name.trim();
  const custom = loadCustomPresets();
  custom[trimmed] = currentBandGains();
  saveCustomPresets(custom);
  populatePresetSelect(`${EQ_CUSTOM_PRESET_PREFIX}${trimmed}`);
});

eqBypassBtn.addEventListener("click", () => {
  eqBypassed = !eqBypassed;
  eqBypassBtn.setAttribute("aria-pressed", String(eqBypassed));
  eqBypassBtn.classList.toggle("is-active", eqBypassed);
  eqBypassStatusEl.textContent = eqBypassed ? "On" : "Off";
  applyEqGains();
});

// Octave-band edges as the geometric mean between each pair of neighbouring centre frequencies -
// the standard way to split a spectrum into bands, so the meter's bins line up with what each
// fader actually controls instead of an arbitrary slice.
function computeEqBandBinRanges() {
  const binHz = eqAudioCtx.sampleRate / eqAnalyser.fftSize;
  const edges = [0];
  for (let i = 0; i < EQ_BANDS.length - 1; i++) {
    edges.push(Math.sqrt(EQ_BANDS[i] * EQ_BANDS[i + 1]));
  }
  edges.push(eqAudioCtx.sampleRate / 2);
  const bufferLength = eqAnalyser.frequencyBinCount;
  return EQ_BANDS.map((_, i) => {
    const lo = Math.max(0, Math.round(edges[i] / binHz));
    const hi = Math.min(bufferLength - 1, Math.max(lo, Math.round(edges[i + 1] / binHz) - 1));
    return [lo, hi];
  });
}

// Built lazily on first playback rather than at page load: createMediaElementSource() can only
// ever be called once per <audio> element (a second call throws "already connected"), and an
// AudioContext created before any user gesture risks starting "suspended" in some browsers -
// tying setup to the first real play sidesteps both.
function ensureEqGraph() {
  if (eqAudioCtx) return;
  eqAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
  // Once audio is routed through here, it stays routed through here - so if the OS suspends the
  // context (iOS in particular does this within seconds of the screen locking, independently of
  // the <audio> element's own paused state), playback goes silent even though queuePlayer looks
  // like it's still "playing". Nothing auto-resumes a suspended context; resumeEqAudioCtx() below
  // is called from every place that could plausibly need it (statechange, visibilitychange, and
  // every "play" event, not just the first).
  eqAudioCtx.addEventListener("statechange", () => {
    logClient(`eq_audio_ctx_${eqAudioCtx.state}`);
    resumeEqAudioCtx();
  });
  const source = eqAudioCtx.createMediaElementSource(queuePlayer);

  eqPreampNode = eqAudioCtx.createGain();
  eqPreampNode.gain.value = effectivePreampGain();

  eqFilterNodes = EQ_BANDS.map((freq, i) => {
    const filter = eqAudioCtx.createBiquadFilter();
    filter.type = "peaking";
    filter.frequency.value = freq;
    filter.Q.value = 1.41; // ~1 octave bandwidth, matching the band spacing above
    filter.gain.value = effectiveBandGain(i);
    return filter;
  });

  eqLimiterNode = eqAudioCtx.createDynamicsCompressor();
  eqLimiterNode.threshold.value = -3;
  eqLimiterNode.knee.value = 0;
  eqLimiterNode.ratio.value = 20;
  eqLimiterNode.attack.value = 0.003;
  eqLimiterNode.release.value = 0.25;

  eqPannerNode = eqAudioCtx.createStereoPanner();
  eqPannerNode.pan.value = eqBalanceValue / 100;

  eqAnalyser = eqAudioCtx.createAnalyser();
  eqAnalyser.fftSize = 2048;
  eqAnalyser.smoothingTimeConstant = 0.8;
  eqAnalyserData = new Uint8Array(eqAnalyser.frequencyBinCount);
  eqBandBinRanges = computeEqBandBinRanges();

  let node = source;
  node.connect(eqPreampNode);
  node = eqPreampNode;
  eqFilterNodes.forEach((filter) => {
    node.connect(filter);
    node = filter;
  });
  node.connect(eqLimiterNode);
  eqLimiterNode.connect(eqPannerNode);
  eqPannerNode.connect(eqAnalyser);
  eqAnalyser.connect(eqAudioCtx.destination);
  startEqMeterLoop();
}
queuePlayer.addEventListener("play", ensureEqGraph);

function resumeEqAudioCtx() {
  if (eqAudioCtx && eqAudioCtx.state === "suspended" && !queuePlayer.paused) {
    eqAudioCtx.resume().catch(() => {});
  }
}

// Runs continuously once started rather than only while actually playing - queuePlayer.paused is
// checked every frame instead, so the meter decays to 0 and resumes correctly across pause/resume
// without needing separate play/pause listeners to start and stop a second loop.
function startEqMeterLoop() {
  if (eqMeterRafId) return;
  const tick = () => {
    eqMeterRafId = requestAnimationFrame(tick);
    if (eqPanelEl.classList.contains("collapsed")) return; // nothing visible - skip the work
    if (!eqAnalyser || queuePlayer.hidden || queuePlayer.paused) {
      eqMeterFillEls.forEach((el, i) => {
        el.style.height = "0%";
        eqPeaks[i] = 0;
        eqMeterPeakEls[i].style.bottom = "0%";
      });
      return;
    }
    eqAnalyser.getByteFrequencyData(eqAnalyserData);
    eqBandBinRanges.forEach(([lo, hi], i) => {
      let sum = 0;
      for (let b = lo; b <= hi; b++) sum += eqAnalyserData[b];
      const avg = sum / (hi - lo + 1); // 0-255
      const pct = Math.min(100, (avg / 255) * 100);
      eqMeterFillEls[i].style.height = `${pct}%`;
      // Jumps up to a new peak instantly, decays back down slowly - the detail that makes a
      // brief transient visible after the live fill bar itself has already dropped from it.
      eqPeaks[i] = pct >= eqPeaks[i] ? pct : Math.max(pct, eqPeaks[i] - EQ_PEAK_DECAY_PER_FRAME);
      eqMeterPeakEls[i].style.bottom = `${eqPeaks[i]}%`;
    });
  };
  tick();
}

function onEqSliderInput(e) {
  const i = Number(e.target.dataset.index);
  const v = Number(e.target.value);
  eqValueEls[i].textContent = `${v > 0 ? "+" : ""}${v} dB`;
  if (eqFilterNodes[i]) eqFilterNodes[i].gain.value = effectiveBandGain(i);
  eqPresetSelectEl.value = matchingPresetKey(currentBandGains());
  saveEqState();
}
eqSliders.forEach((el) => el.addEventListener("input", onEqSliderInput));

eqPreampSlider.addEventListener("input", () => {
  const v = Number(eqPreampSlider.value);
  eqPreampValueEl.textContent = `${v > 0 ? "+" : ""}${v} dB`;
  if (eqPreampNode) eqPreampNode.gain.value = effectivePreampGain();
  saveEqState();
});

// -135deg/+135deg is the standard 270-degree sweep for a software knob (straight up at center).
const EQ_BALANCE_ROTATION_DEGREES = 135;

function setEqBalance(value, { save = true } = {}) {
  eqBalanceValue = Math.max(-100, Math.min(100, Math.round(value / 5) * 5));
  eqBalanceKnobEl.style.transform = `rotate(${(eqBalanceValue / 100) * EQ_BALANCE_ROTATION_DEGREES}deg)`;
  eqBalanceKnobEl.setAttribute("aria-valuenow", String(eqBalanceValue));
  eqBalanceValueEl.textContent =
    eqBalanceValue === 0 ? "C" : eqBalanceValue < 0 ? `L${-eqBalanceValue}` : `R${eqBalanceValue}`;
  if (eqPannerNode) eqPannerNode.pan.value = eqBalanceValue / 100;
  if (save) saveEqState();
}

// Vertical drag rather than tracking the pointer's angle around the knob's center - simpler to
// get right and the common convention for software knobs (turntable/mixer apps, DAWs) anyway.
// setPointerCapture keeps move/up events targeting the knob even once the cursor leaves it
// mid-drag, so a fast drag doesn't "lose" the control. Shared by every rotary <div role="slider">
// knob in the app (EQ balance, queue volume) rather than each wiring up its own copy of this.
function bindKnobDrag(knobEl, { getValue, onChange, range, step, onHome }) {
  let dragStartY = null;
  let dragStartValue = 0;

  knobEl.addEventListener("pointerdown", (e) => {
    if (knobEl.tabIndex < 0) return; // disabled (see updateEqAvailability)
    dragStartY = e.clientY;
    dragStartValue = getValue();
    knobEl.setPointerCapture(e.pointerId);
  });

  knobEl.addEventListener("pointermove", (e) => {
    if (dragStartY === null) return;
    // 150px of drag covers the full range.
    const delta = dragStartY - e.clientY;
    onChange(dragStartValue + (delta / 150) * range);
  });

  const endDrag = () => {
    dragStartY = null;
  };
  knobEl.addEventListener("pointerup", endDrag);
  knobEl.addEventListener("pointercancel", endDrag);

  knobEl.addEventListener("keydown", (e) => {
    if (knobEl.tabIndex < 0) return; // disabled
    if (e.key === "ArrowUp" || e.key === "ArrowRight") {
      e.preventDefault();
      onChange(getValue() + step);
    } else if (e.key === "ArrowDown" || e.key === "ArrowLeft") {
      e.preventDefault();
      onChange(getValue() - step);
    } else if (e.key === "Home" && onHome) {
      e.preventDefault();
      onHome();
    }
  });
}

bindKnobDrag(eqBalanceKnobEl, {
  getValue: () => eqBalanceValue,
  onChange: setEqBalance,
  range: 200, // -100..100
  step: 5,
  onHome: () => setEqBalance(0),
});

// Desktop-only local playback volume (see the comment above #eq-balance-knob in index.html for
// why mobile has no equivalent). 0-270deg, not centered like the balance knob above - volume is
// a single min-to-max range, not a bipolar one, so there's no meaningful "straight up" position
// for it to rest at.
const QUEUE_VOLUME_KEY = "pan.volume";

function loadQueueVolumePercent() {
  try {
    const raw = localStorage.getItem(QUEUE_VOLUME_KEY);
    // Number(null) is 0, not NaN - without this explicit check, "nothing saved yet" was
    // indistinguishable from "saved at 0%" and silently loaded muted every first visit.
    if (raw === null) return 100;
    const saved = Number(raw);
    if (Number.isFinite(saved) && saved >= 0 && saved <= 100) return saved;
  } catch {
    // localStorage unavailable (private browsing etc) - falls back to full volume every load
  }
  return 100;
}

function renderQueueVolumeKnob(percent) {
  queueVolumeKnobEl.style.transform = `rotate(${(percent / 100) * 270 - 135}deg)`;
  queueVolumeKnobEl.setAttribute("aria-valuenow", String(percent));
  queueVolumeValueEl.textContent = `${percent}%`;
}

function setQueueVolume(percent, { save = true } = {}) {
  const clamped = Math.max(0, Math.min(100, Math.round(percent / 5) * 5));
  queuePlayer.volume = clamped / 100;
  queuePlayer.muted = false;
  // renderQueueVolumeKnob() also runs from the "volumechange" listener below, so the dial
  // reflects this assignment either way - setting it here too isn't redundant, since that
  // listener fires async-ish (next microtask-ish tick via the native event), and skipping an
  // explicit render here would leave the dial visibly stale for a drag's entire duration.
  renderQueueVolumeKnob(clamped);
  if (save) {
    try {
      localStorage.setItem(QUEUE_VOLUME_KEY, String(clamped));
    } catch {
      // localStorage unavailable (private browsing etc) - the level just won't persist
    }
  }
}
setQueueVolume(loadQueueVolumePercent(), { save: false });

// Belt-and-suspenders beyond hiding the native <audio controls> volume slider/mute button (see
// style.css) - keeps the dial honest against volume changes from any OTHER source too (OS/site
// volume restoration, a future code path, scroll-wheel-over-audio in some browsers), rather than
// silently drifting out of sync with queuePlayer's actual, real volume the way it did before.
queuePlayer.addEventListener("volumechange", () => {
  renderQueueVolumeKnob(Math.round(queuePlayer.volume * 100));
});

bindKnobDrag(queueVolumeKnobEl, {
  getValue: () => Math.round(queuePlayer.volume * 100),
  onChange: setQueueVolume,
  range: 200, // 0..100, but matching the balance knob's drag feel rather than a 100-range crawl
  step: 5,
});

function seekBy(deltaSeconds) {
  if (isCasting()) {
    if (!Number.isFinite(castPlayer.duration)) return;
    castPlayer.currentTime = Math.max(0, Math.min(castPlayer.duration, castPlayer.currentTime + deltaSeconds));
    castController.seek();
  } else if (!queuePlayer.hidden && Number.isFinite(queuePlayer.duration)) {
    queuePlayer.currentTime = Math.max(0, Math.min(queuePlayer.duration, queuePlayer.currentTime + deltaSeconds));
  }
}

function adjustVolumeBy(deltaPercent) {
  if (isCasting()) {
    castPlayer.volumeLevel = Math.max(0, Math.min(1, castPlayer.volumeLevel + deltaPercent / 100));
    castController.setVolumeLevel();
    updateCastVolumeUi();
  } else if (!queuePlayer.hidden) {
    setQueueVolume(queuePlayer.volume * 100 + deltaPercent);
  }
}

function toggleMute() {
  if (isCasting()) {
    castController.muteOrUnmute();
    updateCastVolumeUi();
  } else if (!queuePlayer.hidden) {
    queuePlayer.muted = !queuePlayer.muted;
  }
}

// Global playback shortcuts - space/arrows/M, the same keys every other web media player (YouTube,
// Spotify web) uses, so nothing here needs to be learned specially. Skipped entirely while a text
// field, button, or a custom role="slider" knob (which handles its own arrow keys - see
// bindKnobDrag above) has focus, so typing in search/filter/prompt fields and operating the EQ's
// own knobs isn't hijacked.
const KEYBOARD_SHORTCUT_IGNORE_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT", "BUTTON"]);

document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey) return; // leave browser/OS shortcuts alone
  const active = document.activeElement;
  if (
    active &&
    (KEYBOARD_SHORTCUT_IGNORE_TAGS.has(active.tagName) ||
      active.isContentEditable ||
      active.getAttribute("role") === "slider")
  ) {
    return;
  }
  switch (e.key) {
    case " ":
      e.preventDefault();
      playPauseBtn.click();
      break;
    case "ArrowLeft":
      e.preventDefault();
      seekBy(-5);
      break;
    case "ArrowRight":
      e.preventDefault();
      seekBy(5);
      break;
    case "ArrowUp":
      e.preventDefault();
      adjustVolumeBy(5);
      break;
    case "ArrowDown":
      e.preventDefault();
      adjustVolumeBy(-5);
      break;
    case "m":
    case "M":
      toggleMute();
      break;
  }
});

eqResetBtn.addEventListener("click", () => {
  eqSliders.forEach((s) => {
    s.value = 0;
  });
  eqPreampSlider.value = 0;
  renderEqValues(EQ_BANDS.map(() => 0));
  eqPreampValueEl.textContent = "0 dB";
  applyEqGains();
  setEqBalance(0, { save: false }); // saveEqState() below covers it, no need to double-save
  eqPresetSelectEl.value = "Flat";
  saveEqState();
});

(function initEqControls() {
  const state = loadEqState();
  eqSliders.forEach((s, i) => {
    s.value = state.bands[i];
  });
  renderEqValues(state.bands);
  eqPreampSlider.value = state.preamp;
  eqPreampValueEl.textContent = `${state.preamp > 0 ? "+" : ""}${state.preamp} dB`;
  setEqBalance(Math.round(state.balance * 100), { save: false });
  populatePresetSelect(matchingPresetKey(state.bands));
})();

// #eq-pane is a permanent pane in #content (like #playlists-panel), not a popup, so this is just
// the same collapsible-body chevron every other panel here uses.
wireCollapseToggle(eqToggleBtn, eqPanelEl);

// queuePlayer.hidden is the existing single signal for "local <audio> isn't what's actually
// playing right now" (see updateCastUi/stopVideoPlayback/onYtStateChange below) - reuse it rather
// than tracking cast/video state a second time here.
function updateEqAvailability() {
  eqPanelEl.classList.toggle("eq-unavailable", queuePlayer.hidden);
  eqSliders.forEach((s) => {
    s.disabled = queuePlayer.hidden;
  });
  eqPreampSlider.disabled = queuePlayer.hidden;
  eqPresetSelectEl.disabled = queuePlayer.hidden;
  eqBypassBtn.disabled = queuePlayer.hidden;
  eqSavePresetBtn.disabled = queuePlayer.hidden;
  // Not a native form control, so there's no .disabled - pointer-events is blocked by the
  // #eq-panel.eq-unavailable CSS rule, and tabIndex -1 is what actually keeps it out of the
  // Tab order and blocks the keydown handler below (see its own "disabled" check).
  eqBalanceKnobEl.tabIndex = queuePlayer.hidden ? -1 : 0;
  // Volume now lives in this same pane (under Balance - see index.html), so it's dimmed and
  // blocked the same way as everything else here rather than needing its own hide/disable path:
  // Cast and the YouTube iframe both supply their own volume control, so this one would have
  // nothing to act on.
  queueVolumeKnobEl.tabIndex = queuePlayer.hidden ? -1 : 0;
}

let queue = []; // [{id, title, artist, file_path}]
let currentIndex = -1;
let queueDragFromIndex = null; // set during a queue-list drag-and-drop reorder, see renderQueue
// Video mode: not persisted across reloads (a session-only preference, unlike shuffle/repeat) -
// re-enable it each time rather than complicate queue-state restore with a third playback mode.
let videoModeEnabled = false;
let playingViaVideo = false; // true only while the current track is actually showing as video
// Locking the phone pauses queuePlayer with no code of ours calling .pause() - confirmed via the
// client log (a "pause" mid-track, then currentTime frozen through several hidden/visible cycles
// until a manual tap). pauseQueuePlayer() marks our own deliberate pauses (button, track-switch,
// cast handoff, ...) so the native "pause" listener below can tell those apart from the OS's and
// only auto-resume the latter once the page is actually foregrounded again.
let deliberatePause = false;
let awaitingBackgroundResume = false;
function pauseQueuePlayer() {
  deliberatePause = true;
  queuePlayer.pause();
}
let ytPlayer = null;
let ytApiReadyPromise = null;
let currentPlaylistId = null; // set when the queue was loaded from a saved playlist (see playTrackList)
let currentPlaylistName = null;
let lastResults = [];
let previousResults = null;

let shuffle = false;
let shuffledOrder = []; // indices into queue, in shuffled playback order (only meaningful when shuffle is true)
let repeatMode = "none"; // "none" -> "all" -> "one" -> "none"

const SEARCH_PAGE_SIZE = 25;
const STATS_PAGE_SIZE = 25;
let lastQuery = "";
let searchOffset = 0;
let searchHasMore = false;
let previousSearchHasMore = false;
// Fetches and appends the next page onto whatever's currently in lastResults - set to null
// for views with no pagination (Similar, Discography, Suggestions, ...), or to loadMoreSearch/
// a showStats closure for ones that do. Saved/restored alongside previousResults so "back"
// resumes the right pagination rather than always falling back to search's.
let loadMoreHandler = null;
let previousLoadMoreHandler = null;

const HISTORY_KEY = "pan_search_history";
const MAX_HISTORY = 12;

function loadHistory() {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY)) || [];
  } catch {
    return [];
  }
}

let searchHistory = loadHistory();

function addToHistory(q) {
  searchHistory = [q, ...searchHistory.filter((x) => x.toLowerCase() !== q.toLowerCase())].slice(0, MAX_HISTORY);
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(searchHistory));
  } catch {
    // localStorage unavailable (private browsing etc) - history just won't persist
  }
  renderHistory();
}

function renderHistory() {
  if (!searchHistory.length) {
    historyEl.innerHTML = "";
    return;
  }
  historyEl.innerHTML = "<span>Recent:</span> ";
  for (const q of searchHistory) {
    const chip = document.createElement("button");
    chip.className = "history-chip";
    chip.textContent = q;
    chip.addEventListener("click", () => {
      input.value = q;
      form.requestSubmit();
    });
    historyEl.appendChild(chip);
  }
  const clear = document.createElement("button");
  clear.className = "history-clear";
  clear.textContent = "clear";
  clear.addEventListener("click", () => {
    searchHistory = [];
    try {
      localStorage.removeItem(HISTORY_KEY);
    } catch {
      // ignore
    }
    renderHistory();
  });
  historyEl.appendChild(clear);
}

renderHistory();

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = input.value.trim();
  if (!q) return;

  addToHistory(q);
  lastQuery = q;
  searchOffset = 0;
  previousResults = null;
  contextBannerEl.hidden = true;
  resultsEl.innerHTML = "<li>Searching...</li>";
  resultsHeaderEl.hidden = true;
  loadMoreBtn.hidden = true;
  const res = await fetch(`/search?q=${encodeURIComponent(q)}&limit=${SEARCH_PAGE_SIZE}&offset=0`);
  const data = await res.json();
  lastResults = data.results;
  searchOffset = data.results.length;
  searchHasMore = data.results.length === SEARCH_PAGE_SIZE;
  renderResults(lastResults);
  loadMoreHandler = searchHasMore ? loadMoreSearch : null;
  loadMoreBtn.hidden = !searchHasMore;
});

async function loadMoreSearch() {
  loadMoreBtn.disabled = true;
  loadMoreBtn.textContent = "Loading...";
  const res = await fetch(`/search?q=${encodeURIComponent(lastQuery)}&limit=${SEARCH_PAGE_SIZE}&offset=${searchOffset}`);
  const data = await res.json();
  lastResults = lastResults.concat(data.results);
  searchOffset += data.results.length;
  searchHasMore = data.results.length === SEARCH_PAGE_SIZE;
  renderResults(lastResults);
  loadMoreBtn.disabled = false;
  loadMoreBtn.textContent = "Load more";
  loadMoreHandler = searchHasMore ? loadMoreSearch : null;
  loadMoreBtn.hidden = !searchHasMore;
}

loadMoreBtn.addEventListener("click", () => {
  if (loadMoreHandler) loadMoreHandler();
});

function showSimilar(track) {
  return showRelated(track, "similar", `Similar to "${track.title || "Untitled"}"`);
}

// Mirrors backend/search/engine.py's _base_title() (same two regexes, case preserved here since
// this is for display rather than comparison) - the versions search itself already ignores the
// "(Radio Edit)"/"[Club Mix]"/"- Remix" part of the title when matching, so showing the raw,
// unstripped title in the banner ("Other versions of \"Song (Specific Mix)\"") read as if it were
// searching for other versions of that one specific mix, when it's actually searching for any
// version of the underlying song.
function versionsBaseTitle(title) {
  if (!title) return "";
  return title
    .replace(/[([][^)\]]*[)\]]/g, "")
    .replace(/\s+-\s+.+$/, "")
    .trim();
}

function showVersions(track) {
  const baseTitle = versionsBaseTitle(track.title) || track.title || "Untitled";
  return showRelated(track, "versions", `Other versions of "${baseTitle}"`);
}

async function showRelated(track, endpointPath, bannerText) {
  const res = await fetch(`/track/${track.id}/${endpointPath}`);
  const data = await res.json();
  previousResults = lastResults;
  previousSearchHasMore = searchHasMore;
  previousLoadMoreHandler = loadMoreHandler;
  loadMoreHandler = null;
  loadMoreBtn.hidden = true;
  // Similar/Versions search the whole library with no awareness of what's already queued -
  // suggesting a track you've already got lined up (e.g. elsewhere in the playlist you're
  // looking at) isn't useful, so filter those out client-side.
  const queuedIds = new Set(queue.map((t) => t.id));
  lastResults = data.results.filter((r) => r.id !== track.id && !queuedIds.has(r.id));
  renderResults(lastResults);
  setContextBanner(bannerText, restorePreviousResults);
}

async function showDiscography(artist) {
  const res = await fetch(`/artist/discography?artist=${encodeURIComponent(artist)}`);
  const data = await res.json();
  previousResults = lastResults;
  previousSearchHasMore = searchHasMore;
  previousLoadMoreHandler = loadMoreHandler;
  loadMoreHandler = null;
  loadMoreBtn.hidden = true;
  resultsHeaderEl.hidden = true;
  renderAlbums(data.albums, artist);
  setContextBanner(`Discography: ${artist}`, restorePreviousResults);
}

function restorePreviousResults() {
  lastResults = previousResults;
  previousResults = null;
  contextBannerEl.hidden = true;
  searchHasMore = previousSearchHasMore;
  loadMoreHandler = previousLoadMoreHandler;
  loadMoreBtn.hidden = !searchHasMore;
  renderResults(lastResults);
}

function setContextBanner(bannerText, onBack) {
  contextBannerEl.hidden = false;
  contextBannerEl.innerHTML = "";
  const label = document.createElement("span");
  label.textContent = `${bannerText} — `;
  const backLink = document.createElement("button");
  backLink.className = "context-back";
  backLink.textContent = "back to search results";
  backLink.addEventListener("click", onBack);
  contextBannerEl.append(label, backLink);
}

function renderAlbums(albums, artist) {
  if (!albums.length) {
    resultsEl.innerHTML = "<li>No albums found.</li>";
    return;
  }
  resultsEl.innerHTML = "";
  for (const a of albums) {
    const li = document.createElement("li");

    const info = document.createElement("div");
    info.className = "track-info";
    const titleStrong = document.createElement("strong");
    titleStrong.textContent = a.album;
    info.appendChild(titleStrong);
    const meta = document.createElement("div");
    meta.className = "track-meta";
    meta.textContent = `${a.year || ""} · ${a.track_count} track${a.track_count === 1 ? "" : "s"}`;
    info.appendChild(meta);

    const playBtn = document.createElement("button");
    playBtn.textContent = "Play";
    playBtn.addEventListener("click", () => playArtistAlbum(artist, a.album));

    const playlistBtn = document.createElement("button");
    playlistBtn.textContent = "+ Playlist";
    playlistBtn.title = "Add this whole album to a playlist";
    const playlistPanel = buildPlaylistPickerPanel(() => fetchAlbumTrackIds(artist, a.album));
    playlistBtn.addEventListener("click", () => {
      playlistPanel.hidden = !playlistPanel.hidden;
      if (!playlistPanel.hidden) playlistPanel.load();
    });

    li.appendChild(info);
    li.appendChild(playBtn);
    li.appendChild(playlistBtn);
    li.appendChild(playlistPanel);
    resultsEl.appendChild(li);
  }
}

// "Title — Artist[, suffix]" with the artist rendered as a clickable discography link (when
// known) - shared by the results list and the now-playing bar, so playing a track gives the
// exact same "find more by this band" access a search result row already has.
function buildTrackTitleLine(track, suffix = "") {
  const frag = document.createDocumentFragment();
  const titleStrong = document.createElement("strong");
  titleStrong.textContent = track.title || "Untitled";
  frag.appendChild(titleStrong);
  frag.append(" — ");
  if (track.artist) {
    const artistLink = document.createElement("button");
    artistLink.className = "artist-link";
    artistLink.textContent = track.artist;
    artistLink.title = "Browse this artist's discography";
    artistLink.addEventListener("click", () => showDiscography(track.artist));
    frag.appendChild(artistLink);
  } else {
    frag.append("Unknown artist");
  }
  if (suffix) frag.append(suffix);
  return frag;
}

function renderResults(results) {
  if (!results.length) {
    resultsEl.innerHTML = "<li>No matches.</li>";
    resultsHeaderEl.hidden = true;
    return;
  }

  resultsHeaderEl.hidden = !results.some((r) => r.file_path);
  resultsEl.innerHTML = "";
  for (const r of results) {
    const li = document.createElement("li");

    if (r.file_path) {
      const art = document.createElement("img");
      art.className = "album-art";
      art.src = `/track/${r.id}/art`;
      art.alt = "";
      art.loading = "lazy";
      art.addEventListener("error", () => art.remove(), { once: true });
      li.appendChild(art);
    }

    const info = document.createElement("div");
    info.className = "track-info";

    const titleEl = document.createElement("div");
    titleEl.appendChild(buildTrackTitleLine(r));
    info.appendChild(titleEl);

    const metaEl = document.createElement("div");
    metaEl.className = "track-meta";
    if (r.album) {
      const albumLink = document.createElement("button");
      albumLink.className = "album-link";
      albumLink.textContent = r.album;
      albumLink.title = "Queue and play the whole album";
      albumLink.addEventListener("click", () => playAlbum(r));
      metaEl.appendChild(albumLink);
      metaEl.append(" ");
    }
    metaEl.append(`${r.year ? `(${r.year})` : ""} · ${r.source}`);
    if (r.play_count !== undefined) {
      const heard = r.avg_percent_listened != null ? `, avg ${Math.round(r.avg_percent_listened * 100)}% heard` : "";
      metaEl.append(` · played ${r.play_count}×${heard}`);
    }
    info.appendChild(metaEl);

    const tagsEl = document.createElement("div");
    tagsEl.className = "track-tags";
    setTagsText(tagsEl, r);
    info.appendChild(tagsEl);

    const playArea = document.createElement("div");
    playArea.className = "play-area";
    if (r.file_path) {
      const audio = document.createElement("audio");
      audio.controls = true;
      audio.src = `/stream/${r.id}`;
      audio.addEventListener("play", () => pauseOtherPlayers(audio));
      playArea.appendChild(audio);

      const queueBtn = document.createElement("button");
      queueBtn.className = "queue-btn";
      queueBtn.dataset.trackId = String(r.id);
      updateQueueButton(queueBtn, r.id);
      queueBtn.addEventListener("click", () => addToQueue(r));
      playArea.appendChild(queueBtn);
    } else {
      playArea.textContent = "(not playable here)";
    }

    appendDiscoveryActions(r, tagsEl, playArea, li);

    const dismissBtn = document.createElement("button");
    dismissBtn.textContent = "×";
    dismissBtn.className = "dismiss-btn";
    dismissBtn.title = "Remove from this list";
    dismissBtn.addEventListener("click", () => {
      lastResults = lastResults.filter((x) => x.id !== r.id);
      li.remove();
    });
    li.appendChild(dismissBtn);

    li.appendChild(info);
    li.appendChild(playArea);
    resultsEl.appendChild(li);
  }
}

// trackIdsSource is either a fixed array (single track), a sync function returning the current
// ids to add (e.g. "add all" needs the live result list, since it can grow via Load more or
// shrink via dismiss between opening the panel and clicking a playlist), or an async function
// (e.g. an album's tracks, which need a fetch to resolve to ids first).
function buildPlaylistPickerPanel(trackIdsSource) {
  const trackIds = async () => (typeof trackIdsSource === "function" ? await trackIdsSource() : trackIdsSource);

  const panel = document.createElement("div");
  panel.className = "playlist-picker-panel";
  panel.hidden = true;

  const listEl = document.createElement("div");
  listEl.className = "playlist-picker-list";

  const newBtn = document.createElement("button");
  newBtn.textContent = "+ New playlist";
  newBtn.addEventListener("click", async () => {
    const name = prompt("Playlist name?");
    if (!name || !name.trim()) return;
    await fetch("/playlists", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim(), track_ids: await trackIds() }),
    });
    loadPlaylists();
    panel.hidden = true;
  });

  // Always re-fetches on open (no "only load once" cache) - a playlist created from this same
  // panel, another row's panel, or the main Playlists sidebar should show up next time this
  // opens, not whatever list happened to exist the first time it was clicked.
  panel.load = async () => {
    listEl.textContent = "Loading...";
    const res = await fetch("/playlists");
    const data = await res.json();
    listEl.innerHTML = "";
    if (!data.playlists.length) {
      listEl.textContent = "No playlists yet.";
    } else {
      for (const p of data.playlists) {
        const btn = document.createElement("button");
        btn.textContent = p.name;
        btn.addEventListener("click", async () => {
          // Sequential, not Promise.all, so tracks land in the playlist in the same order
          // they're shown in the results.
          const failed = [];
          for (const id of await trackIds()) {
            const res = await fetch(`/playlists/${p.id}/tracks`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ track_id: id }),
            });
            if (!res.ok) failed.push(id);
          }
          loadPlaylists();
          panel.hidden = true;
          if (failed.length) {
            alert(`Couldn't add ${failed.length} track(s) to "${p.name}" - try again.`);
          }
          // If this playlist is the one currently loaded in the queue, pull in the track(s)
          // just added - otherwise "add to the playlist I'm playing" silently only updates the
          // saved playlist, not what's actually queued, which looks like the add didn't work.
          if (p.id === currentPlaylistId) {
            const res = await fetch(`/playlists/${p.id}`);
            const data = await res.json();
            const queuedIds = new Set(queue.map((t) => t.id));
            const newTracks = data.results.filter((t) => t.file_path && !queuedIds.has(t.id));
            if (newTracks.length) {
              queue = queue.concat(newTracks.map(toQueueEntry));
              renderQueue();
              persistQueueState();
            }
          }
        });
        listEl.appendChild(btn);
      }
    }
  };

  panel.append(listEl, newBtn);
  return panel;
}

async function playAlbum(track) {
  const res = await fetch(`/track/${track.id}/album`);
  const data = await res.json();
  playTrackList(data.results, `Album: "${track.album}" — ${track.artist || "Unknown artist"}`);
}

async function playArtistAlbum(artist, album) {
  const res = await fetch(`/artist/album?artist=${encodeURIComponent(artist)}&album=${encodeURIComponent(album)}`);
  const data = await res.json();
  playTrackList(data.results, `Album: "${album}" — ${artist}`);
}

// Album rows (discography view) only carry artist/album/track_count, not track ids - fetch the
// same way playArtistAlbum does, lazily, only once the +Playlist panel is actually opened.
async function fetchAlbumTrackIds(artist, album) {
  const res = await fetch(`/artist/album?artist=${encodeURIComponent(artist)}&album=${encodeURIComponent(album)}`);
  const data = await res.json();
  return data.results.filter((r) => r.file_path).map((r) => r.id);
}

// Used by both "play this album" paths. Keeps the tracks visible in the main results list (with
// their full set of controls - Info, Edit tags, Similar, etc.) rather than only in the compact
// queue list, which has none of that.
function playTrackList(results, bannerText, sourcePlaylist = null) {
  previousResults = lastResults;
  previousSearchHasMore = searchHasMore;
  previousLoadMoreHandler = loadMoreHandler;
  loadMoreHandler = null;
  loadMoreBtn.hidden = true;
  lastResults = results;
  renderResults(lastResults);
  queue = results.filter((r) => r.file_path).map(toQueueEntry);
  // Only set when loaded via playPlaylist (below) - lets "Save as playlist" offer to update
  // this same playlist instead of always creating a new one. Stays set across queue edits
  // (add/remove) within this session, so tweaking a playlist and saving updates it in place;
  // loading anything else (an album, a fresh search's "Play all") clears it, same as this
  // default null does when called without a third argument.
  currentPlaylistId = sourcePlaylist ? sourcePlaylist.id : null;
  currentPlaylistName = sourcePlaylist ? sourcePlaylist.name : null;
  renderQueue();
  playAt(0);
  setContextBanner(bannerText, restorePreviousResults);
}

function buildInfoPanel(r) {
  const panel = document.createElement("div");
  panel.className = "info-panel";
  panel.hidden = true;

  const factsEl = document.createElement("div");
  factsEl.className = "info-facts";
  factsEl.textContent = "Loading...";

  const aboutBtn = document.createElement("button");
  aboutBtn.textContent = "✨ Tell me more (AI)";
  const aboutEl = document.createElement("div");
  aboutEl.className = "info-about";

  // dataset.rawText keeps the original markdown source (for the Edit textarea) separate from
  // aboutEl's rendered DOM.
  function setAboutText(text) {
    aboutEl.dataset.rawText = text || "";
    if (text) {
      renderMarkdown(aboutEl, text);
    } else {
      aboutEl.textContent = "No background available (is ANTHROPIC_API_KEY configured?).";
    }
  }

  const aboutEditBtn = document.createElement("button");
  aboutEditBtn.textContent = "✎ Edit about";
  // Hidden until "Tell me more" has been opened at least once - editing a writeup that isn't
  // shown yet is confusing, and the button just clutters the row until then.
  aboutEditBtn.hidden = true;
  const aboutTextarea = document.createElement("textarea");
  aboutTextarea.className = "info-about-edit";
  aboutTextarea.hidden = true;
  const aboutSaveBtn = document.createElement("button");
  aboutSaveBtn.textContent = "💾 Save about";
  aboutSaveBtn.className = "save-btn";
  aboutSaveBtn.hidden = true;

  // Lets the writeup be hand-written or corrected directly - useful with no ANTHROPIC_API_KEY
  // configured, or when the AI's answer was close but not quite right.
  aboutEditBtn.addEventListener("click", () => {
    const editing = !aboutTextarea.hidden;
    if (editing) {
      aboutTextarea.hidden = true;
      aboutEl.hidden = false;
      aboutSaveBtn.hidden = true;
      aboutEditBtn.textContent = "✎ Edit about";
      return;
    }
    aboutTextarea.value = aboutEl.dataset.rawText || "";
    aboutTextarea.hidden = false;
    aboutEl.hidden = true;
    aboutSaveBtn.hidden = false;
    aboutEditBtn.textContent = "✕ Cancel editing about";
  });

  aboutSaveBtn.addEventListener("click", async () => {
    aboutSaveBtn.disabled = true;
    const res = await fetch(`/track/${r.id}/about`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: aboutTextarea.value }),
    });
    aboutSaveBtn.disabled = false;
    if (!res.ok) return;
    const data = await res.json();
    setAboutText(data.text);
    aboutTextarea.hidden = true;
    aboutEl.hidden = false;
    aboutSaveBtn.hidden = true;
    aboutEditBtn.textContent = "✎ Edit about";
  });

  const lyricsBtn = document.createElement("button");
  lyricsBtn.textContent = "🎤 Lyrics";
  const lyricsRefreshBtn = document.createElement("button");
  lyricsRefreshBtn.textContent = "🔄";
  lyricsRefreshBtn.title = "Re-check lyrics";
  lyricsRefreshBtn.hidden = true;
  const lyricsEl = document.createElement("div");
  lyricsEl.className = "info-lyrics";
  lyricsEl.hidden = true;
  // Holds lyricsEl plus (while shown) the reparented dismiss button - kept separate from lyricsEl
  // itself since renderLyrics() does `lyricsEl.innerHTML = ""` on every lookup/refresh, which
  // would otherwise wipe the button out along with the old lyrics text.
  const lyricsWrapper = document.createElement("div");
  lyricsWrapper.className = "lyrics-wrapper";
  lyricsWrapper.appendChild(lyricsEl);

  const lyricsEditBtn = document.createElement("button");
  lyricsEditBtn.textContent = "✎ Edit lyrics";
  // Hidden until "Lyrics" has been opened at least once - same reasoning as aboutEditBtn.
  lyricsEditBtn.hidden = true;
  const lyricsTextarea = document.createElement("textarea");
  lyricsTextarea.className = "info-lyrics-edit";
  lyricsTextarea.hidden = true;
  const lyricsSaveBtn = document.createElement("button");
  lyricsSaveBtn.textContent = "💾 Save lyrics";
  lyricsSaveBtn.className = "save-btn";
  lyricsSaveBtn.hidden = true;

  // Checks for a cached match first (backend/youtube.py) and opens that video directly; falls
  // back to a plain search (no YOUTUBE_API_KEY configured, or the text search itself found
  // nothing) rather than leaving the button dead.
  const videoBtn = document.createElement("button");
  videoBtn.textContent = "📺 Video";
  videoBtn.title = "Open this track on YouTube";
  videoBtn.addEventListener("click", async () => {
    videoBtn.disabled = true;
    try {
      const res = await fetch(`/track/${r.id}/video`);
      const data = await res.json();
      // watch_id survives even once video mode has exhausted every embeddable candidate (some
      // tracks have every one blocked by a rights holder's Content ID policy) - still the right
      // video to open directly, since that restriction doesn't apply to youtube.com itself.
      const id = data.watch_id || data.video_id;
      const url = id
        ? `https://www.youtube.com/watch?v=${id}`
        : `https://www.youtube.com/results?search_query=${encodeURIComponent(`${r.artist || ""} ${r.title || ""}`.trim())}`;
      window.open(url, "_blank", "noopener");
    } finally {
      videoBtn.disabled = false;
    }
  });

  // Both buttons sit in a row together - without this, the block-level aboutEl/lyricsEl divs
  // between them (even empty) would force each button onto its own line.
  const actionsRow = document.createElement("div");
  actionsRow.className = "info-actions";
  actionsRow.append(aboutBtn, aboutEditBtn, lyricsBtn, lyricsRefreshBtn, lyricsEditBtn, videoBtn);

  // Holds the active timeupdate listener for synced lyrics, so a second lookup (Refresh) or
  // this panel closing can tear down the previous one instead of leaking listeners on
  // queuePlayer forever.
  let syncTimeupdateHandler = null;
  function teardownSyncedLyrics() {
    if (syncTimeupdateHandler) {
      queuePlayer.removeEventListener("timeupdate", syncTimeupdateHandler);
      syncTimeupdateHandler = null;
    }
  }

  function renderLyrics(text, synced, fallbackMessage) {
    teardownSyncedLyrics();
    lyricsEl.innerHTML = "";
    lyricsEl.classList.remove("info-lyrics-synced");
    // Kept separate from the rendered DOM (plain text even when synced) so the Edit textarea
    // always starts from the underlying lyrics, not a reconstruction of the line-by-line markup.
    lyricsEl.dataset.rawText = text || "";

    if (!text) {
      lyricsEl.textContent = fallbackMessage;
      return;
    }

    const lines = synced ? parseLrc(synced) : [];
    if (!lines.length) {
      lyricsEl.textContent = text;
      return;
    }

    // Synced lyrics render as one element per line instead of plain text, so each line can be
    // highlighted as it plays and clicked to seek - like a karaoke display.
    lyricsEl.classList.add("info-lyrics-synced");
    const lineEls = lines.map((line) => {
      const el = document.createElement("div");
      el.className = "lyrics-line";
      el.textContent = line.text || " ";
      el.addEventListener("click", () => {
        if (queue[currentIndex] !== r) return;
        queuePlayer.currentTime = line.time;
        if (queuePlayer.paused) queuePlayer.play();
      });
      lyricsEl.appendChild(el);
      return el;
    });

    let lastActiveIndex = -1;
    syncTimeupdateHandler = () => {
      if (queue[currentIndex] !== r) return;
      const t = queuePlayer.currentTime;
      let activeIndex = -1;
      for (let i = 0; i < lines.length; i++) {
        if (lines[i].time <= t) activeIndex = i;
        else break;
      }
      if (activeIndex === lastActiveIndex) return;
      lastActiveIndex = activeIndex;
      lineEls.forEach((el, i) => el.classList.toggle("active", i === activeIndex));
      if (activeIndex < 0) return;
      // scrollIntoView walks every scrollable ancestor, including the whole page - that was
      // yanking the page back to the lyrics box on every line change and fighting any attempt to
      // scroll away (e.g. to keep browsing search results) until the track ended. Scroll just the
      // lyrics panel itself instead.
      const activeEl = lineEls[activeIndex];
      lyricsEl.scrollTo({
        top: activeEl.offsetTop - lyricsEl.clientHeight / 2 + activeEl.clientHeight / 2,
        behavior: "smooth",
      });
    };
    queuePlayer.addEventListener("timeupdate", syncTimeupdateHandler);
  }

  // The main button is purely a show/hide toggle once lyrics have been loaded once - it never
  // re-fetches (that's what the separate small refresh button is for), so hiding and re-showing
  // is instant and never costs another lrclib request or risks a rate limit.
  let lyricsLoaded = false;

  async function lookupLyrics(refresh) {
    try {
      const res = await fetch(`/track/${r.id}/lyrics${refresh ? "?refresh=true" : ""}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.source === "error" && lyricsLoaded) {
        // A failed refresh (e.g. lrclib rate-limited) isn't cached server-side, unlike a
        // confirmed "none" - and must not blow away lyrics already shown from the last
        // successful lookup. Report the failure without touching what's displayed.
        return "transient-error";
      }
      const fallback = data.source === "error" ? "Lookup failed - try again." : "No lyrics found.";
      renderLyrics(data.text, data.synced, fallback);
      lyricsLoaded = true;
      return "ok";
    } catch (err) {
      if (lyricsLoaded) return "transient-error";
      teardownSyncedLyrics();
      lyricsEl.textContent = "Lyrics lookup failed.";
      lyricsLoaded = true;
      return "ok";
    }
  }

  // Moves lyricsBtn between actionsRow (labelled trigger) and lyricsWrapper's corner (compact
  // dismiss) - same reasoning as Info/Edit tags: a small "×" packed into actionsRow next to
  // Edit lyrics/Video was an easy misclick, pinning it to its own panel's corner isn't.
  function setLyricsShown(shown) {
    lyricsEl.hidden = !shown;
    lyricsRefreshBtn.hidden = !shown;
    lyricsBtn.textContent = shown ? "×" : "🎤 Lyrics";
    lyricsBtn.title = shown ? "Hide lyrics" : "";
    lyricsBtn.classList.toggle("inline-dismiss-btn", shown);
    if (shown) lyricsWrapper.appendChild(lyricsBtn);
    else actionsRow.insertBefore(lyricsBtn, lyricsRefreshBtn);
  }

  lyricsBtn.addEventListener("click", async () => {
    if (lyricsLoaded) {
      // Already fetched once this session - just toggle visibility, no network call.
      setLyricsShown(lyricsEl.hidden);
      return;
    }
    lyricsBtn.disabled = true;
    lyricsBtn.textContent = "⏳ Looking up...";
    lyricsEl.hidden = false;
    await lookupLyrics(false);
    lyricsBtn.disabled = false;
    setLyricsShown(lyricsLoaded);
    lyricsEditBtn.hidden = !lyricsLoaded;
  });

  // Separate from the show/hide toggle above, so retrying a stale/failed lookup - e.g. after
  // correcting a mislabeled artist/title - never gets confused with just hiding the lyrics.
  lyricsRefreshBtn.addEventListener("click", async () => {
    lyricsRefreshBtn.disabled = true;
    lyricsRefreshBtn.textContent = "⏳";
    const result = await lookupLyrics(true);
    lyricsRefreshBtn.disabled = false;
    lyricsRefreshBtn.textContent = result === "transient-error" ? "⚠" : "🔄";
    if (result === "transient-error") setTimeout(() => { lyricsRefreshBtn.textContent = "🔄"; }, 2500);
  });

  // Lets lyrics be hand-pasted or corrected directly - useful when lrclib doesn't have the
  // track, is rate-limited, or got it wrong. Saving always clears any synced (LRC) timestamps
  // server-side, since hand-pasted plain text can't carry them.
  lyricsEditBtn.addEventListener("click", () => {
    const editing = !lyricsTextarea.hidden;
    if (editing) {
      lyricsTextarea.hidden = true;
      lyricsEl.hidden = !lyricsLoaded;
      lyricsSaveBtn.hidden = true;
      lyricsEditBtn.textContent = "✎ Edit lyrics";
      return;
    }
    lyricsTextarea.value = lyricsEl.dataset.rawText || "";
    lyricsTextarea.hidden = false;
    lyricsEl.hidden = true;
    lyricsSaveBtn.hidden = false;
    lyricsEditBtn.textContent = "✕ Cancel editing lyrics";
  });

  lyricsSaveBtn.addEventListener("click", async () => {
    lyricsSaveBtn.disabled = true;
    const res = await fetch(`/track/${r.id}/lyrics`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: lyricsTextarea.value }),
    });
    lyricsSaveBtn.disabled = false;
    if (!res.ok) return;
    const data = await res.json();
    renderLyrics(data.text, null, "No lyrics found.");
    lyricsLoaded = true;
    lyricsTextarea.hidden = true;
    lyricsSaveBtn.hidden = true;
    lyricsEditBtn.textContent = "✎ Edit lyrics";
    setLyricsShown(true);
  });

  // Stays visible (not hidden after first use) and re-labels to "Regenerate" so a stale cached
  // writeup - e.g. generated before a mislabeled artist/title got corrected - can be refreshed
  // rather than stuck forever (the backend caches ai_background indefinitely to save API calls).
  aboutBtn.addEventListener("click", async () => {
    const refreshing = aboutBtn.dataset.loaded === "true";
    aboutBtn.disabled = true;
    aboutBtn.textContent = "⏳ Thinking...";
    const res = await fetch(`/track/${r.id}/about${refreshing ? "?refresh=true" : ""}`);
    const data = await res.json();
    setAboutText(data.text);
    aboutBtn.textContent = "🔄 Regenerate";
    aboutBtn.dataset.loaded = "true";
    aboutBtn.disabled = false;
    aboutEditBtn.hidden = false;
  });

  // Always re-fetches on open (no "only load once" cache) - tag/title/artist edits change the
  // source data (e.g. tag_source flips to "manual") and this panel should never show a stale
  // snapshot from before the edit.
  panel.load = async () => {
    factsEl.textContent = "Loading...";
    const res = await fetch(`/track/${r.id}`);
    const d = await res.json();
    factsEl.innerHTML = "";
    const rows = [
      ["Tempo", d.tempo ? `${Math.round(d.tempo)} BPM` : null],
      ["Key", d.musical_key],
      ["Energy", d.energy != null ? `${Math.round(d.energy * 100)}%` : null],
      ["Duration", d.duration_ms ? formatDuration(d.duration_ms) : null],
      ["Format", d.file_format],
      ["File size", d.file_size_bytes ? formatBytes(d.file_size_bytes) : null],
      ["Tag source", d.tag_source],
      ["Path", d.file_path],
    ];
    for (const [label, value] of rows) {
      if (!value) continue;
      const row = document.createElement("div");
      if (label === "Path") row.className = "info-path";
      row.innerHTML = `<span class="info-label">${label}:</span> ${escapeHtml(String(value))}`;
      factsEl.appendChild(row);
    }
    if (!factsEl.children.length) factsEl.textContent = "No technical details available.";
  };

  panel.onHide = teardownSyncedLyrics;
  panel.append(factsEl, actionsRow, aboutEl, aboutTextarea, aboutSaveBtn, lyricsWrapper, lyricsTextarea, lyricsSaveBtn);
  return panel;
}

function formatDuration(ms) {
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function formatBytes(bytes) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// lrclib's LRC format: one "[mm:ss.xx] text" line per lyric line.
function parseLrc(lrc) {
  const lines = [];
  for (const raw of lrc.split("\n")) {
    const m = raw.match(/^\[(\d+):(\d+(?:\.\d+)?)\](.*)$/);
    if (!m) continue;
    lines.push({ time: parseInt(m[1], 10) * 60 + parseFloat(m[2]), text: m[3].trim() });
  }
  return lines;
}

// Minimal markdown rendering for the AI "tell me more" writeup - just the subset Claude's short
// prose responses realistically use (paragraphs, **bold**, *italic*, "- " bullet lists), not a
// general-purpose parser pulled in as a new dependency.
function renderMarkdown(container, text) {
  container.innerHTML = "";

  const renderInline = (el, str) => {
    for (const part of str.split(/(\*\*[^*]+\*\*|\*[^*]+\*|_[^_]+_)/g)) {
      if (!part) continue;
      if (part.startsWith("**") && part.endsWith("**")) {
        const strong = document.createElement("strong");
        strong.textContent = part.slice(2, -2);
        el.appendChild(strong);
      } else if ((part.startsWith("*") && part.endsWith("*")) || (part.startsWith("_") && part.endsWith("_"))) {
        const em = document.createElement("em");
        em.textContent = part.slice(1, -1);
        el.appendChild(em);
      } else {
        el.appendChild(document.createTextNode(part));
      }
    }
  };

  let listEl = null;
  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim();
    if (!line) {
      listEl = null;
      continue;
    }
    const bullet = line.match(/^[-*]\s+(.*)$/);
    if (bullet) {
      if (!listEl) {
        listEl = document.createElement("ul");
        container.appendChild(listEl);
      }
      const li = document.createElement("li");
      renderInline(li, bullet[1]);
      listEl.appendChild(li);
      continue;
    }
    listEl = null;
    const p = document.createElement("p");
    renderInline(p, line);
    container.appendChild(p);
  }
}

// Genre/mood/tags as clickable chips - same "find more like this" idea as the artist/album
// links elsewhere, routed through an exact hard-filter search (see search_by_tag) rather than
// the fuzzy/semantic search box, so clicking "Ska" reliably returns everything tagged Ska.
function setTagsText(tagsEl, r) {
  tagsEl.innerHTML = "";
  if (r.genre) tagsEl.appendChild(buildTagLink(r.genre, "genre", r.genre));
  if (r.mood) tagsEl.appendChild(buildTagLink(r.mood, "mood", r.mood));
  for (const t of r.tags || []) {
    tagsEl.appendChild(buildTagLink(`#${t}`, "tag", t));
  }
}

function buildTagLink(label, field, value) {
  const btn = document.createElement("button");
  btn.className = "tag-link";
  btn.textContent = label;
  btn.title = `Find more tagged "${value}"`;
  btn.addEventListener("click", () => showByTag(field, value));
  return btn;
}

async function showByTag(field, value) {
  const res = await fetch(`/search/by-tag?field=${encodeURIComponent(field)}&value=${encodeURIComponent(value)}`);
  const data = await res.json();
  previousResults = lastResults;
  previousSearchHasMore = searchHasMore;
  previousLoadMoreHandler = loadMoreHandler;
  loadMoreHandler = null;
  loadMoreBtn.hidden = true;
  lastResults = data.results;
  renderResults(lastResults);
  setContextBanner(`Tagged: ${field === "tag" ? "#" : ""}${value}`, restorePreviousResults);
}

// A small add/remove chip editor for the freeform tags list (separate from genre/mood, which
// are single-value fields). Returns the element plus getters/setters so buildEditPanel can read
// the current list on Save and refresh it after.
function buildTagChipsEditor(initialTags) {
  let tags = [...(initialTags || [])];

  const wrap = document.createElement("div");
  wrap.className = "tag-chips-editor";

  const chipsEl = document.createElement("div");
  chipsEl.className = "tag-chips";

  function render() {
    chipsEl.innerHTML = "";
    for (const tag of tags) {
      const chip = document.createElement("span");
      chip.className = "tag-chip";
      chip.textContent = tag;
      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.textContent = "×";
      removeBtn.title = `Remove "${tag}"`;
      removeBtn.addEventListener("click", () => {
        tags = tags.filter((t) => t !== tag);
        render();
      });
      chip.appendChild(removeBtn);
      chipsEl.appendChild(chip);
    }
  }
  render();

  const input = document.createElement("input");
  input.placeholder = "Add tag, Enter to add";
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const value = input.value.trim().toLowerCase();
    if (value && !tags.includes(value)) {
      tags.push(value);
      render();
    }
    input.value = "";
  });

  wrap.append(chipsEl, input);
  return {
    el: wrap,
    getTags: () => tags,
    setTags: (next) => {
      tags = [...next];
      render();
    },
  };
}

// Shared by search result rows and the queue panel's "now playing" track, so both get the
// same Similar/Versions/Info/Edit tags/+Playlist actions instead of search results being the
// only place you can do anything with a track.
function appendDiscoveryActions(r, tagsEl, buttonHost, panelHost) {
  const favoriteBtn = document.createElement("button");
  favoriteBtn.className = "favorite-btn";
  favoriteBtn.title = r.favorite ? "Remove from favorites" : "Add to favorites";
  favoriteBtn.textContent = r.favorite ? "♥" : "♡";
  favoriteBtn.classList.toggle("is-favorite", !!r.favorite);
  favoriteBtn.addEventListener("click", async () => {
    const next = !r.favorite;
    favoriteBtn.disabled = true;
    const res = await fetch(`/track/${r.id}/favorite`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ favorite: next }),
    });
    favoriteBtn.disabled = false;
    if (!res.ok) return;
    r.favorite = next;
    favoriteBtn.title = r.favorite ? "Remove from favorites" : "Add to favorites";
    favoriteBtn.textContent = r.favorite ? "♥" : "♡";
    favoriteBtn.classList.toggle("is-favorite", r.favorite);
    // Mirrors the server's mutual-exclusion: favoriting clears dislike and vice versa.
    if (r.favorite && r.dislike) {
      r.dislike = false;
      dislikeBtn.title = "Dislike";
      dislikeBtn.classList.remove("is-disliked");
    }
  });
  buttonHost.appendChild(favoriteBtn);

  const dislikeBtn = document.createElement("button");
  dislikeBtn.className = "dislike-btn";
  dislikeBtn.title = r.dislike ? "Remove dislike" : "Dislike";
  dislikeBtn.textContent = "\u{1F44E}";
  dislikeBtn.classList.toggle("is-disliked", !!r.dislike);
  dislikeBtn.addEventListener("click", async () => {
    const next = !r.dislike;
    dislikeBtn.disabled = true;
    const res = await fetch(`/track/${r.id}/dislike`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dislike: next }),
    });
    dislikeBtn.disabled = false;
    if (!res.ok) return;
    r.dislike = next;
    dislikeBtn.title = r.dislike ? "Remove dislike" : "Dislike";
    dislikeBtn.classList.toggle("is-disliked", r.dislike);
    // Mirrors the server's mutual-exclusion: disliking clears favorite and vice versa.
    if (r.dislike && r.favorite) {
      r.favorite = false;
      favoriteBtn.title = "Add to favorites";
      favoriteBtn.textContent = "♡";
      favoriteBtn.classList.remove("is-favorite");
    }
  });
  buttonHost.appendChild(dislikeBtn);

  const similarBtn = document.createElement("button");
  similarBtn.textContent = "Similar";
  similarBtn.title = "Find tracks like this one";
  similarBtn.addEventListener("click", () => showSimilar(r));
  buttonHost.appendChild(similarBtn);

  if (looksLikeVersionedTitle(r.title)) {
    const versionsBtn = document.createElement("button");
    versionsBtn.textContent = "Versions";
    versionsBtn.title = "Other mixes/edits/remixes of this song";
    versionsBtn.addEventListener("click", () => showVersions(r));
    buttonHost.appendChild(versionsBtn);
  }

  const infoBtn = document.createElement("button");
  infoBtn.textContent = "Info";
  infoBtn.title = "Track/album/artist details";
  const infoPanel = buildInfoPanel(r);
  infoBtn.addEventListener("click", async () => {
    infoPanel.hidden = !infoPanel.hidden;
    // Same reasoning as the lyrics toggle: a labelled trigger to open it, a compact close
    // affordance once it's open and already visible.
    infoBtn.textContent = infoPanel.hidden ? "Info" : "×";
    infoBtn.title = infoPanel.hidden ? "Track/album/artist details" : "Hide info";
    infoBtn.classList.toggle("inline-dismiss-btn", !infoPanel.hidden);
    if (infoPanel.hidden) {
      infoPanel.onHide();
      // Restore it to its usual spot in the row's button toolbar, right before Edit tags.
      buttonHost.insertBefore(infoBtn, editBtn);
    } else {
      await infoPanel.load();
      // Appended directly to the panel (not factsEl, whose innerHTML load() rebuilds from
      // scratch on every open) and pinned to the panel's top-right corner via CSS - same
      // positioning as .dismiss-btn on a track card.
      infoPanel.appendChild(infoBtn);
    }
  });
  buttonHost.appendChild(infoBtn);
  panelHost.appendChild(infoPanel);

  const editBtn = document.createElement("button");
  editBtn.textContent = "Tags";
  editBtn.className = "edit-tags-btn";
  const editPanel = buildEditPanel(r, tagsEl);
  // Shared by the toggle click below and a successful Save (buildEditPanel calls this directly)
  // so both paths move editBtn back to its spot in the toolbar the same way - Save used to only
  // reset its text/class and leave the button itself stranded inside the now-hidden panel.
  editPanel.collapse = () => {
    editPanel.hidden = true;
    editBtn.textContent = "Tags";
    editBtn.title = "";
    editBtn.classList.remove("inline-dismiss-btn");
    buttonHost.insertBefore(editBtn, playlistBtn);
  };
  editBtn.addEventListener("click", () => {
    // Same treatment as Info: a labelled trigger to open it, a compact dismiss pinned to the
    // panel's own corner once it's open - not left inline in the crowded button row, where a
    // packed-in small "×" next to +Playlist was an easy misclick (clicks landing on the
    // neighbour instead of closing the panel).
    if (!editPanel.hidden) {
      editPanel.collapse();
      return;
    }
    editPanel.hidden = false;
    editBtn.textContent = "×";
    editBtn.title = "Cancel editing tags";
    editBtn.classList.add("inline-dismiss-btn");
    editPanel.appendChild(editBtn);
  });
  buttonHost.appendChild(editBtn);
  panelHost.appendChild(editPanel);

  const playlistBtn = document.createElement("button");
  playlistBtn.textContent = "+ Playlist";
  playlistBtn.title = "Add this track to a playlist";
  const playlistPanel = buildPlaylistPickerPanel([r.id]);
  playlistBtn.addEventListener("click", () => {
    playlistPanel.hidden = !playlistPanel.hidden;
    if (!playlistPanel.hidden) playlistPanel.load();
  });
  buttonHost.appendChild(playlistBtn);
  panelHost.appendChild(playlistPanel);
}

function renderNowPlayingActions(track) {
  nowPlayingActionsEl.innerHTML = "";
  setNowPlayingArt(track);
  if (!track) {
    nowPlayingActionsEl.hidden = true;
    return;
  }
  nowPlayingActionsEl.hidden = false;

  const tagsEl = document.createElement("span");
  tagsEl.className = "track-tags";
  setTagsText(tagsEl, track);
  nowPlayingActionsEl.appendChild(tagsEl);

  const buttonRow = document.createElement("div");
  buttonRow.className = "play-area";
  const panelHost = document.createElement("div");
  appendDiscoveryActions(track, tagsEl, buttonRow, panelHost);
  nowPlayingActionsEl.append(buttonRow, panelHost);
}

// #queue-art is a single persistent <img> (see the comment above #queue-top in index.html) that
// this just points at the current track's art and shows/hides - never created or removed, unlike
// the old approach of building a fresh <img> per track inside #queue-now-playing-actions, which
// made that row's own height (and therefore everything below it) depend on whether the current
// track happened to have art. Re-assigning .src on the same <img> element aborts any still-
// in-flight previous request, so a slow/stale response from a few tracks ago can't land here
// and flip visibility back on after a newer, art-less track has already hidden it.
function setNowPlayingArt(track) {
  queueArtEl.style.visibility = "hidden";
  if (track && track.file_path) {
    queueArtEl.src = `/track/${track.id}/art`;
  }
}
queueArtEl.addEventListener("load", () => {
  queueArtEl.style.visibility = "visible";
});
queueArtEl.addEventListener("error", () => {
  queueArtEl.style.visibility = "hidden";
});

// Cheap, no-extra-query heuristic for whether the "Versions" button is worth showing: does
// this track's own title already look like a mix/edit/remix (mirrors backend _base_title's
// stripping logic)? Under-shows rather than over-shows - a plain-titled original won't get the
// button even if remixes of it exist elsewhere, but every remix-titled track will.
function looksLikeVersionedTitle(title) {
  if (!title) return false;
  const stripped = title.replace(/[\(\[][^\)\]]*[\)\]]/g, "").replace(/\s+-\s+.+$/, "").trim();
  return stripped.toLowerCase() !== title.trim().toLowerCase();
}

function buildEditPanel(r, tagsEl) {
  const panel = document.createElement("div");
  panel.className = "edit-panel";
  panel.hidden = true;

  const titleInput = document.createElement("input");
  titleInput.placeholder = "Title";
  titleInput.value = r.title || "";

  const artistInput = document.createElement("input");
  artistInput.placeholder = "Artist";
  artistInput.value = r.artist || "";

  const albumInput = document.createElement("input");
  albumInput.placeholder = "Album";
  albumInput.value = r.album || "";

  const genreInput = document.createElement("input");
  genreInput.placeholder = "Genre";
  genreInput.value = r.genre || "";

  const moodInput = document.createElement("input");
  moodInput.placeholder = "Mood";
  moodInput.value = r.mood || "";

  const tagsEditor = buildTagChipsEditor(r.tags || []);

  const suggestBtn = document.createElement("button");
  suggestBtn.textContent = "Suggest";
  const suggestionsEl = document.createElement("div");
  suggestionsEl.className = "suggestions";

  suggestBtn.addEventListener("click", async () => {
    suggestBtn.disabled = true;
    suggestBtn.textContent = "Thinking...";
    suggestionsEl.innerHTML = "";
    try {
      const res = await fetch(`/track/${r.id}/suggest-tags`);
      const data = await res.json();
      renderSuggestionChips(suggestionsEl, "Genre", data.genres, genreInput);
      renderSuggestionChips(suggestionsEl, "Mood", data.moods, moodInput);
      if (!data.genres.length && !data.moods.length) {
        suggestionsEl.textContent = "No suggestions available (is ANTHROPIC_API_KEY configured?).";
      }
    } finally {
      suggestBtn.disabled = false;
      suggestBtn.textContent = "Suggest";
    }
  });

  const saveBtn = document.createElement("button");
  saveBtn.textContent = "Save";
  saveBtn.className = "save-btn";
  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    const res = await fetch(`/track/${r.id}/tags`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: titleInput.value.trim() || null,
        artist: artistInput.value.trim() || null,
        album: albumInput.value.trim() || null,
        genre: genreInput.value.trim() || null,
        mood: moodInput.value.trim() || null,
        tags: tagsEditor.getTags(),
      }),
    });
    saveBtn.disabled = false;
    if (!res.ok) {
      alert(`Couldn't save: ${res.status} ${res.statusText}`);
      return;
    }
    const updated = await res.json();
    const identityChanged = r.artist !== updated.artist || r.title !== updated.title || r.album !== updated.album;
    r.title = updated.title;
    r.artist = updated.artist;
    r.album = updated.album;
    r.genre = updated.genre;
    r.mood = updated.mood;
    r.tags = updated.tags;
    tagsEditor.setTags(updated.tags);
    setTagsText(tagsEl, r);
    panel.collapse();
    // The title/artist are shown in several places built from this same track object (the
    // results list, the queue list, the now-playing bar) - re-render whichever of those
    // currently hold it rather than patching each one's DOM by hand.
    if (identityChanged) {
      if (lastResults.includes(r)) renderResults(lastResults);
      if (queue.includes(r)) renderQueue();
      if (queue[currentIndex] === r) {
        nowPlayingEl.innerHTML = "";
        nowPlayingEl.appendChild(buildTrackTitleLine(r));
      }
    }
  });

  const row1 = document.createElement("div");
  row1.className = "edit-row";
  row1.append(titleInput, artistInput, albumInput, genreInput, moodInput, suggestBtn, saveBtn);

  panel.append(row1, tagsEditor.el, suggestionsEl);
  return panel;
}

function renderSuggestionChips(container, label, options, targetInput) {
  if (!options.length) return;
  const group = document.createElement("div");
  group.className = "suggestion-group";
  const labelEl = document.createElement("span");
  labelEl.textContent = `${label}: `;
  group.appendChild(labelEl);
  for (const option of options) {
    const chip = document.createElement("button");
    chip.className = "suggestion-chip";
    chip.textContent = option;
    chip.addEventListener("click", () => {
      targetInput.value = option;
    });
    group.appendChild(chip);
  }
  container.appendChild(group);
}

function pauseOtherPlayers(current) {
  for (const audio of resultsEl.querySelectorAll("audio")) {
    if (audio !== current) audio.pause();
  }
  if (current !== queuePlayer) pauseQueuePlayer();
}

playAllBtn.addEventListener("click", () => {
  const playable = lastResults.filter((r) => r.file_path);
  queue = playable.map(toQueueEntry);
  currentPlaylistId = null;
  currentPlaylistName = null;
  renderQueue();
  playAt(0);
});

// Reads lastResults live (not a snapshot at click time) so it stays correct across Load more
// and dismiss. Only playable tracks, matching playAllBtn's behavior above.
const playlistAllPanel = buildPlaylistPickerPanel(() => lastResults.filter((r) => r.file_path).map((r) => r.id));
resultsHeaderEl.appendChild(playlistAllPanel);
playlistAllBtn.addEventListener("click", () => {
  playlistAllPanel.hidden = !playlistAllPanel.hidden;
  if (!playlistAllPanel.hidden) playlistAllPanel.load();
});

function addToQueue(track) {
  queue.push(toQueueEntry(track));
  renderQueue();
  if (currentIndex === -1) playAt(0);
}

function isQueued(trackId) {
  return queue.some((t) => t.id === trackId);
}

// A track can get queued/removed from several places (this button, the queue panel's own ×,
// Clear) - rather than tracking all of them, every queue mutation funnels through renderQueue(),
// so refreshing every visible +Queue button there keeps them all in sync regardless of source.
function updateQueueButton(btn, trackId) {
  const queued = isQueued(trackId);
  btn.textContent = queued ? "✓ Queued" : "+ Queue";
  btn.classList.toggle("is-queued", queued);
  btn.title = queued ? "Already in the queue - click to add another copy" : "Add to queue";
}

function refreshQueueButtons() {
  for (const btn of document.querySelectorAll(".queue-btn")) {
    updateQueueButton(btn, Number(btn.dataset.trackId));
  }
}

function updateSaveControls() {
  saveBtn.title = currentPlaylistId ? `Update "${currentPlaylistName}"` : "Save as playlist";
  // Only meaningful once a playlist is actually loaded - saving a fresh queue already creates
  // a new playlist via the Save button itself, so a separate clone action would be redundant.
  cloneBtn.hidden = !currentPlaylistId;
}

function toQueueEntry(r) {
  // Keep the full result object (not just id/title/artist/file_path) so the "now playing" bar
  // can offer the same Similar/Info/Edit tags/+Playlist actions search results have, including
  // genre/mood pre-filled correctly in the edit panel.
  return r;
}

// Shuffle doesn't reorder the visible queue list - it only changes which track next/prev
// moves to, via a separate permutation of queue indices. Rebuilding is keyed off the queue's
// length changing, which covers add/remove without needing an explicit dirty flag; the
// current track is always pinned first so toggling shuffle on mid-playback doesn't jump away
// from what's currently playing.
function rebuildShuffleOrder() {
  const indices = queue.map((_, i) => i);
  for (let i = indices.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [indices[i], indices[j]] = [indices[j], indices[i]];
  }
  if (currentIndex !== -1) {
    const pos = indices.indexOf(currentIndex);
    if (pos > 0) {
      indices.splice(pos, 1);
      indices.unshift(currentIndex);
    }
  }
  shuffledOrder = indices;
}

function nextQueueIndex(i) {
  if (shuffle) {
    if (shuffledOrder.length !== queue.length) rebuildShuffleOrder();
    const pos = shuffledOrder.indexOf(i);
    if (pos === -1 || pos + 1 >= shuffledOrder.length) return repeatMode === "all" ? shuffledOrder[0] ?? -1 : -1;
    return shuffledOrder[pos + 1];
  }
  if (i + 1 < queue.length) return i + 1;
  return repeatMode === "all" && queue.length ? 0 : -1;
}

function prevQueueIndex(i) {
  if (shuffle) {
    if (shuffledOrder.length !== queue.length) rebuildShuffleOrder();
    const pos = shuffledOrder.indexOf(i);
    return pos > 0 ? shuffledOrder[pos - 1] : -1;
  }
  return i - 1 >= 0 ? i - 1 : -1;
}

function updateShuffleRepeatButtons() {
  shuffleBtn.classList.toggle("is-active", shuffle);
  shuffleBtn.title = shuffle ? "Shuffle on" : "Shuffle";
  const repeatTitles = { none: "Repeat off", all: "Repeat all", one: "Repeat one" };
  repeatBtn.title = repeatTitles[repeatMode];
  repeatBtn.classList.toggle("is-active", repeatMode !== "none");
  // Plain Unicode glyph (not an emoji) so .is-active's color actually applies to it, same as
  // the other transport buttons - color emoji ignore CSS `color` entirely, which is why shuffle
  // and repeat used to look like they didn't match the rest of the row's active-state styling.
  repeatBtn.textContent = repeatMode === "one" ? "↻¹" : "↻";
}

shuffleBtn.addEventListener("click", () => {
  shuffle = !shuffle;
  if (shuffle) rebuildShuffleOrder();
  updateShuffleRepeatButtons();
  persistQueueState();
});

repeatBtn.addEventListener("click", () => {
  repeatMode = { none: "all", all: "one", one: "none" }[repeatMode];
  updateShuffleRepeatButtons();
  persistQueueState();
});

videoModeBtn.addEventListener("click", () => {
  videoModeEnabled = !videoModeEnabled;
  videoModeBtn.classList.toggle("is-active", videoModeEnabled);
  videoModeBtn.setAttribute("aria-pressed", String(videoModeEnabled));
  if (!videoModeEnabled) {
    stopVideoPlayback();
  } else if (currentIndex !== -1 && !isCasting()) {
    playAt(currentIndex); // re-triggers so the current track picks up video mode immediately
  }
});

// Loaded lazily (only once video mode is actually used) rather than unconditionally in
// index.html, so people who never touch video mode don't pay for the extra script.
function ensureYtApi() {
  if (ytApiReadyPromise) return ytApiReadyPromise;
  ytApiReadyPromise = new Promise((resolve) => {
    if (window.YT && window.YT.Player) return resolve();
    window.onYouTubeIframeAPIReady = resolve;
    const tag = document.createElement("script");
    tag.src = "https://www.youtube.com/iframe_api";
    document.head.appendChild(tag);
  });
  return ytApiReadyPromise;
}

async function ensureYtPlayer() {
  await ensureYtApi();
  if (ytPlayer) return ytPlayer;
  return new Promise((resolve) => {
    ytPlayer = new YT.Player("youtube-player", {
      height: "100%",
      width: "100%",
      playerVars: { playsinline: 1 },
      events: {
        onReady: () => resolve(ytPlayer),
        onStateChange: onYtStateChange,
        onError: onYtError,
      },
    });
  });
}

// Mirrors the <audio> "ended"/"play"/"pause" listeners further down, so repeat/shuffle and the
// play/pause button icon + Media Session state behave the same regardless of playback mode.
function onYtStateChange(event) {
  if (event.data === YT.PlayerState.ENDED) {
    advanceOnEnded();
  } else if (event.data === YT.PlayerState.PLAYING) {
    // Only reveal the video here, on an actual confirmed-playing state - not the moment we start
    // trying (see tryPlayVideo). A video that's going to hit an embed error (onYtError) usually
    // does so before ever reaching PLAYING, so waiting for this avoids a flash of a video that's
    // about to be yanked away and replaced with audio a moment later.
    if (playingViaVideo) {
      queuePlayer.hidden = true;
      youtubePlayerWrap.hidden = false;
      updateEqAvailability();
    }
    playPauseBtn.innerHTML = "&#10074;&#10074;";
    if ("mediaSession" in navigator) navigator.mediaSession.playbackState = "playing";
  } else if (event.data === YT.PlayerState.PAUSED) {
    playPauseBtn.innerHTML = "&#9654;";
    if ("mediaSession" in navigator) navigator.mediaSession.playbackState = "paused";
  }
}

// The YouTube Data API's "embeddable" flag (used when picking a match - see backend/youtube.py)
// isn't fully reliable for some official/label-managed uploads: it can say true while the
// actual player still refuses to play it. There's no way to know this in advance short of
// actually trying to embed it, so self-heal here instead - 100/101/150 all mean "can never play
// this here" (removed/private, or embedding disallowed by the owner under one of two historical
// error codes for the same thing), so dismiss it the same as the manual button and fall back.
function onYtError(event) {
  if ([100, 101, 150].includes(event.data)) {
    logClient("video_error", { youtubeErrorCode: event.data });
    dismissCurrentVideo();
  }
}

function stopVideoPlayback() {
  playingViaVideo = false;
  youtubePlayerWrap.hidden = true;
  queuePlayer.hidden = false;
  updateEqAvailability();
  if (ytPlayer && ytPlayer.stopVideo) ytPlayer.stopVideo();
}

async function loadVideoInPlayer(videoId) {
  const player = await ensureYtPlayer();
  pauseQueuePlayer(); // stop the old track's audio now; the new video/audio choice reveals
  // itself once we know which one actually works (see onYtStateChange's PLAYING branch)
  playingViaVideo = true;
  player.loadVideoById(videoId);
}

// Returns true if this track is now playing as video (caller should skip its normal audio
// playback), false if video mode found nothing to play at all (caller should fall back).
async function tryPlayVideo(track) {
  if (!videoModeEnabled) return false;
  let data;
  try {
    const res = await fetch(`/track/${track.id}/video`);
    data = await res.json();
  } catch {
    return false;
  }
  if (!data.video_id) return false;
  await loadVideoInPlayer(data.video_id);
  return true;
}

// The current match's actual embed turned out broken - tries the next-best candidate from that
// same lookup (the backend keeps the runner-ups cached, no extra search cost - see the DELETE
// endpoint), only falling back to audio once there's truly nothing left to try. The fallback
// resumes at roughly the same position when there's a real one to resume from (the manual "Not
// this" button on an actually-playing video); starts from the beginning when the video never
// actually played (an onError auto-dismiss, where getCurrentTime() wouldn't mean anything).
async function dismissCurrentVideo() {
  if (currentIndex === -1 || !queue[currentIndex]) return;
  const track = queue[currentIndex];
  const resumeAt = ytPlayer && ytPlayer.getCurrentTime ? ytPlayer.getCurrentTime() : 0;

  let data;
  try {
    const res = await fetch(`/track/${track.id}/video`, { method: "DELETE" });
    data = await res.json();
  } catch {
    data = { video_id: null };
  }

  if (data.video_id) {
    await loadVideoInPlayer(data.video_id);
    return;
  }

  stopVideoPlayback();
  queuePlayer.src = `/stream/${track.id}`;
  if (resumeAt) {
    queuePlayer.addEventListener(
      "loadedmetadata",
      () => {
        queuePlayer.currentTime = resumeAt;
      },
      { once: true }
    );
  }
  queuePlayer.play();
  schedulePrefetch(queue[nextQueueIndex(currentIndex)]);
}

videoDismissBtn.addEventListener("click", () => {
  dismissCurrentVideo();
});

const QUEUE_STATE_KEY = "pan_queue_state";
let lastPersistedTime = 0;

function persistQueueState() {
  const state = {
    queue,
    currentIndex,
    shuffle,
    repeatMode,
    currentPlaylistId,
    currentPlaylistName,
    currentTime: queuePlayer.currentTime || 0,
    updatedAt: Date.now(),
  };
  try {
    localStorage.setItem(QUEUE_STATE_KEY, JSON.stringify(state));
  } catch {
    // localStorage unavailable (private browsing etc) - the queue just won't survive a refresh
  }
  // Also synced server-side (not just localStorage) so "what was I listening to" follows you
  // between devices, not just between reloads of the same browser - see /now-playing.
  fetch("/now-playing", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state),
  }).catch(() => {});
}

async function restoreQueueState() {
  let local = null;
  try {
    local = JSON.parse(localStorage.getItem(QUEUE_STATE_KEY));
  } catch {
    local = null;
  }

  let remote = null;
  try {
    const res = await fetch("/now-playing");
    const data = await res.json();
    remote = data.state;
  } catch {
    remote = null;
  }

  // Whichever side is actually newer wins - e.g. you played something on your phone after
  // last using this device, so the phone's state (now on the server) should take over here.
  const saved =
    remote && (!local || (remote.updatedAt || 0) > (local.updatedAt || 0)) ? remote : local;
  if (!saved || !Array.isArray(saved.queue) || !saved.queue.length) return;

  queue = saved.queue;
  currentIndex = typeof saved.currentIndex === "number" && saved.currentIndex < queue.length ? saved.currentIndex : -1;
  shuffle = !!saved.shuffle;
  repeatMode = ["all", "one"].includes(saved.repeatMode) ? saved.repeatMode : "none";
  currentPlaylistId = typeof saved.currentPlaylistId === "number" ? saved.currentPlaylistId : null;
  currentPlaylistName = currentPlaylistId ? saved.currentPlaylistName || null : null;
  if (shuffle) rebuildShuffleOrder();
  updateShuffleRepeatButtons();
  renderQueue();

  const track = currentIndex !== -1 ? queue[currentIndex] : null;
  if (track) {
    nowPlayingEl.innerHTML = "";
    nowPlayingEl.appendChild(buildTrackTitleLine(track, " (paused — press play to resume)"));
    renderNowPlayingActions(track);
    updateMediaSessionMetadata(track);
    if (track.file_path) {
      queuePlayer.src = `/stream/${track.id}`;
      if (saved.currentTime) {
        queuePlayer.addEventListener(
          "loadedmetadata",
          () => {
            queuePlayer.currentTime = saved.currentTime;
          },
          { once: true }
        );
      }
      schedulePrefetch(queue[nextQueueIndex(currentIndex)]);
    }
  }
}

function renderQueue() {
  queueListEl.innerHTML = "";
  queue.forEach((track, i) => {
    const li = document.createElement("li");
    li.className = i === currentIndex ? "queue-current" : "";

    const label = document.createElement("span");
    label.textContent = `${track.title || "Untitled"} — ${track.artist || "Unknown artist"}`;
    label.addEventListener("click", () => playAt(i));
    li.appendChild(label);

    const removeBtn = document.createElement("button");
    removeBtn.textContent = "×";
    removeBtn.title = "Remove from queue";
    removeBtn.addEventListener("click", () => removeFromQueue(i));
    li.appendChild(removeBtn);

    // Drag-and-drop reordering. Dropping on a row moves the dragged track to that row's index -
    // splice-then-splice naturally lands it before or after depending on drag direction, so no
    // half-row "insert above/below" detection is needed for this to feel right.
    li.draggable = true;
    li.title = "Drag to reorder";
    li.addEventListener("dragstart", (e) => {
      queueDragFromIndex = i;
      e.dataTransfer.effectAllowed = "move";
      li.classList.add("dragging");
    });
    li.addEventListener("dragend", () => {
      li.classList.remove("dragging");
      queueDragFromIndex = null;
    });
    li.addEventListener("dragover", (e) => {
      if (queueDragFromIndex === null) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
    });
    li.addEventListener("drop", (e) => {
      e.preventDefault();
      if (queueDragFromIndex === null || queueDragFromIndex === i) return;
      reorderQueue(queueDragFromIndex, i);
    });

    queueListEl.appendChild(li);
  });

  const hasQueue = queue.length > 0;
  shuffleBtn.disabled = !hasQueue;
  prevBtn.disabled = !hasQueue;
  playPauseBtn.disabled = !hasQueue;
  nextBtn.disabled = !hasQueue;
  repeatBtn.disabled = !hasQueue;
  videoModeBtn.disabled = !hasQueue;
  eqToggleBtn.disabled = !hasQueue;
  saveBtn.disabled = !hasQueue;
  cloneBtn.disabled = !hasQueue;
  addToPlaylistBtn.disabled = !hasQueue;
  clearBtn.disabled = !hasQueue;
  persistQueueState();
  refreshQueueButtons();
  updateSaveControls();
}

// Moves the track at `from` to position `to` - splice-out-then-splice-in, same approach as
// removeFromQueue/playAt use for mutating `queue` in place, with currentIndex re-derived
// afterwards so "what's actually playing" survives the array shuffling underneath it intact.
function reorderQueue(from, to) {
  const [moved] = queue.splice(from, 1);
  queue.splice(to, 0, moved);
  if (currentIndex === from) {
    currentIndex = to;
  } else if (from < currentIndex && to >= currentIndex) {
    currentIndex -= 1;
  } else if (from > currentIndex && to <= currentIndex) {
    currentIndex += 1;
  }
  renderQueue();
}

function removeFromQueue(i) {
  if (nextPrefetch && queue[i] && queue[i].id === nextPrefetch.id) cancelPrefetch();
  queue.splice(i, 1);
  if (i === currentIndex) {
    pauseQueuePlayer();
    currentIndex = -1;
    nowPlayingEl.textContent = "";
    renderNowPlayingActions(null);
  } else if (i < currentIndex) {
    currentIndex -= 1;
  }
  renderQueue();
}

// Gapless auto-advance: download the next queued track's audio as soon as the current one
// starts playing, so by the time it ends there's no network wait before the next starts.
let nextPrefetch = null; // {id, abort, promise: Promise<string blobUrl>}
let activeBlobUrl = null;

function cancelPrefetch() {
  if (nextPrefetch) nextPrefetch.abort.abort();
  nextPrefetch = null;
}

function schedulePrefetch(track) {
  if (!track || !track.file_path || (nextPrefetch && nextPrefetch.id === track.id)) return;
  cancelPrefetch();
  const abort = new AbortController();
  const promise = fetch(`/stream/${track.id}`, { signal: abort.signal })
    .then((r) => r.blob())
    .then((blob) => URL.createObjectURL(blob));
  nextPrefetch = { id: track.id, abort, promise };
}

function recordCurrentPlayProgress() {
  if (currentIndex === -1 || !queue[currentIndex]) return;
  const track = queue[currentIndex];
  // When the current track played as video, queuePlayer never actually moved - read progress
  // from the YouTube player instead, or this would wrongly log it as barely listened to.
  const usingYt = playingViaVideo && ytPlayer && ytPlayer.getDuration;
  const duration = usingYt ? ytPlayer.getDuration() : queuePlayer.duration;
  if (!duration || !isFinite(duration)) return;
  const currentTime = usingYt ? ytPlayer.getCurrentTime() : queuePlayer.currentTime;
  const percentListened = currentTime / duration;
  fetch(`/track/${track.id}/play-event`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ percent_listened: percentListened }),
  }).catch(() => {});
}

// Chromecast. The receiver fetches /stream/{id} directly over the network itself (not through
// this browser tab), so it only works when the Chromecast can reach this server - same LAN today,
// or wherever Tailscale/the eventual deployment makes reachable. Google's <google-cast-launcher>
// custom element (in index.html) renders its own icon/button and handles the device-picker UI;
// we just react to connect/disconnect and route playback through the cast session while one's
// active, via the SDK's RemotePlayer/RemotePlayerController rather than hand-rolling state sync.
let castPlayer = null;
let castController = null;

const CAST_CONTENT_TYPES = {
  flac: "audio/flac", wma: "audio/x-ms-wma", aac: "audio/aac", mp3: "audio/mpeg",
  m4a: "audio/mp4", ogg: "audio/ogg", wav: "audio/wav",
};

function guessCastContentType(track) {
  const ext = (track.file_path || "").split(".").pop().toLowerCase();
  return CAST_CONTENT_TYPES[ext] || "audio/mpeg";
}

// Chromecast's receiver hardware fetches this URL itself, directly over the network, and does
// real TLS certificate validation - unlike a browser, it has no way to click through a warning.
// Locally (`make run`), the app's HTTPS cert is self-signed, so casting needs the plain-HTTP
// sibling served in parallel on :8080 (see Makefile) instead - that's the one case this app ever
// has an untrusted cert. A real deployment (the Tailscale sidecar's `tailscale serve`, HTTPS on
// the default 443) has a properly trusted cert and no :8080 listener at all - nothing is
// reachable there except the page's own origin, which is also the only thing that actually needs
// to be: casting from a phone against the deployed app was failing because this was hardcoded to
// always assume the dev :8080 workaround, even in a deployment with no such port.
const CAST_STREAM_ORIGIN =
  location.protocol === "https:" && location.port === "8000"
    ? `http://${location.hostname}:8080`
    : location.origin;

function isCasting() {
  return !!(castPlayer && castPlayer.isConnected);
}

// The native <audio controls> bar's own play/scrubber UI isn't wired to the Chromecast session
// at all - while casting it just sits there looking broken (stuck, unresponsive to clicks) since
// actual playback is happening on the receiver, not locally. Swap it for a plain status line;
// the real transport buttons (prev/play/next) already branch on isCasting() and work correctly.
function updateCastUi() {
  const casting = isCasting();
  if (casting) stopVideoPlayback(); // video mode and casting can't both be showing at once
  queuePlayer.hidden = casting;
  updateEqAvailability();
  castStatusEl.hidden = !casting;
  castVolumeControlsEl.hidden = !casting;
  castProgressControlsEl.hidden = !casting;
  if (casting) {
    const session = cast.framework.CastContext.getInstance().getCurrentSession();
    const deviceName = session?.getCastDevice()?.friendlyName || "Chromecast";
    castStatusEl.textContent = `Casting to ${deviceName} - use the controls above to play/pause/skip.`;
    updateCastVolumeUi();
    startCastProgressPolling();
  } else {
    stopCastProgressPolling();
  }
}

// Reflects the receiver's actual volume/mute state - kept in sync both after our own slider/mute
// changes and after an external one (the device's physical remote, Google Home app, etc.) via
// the VOLUME_LEVEL_CHANGED/IS_MUTED_CHANGED listeners below.
function updateCastVolumeUi() {
  castVolumeSlider.value = castPlayer.volumeLevel;
  castMuteBtn.textContent = castPlayer.isMuted ? "\u{1F507}" : "\u{1F50A}";
  castMuteBtn.title = castPlayer.isMuted ? "Unmute" : "Mute";
  // The toggle button mirrors mute state at a glance, even collapsed - only clicking it reveals
  // the actual slider/mute button, keeping the status line from being dominated by a control
  // most casting sessions never need to touch.
  castVolumeToggleBtn.textContent = castPlayer.isMuted ? "\u{1F507}" : "\u{1F50A}";
}

castVolumeToggleBtn.addEventListener("click", () => {
  const collapsed = castVolumeSlidersEl.classList.toggle("collapsed");
  castVolumeToggleBtn.setAttribute("aria-expanded", String(!collapsed));
  castVolumeToggleBtn.title = collapsed ? "Show volume control" : "Hide volume control";
});

// Unlike the local <audio> element, the Cast SDK has no continuous "time updated" event - the
// framework only refreshes castPlayer.currentTime/duration periodically on its own, so a poll
// is the normal way sender apps drive a progress bar. Paused while the user is actively dragging
// the slider (castSeeking), so the poll can't fight with their in-progress drag.
let castProgressTimer = null;
let castSeeking = false;

function startCastProgressPolling() {
  if (castProgressTimer) return;
  castProgressTimer = setInterval(() => {
    if (castSeeking || !isCasting()) return;
    const duration = castPlayer.duration || 0;
    castProgressSlider.max = String(duration);
    castProgressSlider.value = String(castPlayer.currentTime || 0);
    castTimeCurrentEl.textContent = formatDuration((castPlayer.currentTime || 0) * 1000);
    castTimeDurationEl.textContent = formatDuration(duration * 1000);
  }, 1000);
}

function stopCastProgressPolling() {
  if (castProgressTimer) {
    clearInterval(castProgressTimer);
    castProgressTimer = null;
  }
}

castProgressSlider.addEventListener("input", () => {
  castSeeking = true;
  castTimeCurrentEl.textContent = formatDuration(Number(castProgressSlider.value) * 1000);
});
castProgressSlider.addEventListener("change", () => {
  castPlayer.currentTime = Number(castProgressSlider.value);
  castController.seek();
  castSeeking = false;
});

// Whether the current load actually got as far as playing, so PLAYER_STATE_CHANGED's IDLE
// handler (below) knows a later IDLE really means "this track ended" rather than "no media
// loaded yet" (true before the first ever load) or "already handled via the catch below".
// Deliberately NOT keyed off idleReason - some receiver firmware reports IDLE at the end of a
// track with an idleReason other than FINISHED (or that lags/doesn't update in time), which is
// what made the previous FINISHED/ERROR-only check still miss real "track ended" cases.
let castTrackActive = false;

function castCurrentTrack() {
  if (!isCasting() || currentIndex === -1) return;
  const session = cast.framework.CastContext.getInstance().getCurrentSession();
  if (!session) return;
  castTrackActive = false;
  const track = queue[currentIndex];
  const mediaInfo = new chrome.cast.media.MediaInfo(`${CAST_STREAM_ORIGIN}/stream/${track.id}`, guessCastContentType(track));
  mediaInfo.metadata = new chrome.cast.media.MusicTrackMediaMetadata();
  mediaInfo.metadata.title = track.title || "Untitled";
  mediaInfo.metadata.artist = track.artist || "";
  session.loadMedia(new chrome.cast.media.LoadRequest(mediaInfo))
    .then(() => {
      castTrackActive = true;
      pauseQueuePlayer();
    })
    .catch((err) => {
      // A rejected loadMedia (receiver can't decode this format/file, or a network hiccup
      // reaching :8080) used to just silently stall here forever - nothing else would ever
      // advance the queue, since the IDLE handler only ever fires for a track that actually
      // started. Skip to the next track instead.
      console.warn("Cast loadMedia failed, skipping track", track.id, err);
      advanceOnEnded();
    });
}

function initializeCastApi() {
  cast.framework.CastContext.getInstance().setOptions({
    receiverApplicationId: chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID,
    autoJoinPolicy: chrome.cast.AutoJoinPolicy.ORIGIN_SCOPED,
  });
  castPlayer = new cast.framework.RemotePlayer();
  castController = new cast.framework.RemotePlayerController(castPlayer);

  castController.addEventListener(cast.framework.RemotePlayerEventType.IS_CONNECTED_CHANGED, () => {
    // Starting a cast session hands playback to the receiver; local <audio> would otherwise
    // keep playing too (both audible at once). Disconnecting doesn't resume local playback
    // automatically - press play again to continue here.
    if (isCasting()) castCurrentTrack();
    updateCastUi();
  });
  castController.addEventListener(cast.framework.RemotePlayerEventType.IS_PAUSED_CHANGED, () => {
    playPauseBtn.innerHTML = castPlayer.isPaused ? "&#9654;" : "&#10074;&#10074;";
  });
  castController.addEventListener(cast.framework.RemotePlayerEventType.PLAYER_STATE_CHANGED, () => {
    if (!isCasting() || castPlayer.playerState !== chrome.cast.media.PlayerState.IDLE) return;
    // Only advance for an IDLE that follows a track that genuinely started (castTrackActive) -
    // not the IDLE that's just the default state before the first ever load. Once consumed,
    // reset the flag so a receiver that reports IDLE more than once for the same ended track
    // (seen on some firmware) can't double-advance.
    if (!castTrackActive) return;
    castTrackActive = false;
    advanceOnEnded();
  });
  castController.addEventListener(cast.framework.RemotePlayerEventType.VOLUME_LEVEL_CHANGED, updateCastVolumeUi);
  castController.addEventListener(cast.framework.RemotePlayerEventType.IS_MUTED_CHANGED, updateCastVolumeUi);

  castVolumeSlider.addEventListener("input", () => {
    castPlayer.volumeLevel = Number(castVolumeSlider.value);
    castController.setVolumeLevel();
  });
  castMuteBtn.addEventListener("click", () => castController.muteOrUnmute());
}

window["__onGCastApiAvailable"] = (isAvailable) => {
  if (!isAvailable) return;
  // chrome.cast fires this callback as soon as the base SDK is ready, but the cast.framework
  // bundle (loaded via the ?loadCastFramework=1 query param) can finish a tick later - poll
  // briefly rather than assuming "cast" is already defined, which throws a ReferenceError.
  const tryInit = () => {
    if (typeof cast === "undefined" || !cast.framework) {
      setTimeout(tryInit, 100);
      return;
    }
    initializeCastApi();
  };
  tryInit();
};

async function playAt(i) {
  if (i < 0 || i >= queue.length) return;
  recordCurrentPlayProgress();
  currentIndex = i;
  const track = queue[i];

  if (isCasting()) {
    castCurrentTrack();
    nowPlayingEl.innerHTML = "";
    nowPlayingEl.appendChild(buildTrackTitleLine(track));
    renderNowPlayingActions(track);
    renderQueue();
    updateMediaSessionMetadata(track);
    return;
  }

  if (await tryPlayVideo(track)) {
    nowPlayingEl.innerHTML = "";
    nowPlayingEl.appendChild(buildTrackTitleLine(track));
    renderNowPlayingActions(track);
    renderQueue();
    updateMediaSessionMetadata(track);
    schedulePrefetch(queue[nextQueueIndex(i)]);
    return;
  }
  stopVideoPlayback();

  let src = `/stream/${track.id}`;
  if (nextPrefetch && nextPrefetch.id === track.id) {
    const prefetched = nextPrefetch;
    nextPrefetch = null;
    try {
      src = await prefetched.promise;
    } catch {
      // Prefetch failed/aborted - fall back to the normal streamed URL.
    }
  } else {
    cancelPrefetch();
  }

  if (activeBlobUrl) {
    URL.revokeObjectURL(activeBlobUrl);
    activeBlobUrl = null;
  }
  if (src.startsWith("blob:")) activeBlobUrl = src;

  queuePlayer.src = src;
  queuePlayer.play();
  pauseOtherPlayers(queuePlayer);
  nowPlayingEl.innerHTML = "";
  nowPlayingEl.appendChild(buildTrackTitleLine(track));
  renderNowPlayingActions(track);
  renderQueue();
  updateMediaSessionMetadata(track);

  schedulePrefetch(queue[nextQueueIndex(i)]);
}

// Shared by the local <audio> "ended" event and the Chromecast receiver's IDLE/FINISHED state,
// so repeat-one/repeat-all/shuffle behave the same regardless of where playback is happening.
function advanceOnEnded() {
  logClient("ended", { willAdvance: repeatMode === "one" || nextQueueIndex(currentIndex) !== -1 });
  if (repeatMode === "one") {
    playAt(currentIndex);
    return;
  }
  const next = nextQueueIndex(currentIndex);
  if (next !== -1) playAt(next);
}

// Diagnostic sink for mobile playback issues (e.g. "stops after ~5 minutes locked") that can't
// be reproduced with devtools attached - lands in the pan container's own log output via
// POST /client-log. Fire-and-forget: never throws, never blocks playback if the request fails.
function logClient(event, detail = {}) {
  try {
    navigator.sendBeacon?.(
      "/client-log",
      new Blob(
        [
          JSON.stringify({
            event,
            detail: {
              ...detail,
              hidden: document.hidden,
              trackId: queue[currentIndex]?.id ?? null,
              currentTime: queuePlayer.currentTime,
              readyState: queuePlayer.readyState,
            },
          }),
        ],
        { type: "application/json" }
      )
    );
  } catch {
    // Best-effort only.
  }
}

document.addEventListener("visibilitychange", () => {
  logClient("visibilitychange");
  // The flip side of the "pause" listener's bookkeeping above: once an involuntary (OS-driven)
  // pause has happened, wait for the page to actually come back to the foreground before trying
  // to resume - a bare .play() call the instant it's paused tends to be rejected anyway, and this
  // mirrors the one thing that reliably un-sticks it today (unlocking the phone).
  if (!document.hidden && awaitingBackgroundResume && queuePlayer.paused) {
    awaitingBackgroundResume = false;
    queuePlayer.play().catch(() => logClient("auto_resume_blocked"));
  }
  if (!document.hidden) resumeEqAudioCtx();
});
for (const evt of ["stalled", "suspend", "waiting", "error"]) {
  queuePlayer.addEventListener(evt, () => logClient(evt));
}

// Lock-screen/notification playback controls on phones - without this, backgrounding the
// browser tab during playback gives you no way to pause/skip without switching back to it.
// Action handlers are wired once; metadata/playbackState update per track and per play/pause.
function updateMediaSessionMetadata(track) {
  if (!("mediaSession" in navigator)) return;
  navigator.mediaSession.metadata = new MediaMetadata({
    title: track.title || "Untitled",
    artist: track.artist || "Unknown artist",
    album: track.album || "",
  });
}

if ("mediaSession" in navigator) {
  navigator.mediaSession.setActionHandler("play", () => {
    if (isCasting()) castController.playOrPause();
    else if (playingViaVideo && ytPlayer) ytPlayer.playVideo();
    else queuePlayer.play();
  });
  navigator.mediaSession.setActionHandler("pause", () => {
    if (isCasting()) castController.playOrPause();
    else if (playingViaVideo && ytPlayer) ytPlayer.pauseVideo();
    else pauseQueuePlayer();
  });
  navigator.mediaSession.setActionHandler("previoustrack", () => playAt(prevQueueIndex(currentIndex)));
  navigator.mediaSession.setActionHandler("nexttrack", () => playAt(nextQueueIndex(currentIndex)));
}

queuePlayer.addEventListener("play", () => {
  logClient("play");
  playPauseBtn.innerHTML = "&#10074;&#10074;";
  pauseOtherPlayers(queuePlayer);
  if ("mediaSession" in navigator) navigator.mediaSession.playbackState = "playing";
  awaitingBackgroundResume = false;
  resumeEqAudioCtx();
});
queuePlayer.addEventListener("pause", () => {
  logClient("pause");
  playPauseBtn.innerHTML = "&#9654;";
  if ("mediaSession" in navigator) navigator.mediaSession.playbackState = "paused";
  persistQueueState();
  if (!deliberatePause && currentIndex !== -1) awaitingBackgroundResume = true;
  deliberatePause = false;
});
queuePlayer.addEventListener("ended", advanceOnEnded);

// Keep the resumable playback position fresh without writing to localStorage on every
// timeupdate tick (which fires several times a second) - once every ~5s is plenty for
// "resume roughly where I left off" after a refresh or browser restart.
queuePlayer.addEventListener("timeupdate", () => {
  if (queuePlayer.currentTime - lastPersistedTime >= 5) {
    lastPersistedTime = queuePlayer.currentTime;
    persistQueueState();
  }
});
window.addEventListener("beforeunload", persistQueueState);

playPauseBtn.addEventListener("click", () => {
  if (currentIndex === -1 && queue.length) {
    playAt(0);
  } else if (isCasting()) {
    castController.playOrPause();
  } else if (playingViaVideo && ytPlayer) {
    if (ytPlayer.getPlayerState() === YT.PlayerState.PLAYING) ytPlayer.pauseVideo();
    else ytPlayer.playVideo();
  } else if (queuePlayer.paused) {
    queuePlayer.play();
  } else {
    pauseQueuePlayer();
  }
});
prevBtn.addEventListener("click", () => playAt(prevQueueIndex(currentIndex)));
nextBtn.addEventListener("click", () => playAt(nextQueueIndex(currentIndex)));
// Saving when the queue came from a playlist (currentPlaylistId set) updates that playlist
// directly, no confirmation - that's the common case and the whole point of tracking it.
// Starting a genuinely new playlist from scratch still prompts for a name either way.
async function saveAsNewPlaylist() {
  const name = prompt("Playlist name?");
  if (!name || !name.trim()) return;
  await fetch("/playlists", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name.trim(), track_ids: queue.map((t) => t.id) }),
  });
  loadPlaylists();
}

saveBtn.addEventListener("click", async () => {
  if (currentPlaylistId) {
    await fetch(`/playlists/${currentPlaylistId}/tracks`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_ids: queue.map((t) => t.id) }),
    });
    loadPlaylists();
    return;
  }
  await saveAsNewPlaylist();
});

// Clone: fork the currently-loaded playlist's queue into a new one instead of updating it -
// only shown when a playlist is actually loaded (see updateSaveControls).
cloneBtn.addEventListener("click", saveAsNewPlaylist);

// Unlike Save (which creates a new playlist or overwrites the one the queue was loaded from),
// this adds the whole queue onto an existing playlist of your choice, same as per-track +Playlist.
const addToPlaylistPanel = buildPlaylistPickerPanel(() => queue.map((t) => t.id));
document.getElementById("queue-header").appendChild(addToPlaylistPanel);
addToPlaylistBtn.addEventListener("click", () => {
  addToPlaylistPanel.hidden = !addToPlaylistPanel.hidden;
  if (!addToPlaylistPanel.hidden) addToPlaylistPanel.load();
});

clearBtn.addEventListener("click", () => {
  recordCurrentPlayProgress();
  pauseQueuePlayer();
  queuePlayer.removeAttribute("src");
  cancelPrefetch();
  if (activeBlobUrl) {
    URL.revokeObjectURL(activeBlobUrl);
    activeBlobUrl = null;
  }
  queue = [];
  currentIndex = -1;
  currentPlaylistId = null;
  currentPlaylistName = null;
  nowPlayingEl.textContent = "";
  renderNowPlayingActions(null);
  renderQueue();
  if ("mediaSession" in navigator) {
    navigator.mediaSession.metadata = null;
    navigator.mediaSession.playbackState = "none";
  }
});

// Session-only, like videoModeEnabled - reopens collapsed each load rather than persisting a
// "show clutter" preference across visits.
let showHiddenPlaylists = false;
// Below this many playlists, a filter box is just one more thing to look at before finding the
// one you want in a two-second glance - it only earns its keep once scrolling/scanning the full
// list actually costs more than typing a few letters.
const PLAYLIST_FILTER_THRESHOLD = 8;
let playlistFilterQuery = "";
let lastPlaylists = []; // re-filtering on every keystroke shouldn't mean refetching every time

async function loadPlaylists() {
  // Always fetch the full set (not just when showHiddenPlaylists is on) so the "N hidden" count
  // below is available without a second round-trip when the toggle is clicked.
  const res = await fetch("/playlists?include_hidden=true");
  const data = await res.json();
  lastPlaylists = data.playlists;
  renderPlaylists(data.playlists);
}

playlistsFilterEl.addEventListener("input", () => {
  playlistFilterQuery = playlistsFilterEl.value.trim().toLowerCase();
  renderPlaylists(lastPlaylists);
});

function renderPlaylists(playlists) {
  playlistsListEl.innerHTML = "";
  playlistsFilterEl.hidden = playlists.length < PLAYLIST_FILTER_THRESHOLD;

  const hidden = playlists.filter((p) => p.hidden_at);
  let visible = showHiddenPlaylists ? playlists : playlists.filter((p) => !p.hidden_at);
  if (playlistFilterQuery) {
    visible = visible.filter((p) => p.name.toLowerCase().includes(playlistFilterQuery));
  }

  if (!visible.length) {
    playlistsListEl.innerHTML = playlistFilterQuery
      ? `<li>No playlists match "${escapeHtml(playlistsFilterEl.value.trim())}".</li>`
      : "<li>No saved playlists yet.</li>";
  }
  for (const p of visible) {
    const li = document.createElement("li");
    if (p.hidden_at) li.classList.add("is-hidden-playlist");

    const label = document.createElement("span");
    label.textContent = `${p.name} (${p.track_count} track${p.track_count === 1 ? "" : "s"})`;
    label.addEventListener("click", () => playPlaylist(p.id, p.name));
    li.appendChild(label);

    const actions = document.createElement("span");
    actions.className = "playlist-actions";

    const suggestBtn = document.createElement("button");
    suggestBtn.className = "suggest-btn";
    suggestBtn.textContent = "+ Suggest";
    suggestBtn.title = `Find more tracks like "${p.name}"`;
    suggestBtn.addEventListener("click", () => showPlaylistSuggestions(p.id, p.name));
    actions.appendChild(suggestBtn);

    const hideBtn = document.createElement("button");
    hideBtn.textContent = p.hidden_at ? "\u{1F441}‍\u{1F5E8}" : "\u{1F441}";
    hideBtn.title = p.hidden_at ? "Unhide playlist" : "Hide playlist from this list";
    hideBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await fetch(`/playlists/${p.id}/hidden`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hidden: !p.hidden_at }),
      });
      loadPlaylists();
    });
    actions.appendChild(hideBtn);

    const renameBtn = document.createElement("button");
    renameBtn.className = "rename-btn";
    renameBtn.textContent = "✎";
    renameBtn.title = "Rename playlist";
    renameBtn.addEventListener("click", async () => {
      const name = prompt("New playlist name?", p.name);
      if (!name || !name.trim() || name.trim() === p.name) return;
      const res = await fetch(`/playlists/${p.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim() }),
      });
      if (!res.ok) return;
      if (currentPlaylistId === p.id) {
        currentPlaylistName = name.trim();
        updateSaveControls();
      }
      loadPlaylists();
    });
    actions.appendChild(renameBtn);

    const deleteBtn = document.createElement("button");
    deleteBtn.textContent = "×";
    deleteBtn.title = "Delete playlist";
    deleteBtn.addEventListener("click", async () => {
      if (!confirm(`Delete playlist "${p.name}"?`)) return;
      await fetch(`/playlists/${p.id}`, { method: "DELETE" });
      loadPlaylists();
    });
    actions.appendChild(deleteBtn);

    li.appendChild(actions);

    playlistsListEl.appendChild(li);
  }

  if (hidden.length) {
    const toggleLi = document.createElement("li");
    const toggleBtn = document.createElement("button");
    toggleBtn.className = "link-btn";
    toggleBtn.textContent = showHiddenPlaylists
      ? "Hide hidden playlists"
      : `Show ${hidden.length} hidden playlist${hidden.length === 1 ? "" : "s"}`;
    toggleBtn.addEventListener("click", () => {
      showHiddenPlaylists = !showHiddenPlaylists;
      renderPlaylists(playlists);
    });
    toggleLi.appendChild(toggleBtn);
    playlistsListEl.appendChild(toggleLi);
  }
}

async function playPlaylist(id, name) {
  const res = await fetch(`/playlists/${id}`);
  const data = await res.json();
  playTrackList(data.results, `Playlist: "${name}"`, { id, name });
}

// "More like this playlist" - same results-list/context-banner pattern as showStats/
// showDiscography, so the suggestions get the full set of row actions (favorite, +Queue,
// +Playlist, etc.) rather than only living in the compact queue list.
async function showPlaylistSuggestions(id, name) {
  const res = await fetch(`/playlists/${id}/suggest`);
  const data = await res.json();
  previousResults = lastResults;
  previousSearchHasMore = searchHasMore;
  previousLoadMoreHandler = loadMoreHandler;
  loadMoreHandler = null;
  loadMoreBtn.hidden = true;
  lastResults = data.results;
  renderResults(lastResults);
  setContextBanner(`Suggested for "${name}"`, restorePreviousResults);
}

loadPlaylists();
updateShuffleRepeatButtons();
restoreQueueState();

async function showStats(endpointPath, label) {
  previousResults = lastResults;
  previousSearchHasMore = searchHasMore;
  previousLoadMoreHandler = loadMoreHandler;
  loadMoreBtn.hidden = true;

  let statsOffset = 0;
  const loadMoreStats = async () => {
    loadMoreBtn.disabled = true;
    loadMoreBtn.textContent = "Loading...";
    const res = await fetch(`/stats/${endpointPath}?limit=${STATS_PAGE_SIZE}&offset=${statsOffset}`);
    const data = await res.json();
    lastResults = lastResults.concat(data.results);
    statsOffset += data.results.length;
    searchHasMore = data.results.length === STATS_PAGE_SIZE;
    renderResults(lastResults);
    loadMoreBtn.disabled = false;
    loadMoreBtn.textContent = "Load more";
    loadMoreHandler = searchHasMore ? loadMoreStats : null;
    loadMoreBtn.hidden = !searchHasMore;
  };

  const res = await fetch(`/stats/${endpointPath}?limit=${STATS_PAGE_SIZE}&offset=0`);
  const data = await res.json();
  lastResults = data.results;
  statsOffset = data.results.length;
  searchHasMore = data.results.length === STATS_PAGE_SIZE;
  renderResults(lastResults);
  loadMoreHandler = searchHasMore ? loadMoreStats : null;
  loadMoreBtn.hidden = !searchHasMore;
  setContextBanner(label, restorePreviousResults);
}

document.getElementById("stats-recently-played").addEventListener("click", () => showStats("recently-played", "Recently played"));
document.getElementById("stats-recently-added").addEventListener("click", () => showStats("recently-added", "Recently added"));
document.getElementById("stats-favorites").addEventListener("click", () => showStats("favorites", "Favorites"));
document.getElementById("stats-dislikes").addEventListener("click", () => showStats("dislikes", "Disliked"));
document.getElementById("stats-most-played").addEventListener("click", () => showStats("most-played", "Most played"));
document.getElementById("stats-most-skipped").addEventListener("click", () => showStats("most-skipped", "Most skipped"));
document.getElementById("stats-never-played").addEventListener("click", () => showStats("never-played", "Never played"));

// Tracks with no title/artist are otherwise unfindable - nothing in their metadata for semantic
// search to match - so this is the only way to browse to one and fix it via Edit tags.
document.getElementById("missing-metadata-btn").addEventListener("click", async () => {
  const res = await fetch("/admin/missing-metadata");
  const data = await res.json();
  previousResults = lastResults;
  previousSearchHasMore = searchHasMore;
  previousLoadMoreHandler = loadMoreHandler;
  loadMoreHandler = null;
  loadMoreBtn.hidden = true;
  lastResults = data.results;
  renderResults(lastResults);
  setContextBanner(`Needs attention (${data.results.length})`, restorePreviousResults);
});

const clientLogsPanel = document.getElementById("client-logs-panel");
const clientLogsOutput = document.getElementById("client-logs-output");
document.getElementById("client-logs-btn").addEventListener("click", async () => {
  clientLogsPanel.hidden = !clientLogsPanel.hidden;
  if (clientLogsPanel.hidden) return;
  clientLogsOutput.textContent = "Loading...";
  const res = await fetch("/admin/client-logs");
  const data = await res.json();
  clientLogsOutput.textContent = data.logs.length
    ? data.logs.map((l) => `${l.created_at}  ${l.event}  ${JSON.stringify(l.detail)}`).join("\n")
    : "No client logs yet.";
});

const recommendationsPanel = document.getElementById("recommendations-panel");
document.getElementById("recommendations-btn").addEventListener("click", async () => {
  recommendationsPanel.hidden = !recommendationsPanel.hidden;
  if (recommendationsPanel.hidden) return;
  recommendationsPanel.textContent = "Loading...";
  const res = await fetch("/admin/recommendations");
  const data = await res.json();
  renderRecommendations(data);
});

// Refreshed as part of reindex (backend/recommend.py), not on demand from here - this just
// displays whatever the most recent reindex produced.
function renderRecommendations(data) {
  recommendationsPanel.innerHTML = "";
  if (!data.recommendations.length) {
    recommendationsPanel.textContent = "No recommendations yet - needs some listening history and a reindex (with ANTHROPIC_API_KEY configured) first.";
    return;
  }

  const generated = document.createElement("div");
  generated.className = "recommendations-generated";
  generated.textContent = `From the ${data.generated_at} reindex`;
  recommendationsPanel.appendChild(generated);

  const gapFill = data.recommendations.filter((r) => r.category === "gap_fill");
  const discovery = data.recommendations.filter((r) => r.category === "discovery");

  if (gapFill.length) {
    const heading = document.createElement("strong");
    heading.textContent = "Fill out artists you already love";
    recommendationsPanel.appendChild(heading);
    const list = document.createElement("ul");
    for (const r of gapFill) {
      const albums = (r.detail?.missing_albums || []).join(", ");
      list.appendChild(
        buildRecommendationItem(r, `<strong>${escapeHtml(r.artist)}</strong> — ${escapeHtml(albums)}`)
      );
    }
    recommendationsPanel.appendChild(list);
  }

  if (discovery.length) {
    const heading = document.createElement("strong");
    heading.textContent = "New artists to try";
    recommendationsPanel.appendChild(heading);
    const list = document.createElement("ul");
    for (const r of discovery) {
      list.appendChild(buildRecommendationItem(r, `<strong>${escapeHtml(r.artist)}</strong>`));
    }
    recommendationsPanel.appendChild(list);
  }
}

function buildRecommendationItem(r, headlineHtml) {
  const li = document.createElement("li");
  li.className = `recommendation-item recommendation-${r.status}`;
  li.innerHTML = `
    <div class="recommendation-main">
      ${headlineHtml}<br><span class="recommendation-reasoning">${escapeHtml(r.reasoning || "")}</span>
    </div>
    <div class="recommendation-actions">
      <button class="recommendation-accept" title="Added to library / agree">✓</button>
      <button class="recommendation-dismiss" title="Not interested">✕</button>
    </div>`;
  li.querySelector(".recommendation-accept").addEventListener("click", () =>
    setRecommendationStatus(r.id, "accepted", li)
  );
  li.querySelector(".recommendation-dismiss").addEventListener("click", () =>
    setRecommendationStatus(r.id, "dismissed", li)
  );
  return li;
}

async function setRecommendationStatus(id, status, li) {
  await fetch(`/admin/recommendations/${id}/status`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  li.className = `recommendation-item recommendation-${status}`;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

reindexBtn.addEventListener("click", async () => {
  reindexBtn.disabled = true;
  reindexStatusEl.textContent = "Starting...";
  await fetch("/admin/reindex", { method: "POST" });
  poll();
});

async function poll() {
  const res = await fetch("/admin/reindex/status");
  const status = await res.json();
  if (status.running) {
    reindexStatusEl.textContent = "Reindexing...";
    setTimeout(poll, 2000);
  } else {
    reindexBtn.disabled = false;
    reindexStatusEl.textContent = status.error ? `Error: ${status.error}` : "Done";
  }
}

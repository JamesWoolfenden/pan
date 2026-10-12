const form = document.getElementById("search-form");
const input = document.getElementById("query");
const historyEl = document.getElementById("search-history");
const resultsEl = document.getElementById("results");
const resultsHeaderEl = document.getElementById("results-header");
const contextBannerEl = document.getElementById("context-banner");
const playAllBtn = document.getElementById("play-all-btn");
const reindexBtn = document.getElementById("reindex-btn");
const reindexStatusEl = document.getElementById("reindex-status");

const queuePlayer = document.getElementById("queue-player");
const queueListEl = document.getElementById("queue-list");
const nowPlayingEl = document.getElementById("queue-now-playing");
const prevBtn = document.getElementById("queue-prev");
const playPauseBtn = document.getElementById("queue-playpause");
const nextBtn = document.getElementById("queue-next");
const clearBtn = document.getElementById("queue-clear");

let queue = []; // [{id, title, artist, file_path}]
let currentIndex = -1;
let lastResults = [];
let previousResults = null;

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
  previousResults = null;
  contextBannerEl.hidden = true;
  resultsEl.innerHTML = "<li>Searching...</li>";
  resultsHeaderEl.hidden = true;
  const res = await fetch(`/search?q=${encodeURIComponent(q)}`);
  const data = await res.json();
  lastResults = data.results;
  renderResults(lastResults);
});

function showSimilar(track) {
  return showRelated(track, "similar", `Similar to "${track.title || "Untitled"}"`);
}

function showVersions(track) {
  return showRelated(track, "versions", `Other versions of "${track.title || "Untitled"}"`);
}

async function showRelated(track, endpointPath, bannerText) {
  const res = await fetch(`/track/${track.id}/${endpointPath}`);
  const data = await res.json();
  previousResults = lastResults;
  lastResults = data.results;
  renderResults(lastResults);

  contextBannerEl.hidden = false;
  contextBannerEl.innerHTML = "";
  const label = document.createElement("span");
  label.textContent = `${bannerText} — `;
  const backLink = document.createElement("button");
  backLink.className = "context-back";
  backLink.textContent = "back to search results";
  backLink.addEventListener("click", () => {
    lastResults = previousResults;
    previousResults = null;
    contextBannerEl.hidden = true;
    renderResults(lastResults);
  });
  contextBannerEl.append(label, backLink);
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

    const info = document.createElement("div");
    info.innerHTML = `
      <div><strong>${escapeHtml(r.title || "Untitled")}</strong> — ${escapeHtml(r.artist || "Unknown artist")}</div>
      <div class="track-meta">${escapeHtml(r.album || "")} ${r.year ? `(${r.year})` : ""} · ${r.source}</div>
    `;
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
      queueBtn.textContent = "+ Queue";
      queueBtn.title = "Add to queue";
      queueBtn.addEventListener("click", () => addToQueue(r));
      playArea.appendChild(queueBtn);
    } else {
      playArea.textContent = "(not playable here)";
    }

    const similarBtn = document.createElement("button");
    similarBtn.textContent = "Similar";
    similarBtn.title = "Find tracks like this one";
    similarBtn.addEventListener("click", () => showSimilar(r));
    playArea.appendChild(similarBtn);

    const versionsBtn = document.createElement("button");
    versionsBtn.textContent = "Versions";
    versionsBtn.title = "Other mixes/edits/remixes of this song";
    versionsBtn.addEventListener("click", () => showVersions(r));
    playArea.appendChild(versionsBtn);

    if (r.album) {
      const albumBtn = document.createElement("button");
      albumBtn.textContent = "Play album";
      albumBtn.title = "Queue and play the whole album";
      albumBtn.addEventListener("click", () => playAlbum(r));
      playArea.appendChild(albumBtn);
    }

    const infoBtn = document.createElement("button");
    infoBtn.textContent = "Info";
    infoBtn.title = "Track/album/artist details";
    const infoPanel = buildInfoPanel(r);
    infoBtn.addEventListener("click", () => {
      infoPanel.hidden = !infoPanel.hidden;
      infoBtn.textContent = infoPanel.hidden ? "Info" : "Hide info";
      if (!infoPanel.hidden) infoPanel.load();
    });
    playArea.appendChild(infoBtn);

    const editBtn = document.createElement("button");
    editBtn.textContent = "Edit tags";
    editBtn.className = "edit-tags-btn";
    const editPanel = buildEditPanel(r, tagsEl, editBtn);
    editBtn.addEventListener("click", () => {
      editPanel.hidden = !editPanel.hidden;
      editBtn.textContent = editPanel.hidden ? "Edit tags" : "Cancel";
    });
    playArea.appendChild(editBtn);

    li.appendChild(info);
    li.appendChild(playArea);
    li.appendChild(infoPanel);
    li.appendChild(editPanel);
    resultsEl.appendChild(li);
  }
}

async function playAlbum(track) {
  const res = await fetch(`/track/${track.id}/album`);
  const data = await res.json();
  queue = data.results.map(toQueueEntry);
  renderQueue();
  playAt(0);
}

function buildInfoPanel(r) {
  const panel = document.createElement("div");
  panel.className = "info-panel";
  panel.hidden = true;

  const factsEl = document.createElement("div");
  factsEl.className = "info-facts";
  factsEl.textContent = "Loading...";

  const aboutBtn = document.createElement("button");
  aboutBtn.textContent = "Tell me more (AI)";
  const aboutEl = document.createElement("div");
  aboutEl.className = "info-about";

  aboutBtn.addEventListener("click", async () => {
    aboutBtn.disabled = true;
    aboutBtn.textContent = "Thinking...";
    const res = await fetch(`/track/${r.id}/about`);
    const data = await res.json();
    aboutEl.textContent = data.text || "No background available (is ANTHROPIC_API_KEY configured?).";
    aboutBtn.hidden = true;
  });

  let loaded = false;
  panel.load = async () => {
    if (loaded) return;
    loaded = true;
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
    ];
    for (const [label, value] of rows) {
      if (!value) continue;
      const row = document.createElement("div");
      row.innerHTML = `<span class="info-label">${label}:</span> ${escapeHtml(String(value))}`;
      factsEl.appendChild(row);
    }
    if (!factsEl.children.length) factsEl.textContent = "No technical details available.";
  };

  panel.append(factsEl, aboutBtn, aboutEl);
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

function setTagsText(tagsEl, r) {
  tagsEl.textContent = `${r.genre || ""}${r.mood ? ` · ${r.mood}` : ""}`;
}

function buildEditPanel(r, tagsEl, editBtn) {
  const panel = document.createElement("div");
  panel.className = "edit-panel";
  panel.hidden = true;

  const genreInput = document.createElement("input");
  genreInput.placeholder = "Genre";
  genreInput.value = r.genre || "";

  const moodInput = document.createElement("input");
  moodInput.placeholder = "Mood";
  moodInput.value = r.mood || "";

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
      body: JSON.stringify({ genre: genreInput.value.trim() || null, mood: moodInput.value.trim() || null }),
    });
    saveBtn.disabled = false;
    if (!res.ok) return;
    const updated = await res.json();
    r.genre = updated.genre;
    r.mood = updated.mood;
    setTagsText(tagsEl, r);
    panel.hidden = true;
    editBtn.textContent = "Edit tags";
  });

  const row1 = document.createElement("div");
  row1.className = "edit-row";
  row1.append(genreInput, moodInput, suggestBtn, saveBtn);

  panel.append(row1, suggestionsEl);
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
  if (current !== queuePlayer) queuePlayer.pause();
}

playAllBtn.addEventListener("click", () => {
  const playable = lastResults.filter((r) => r.file_path);
  queue = playable.map(toQueueEntry);
  renderQueue();
  playAt(0);
});

function addToQueue(track) {
  queue.push(toQueueEntry(track));
  renderQueue();
  if (currentIndex === -1) playAt(0);
}

function toQueueEntry(r) {
  return { id: r.id, title: r.title, artist: r.artist, file_path: r.file_path };
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

    queueListEl.appendChild(li);
  });

  const hasQueue = queue.length > 0;
  prevBtn.disabled = !hasQueue;
  playPauseBtn.disabled = !hasQueue;
  nextBtn.disabled = !hasQueue;
  clearBtn.disabled = !hasQueue;
}

function removeFromQueue(i) {
  if (nextPrefetch && queue[i] && queue[i].id === nextPrefetch.id) cancelPrefetch();
  queue.splice(i, 1);
  if (i === currentIndex) {
    queuePlayer.pause();
    currentIndex = -1;
    nowPlayingEl.textContent = "";
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

async function playAt(i) {
  if (i < 0 || i >= queue.length) return;
  currentIndex = i;
  const track = queue[i];

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
  nowPlayingEl.textContent = `${track.title || "Untitled"} — ${track.artist || "Unknown artist"}`;
  renderQueue();

  schedulePrefetch(queue[i + 1]);
}

queuePlayer.addEventListener("play", () => {
  playPauseBtn.innerHTML = "&#10074;&#10074;";
  pauseOtherPlayers(queuePlayer);
});
queuePlayer.addEventListener("pause", () => {
  playPauseBtn.innerHTML = "&#9654;";
});
queuePlayer.addEventListener("ended", () => {
  if (currentIndex + 1 < queue.length) playAt(currentIndex + 1);
});

playPauseBtn.addEventListener("click", () => {
  if (currentIndex === -1 && queue.length) {
    playAt(0);
  } else if (queuePlayer.paused) {
    queuePlayer.play();
  } else {
    queuePlayer.pause();
  }
});
prevBtn.addEventListener("click", () => playAt(currentIndex - 1));
nextBtn.addEventListener("click", () => playAt(currentIndex + 1));
clearBtn.addEventListener("click", () => {
  queuePlayer.pause();
  queuePlayer.removeAttribute("src");
  cancelPrefetch();
  if (activeBlobUrl) {
    URL.revokeObjectURL(activeBlobUrl);
    activeBlobUrl = null;
  }
  queue = [];
  currentIndex = -1;
  nowPlayingEl.textContent = "";
  renderQueue();
});

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

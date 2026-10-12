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
const saveBtn = document.getElementById("queue-save");
const clearBtn = document.getElementById("queue-clear");
const playlistsListEl = document.getElementById("playlists-list");
const loadMoreBtn = document.getElementById("load-more-btn");
const nowPlayingActionsEl = document.getElementById("queue-now-playing-actions");

let queue = []; // [{id, title, artist, file_path}]
let currentIndex = -1;
let lastResults = [];
let previousResults = null;

const SEARCH_PAGE_SIZE = 25;
let lastQuery = "";
let searchOffset = 0;
let searchHasMore = false;
let previousSearchHasMore = false;

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
  loadMoreBtn.hidden = !searchHasMore;
});

loadMoreBtn.addEventListener("click", async () => {
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
  loadMoreBtn.hidden = !searchHasMore;
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
  previousSearchHasMore = searchHasMore;
  loadMoreBtn.hidden = true;
  lastResults = data.results;
  renderResults(lastResults);
  setContextBanner(bannerText, restorePreviousResults);
}

async function showDiscography(artist) {
  const res = await fetch(`/artist/discography?artist=${encodeURIComponent(artist)}`);
  const data = await res.json();
  previousResults = lastResults;
  previousSearchHasMore = searchHasMore;
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

    li.appendChild(info);
    li.appendChild(playBtn);
    resultsEl.appendChild(li);
  }
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

    const titleEl = document.createElement("div");
    const titleStrong = document.createElement("strong");
    titleStrong.textContent = r.title || "Untitled";
    titleEl.appendChild(titleStrong);
    titleEl.append(" — ");
    if (r.artist) {
      const artistLink = document.createElement("button");
      artistLink.className = "artist-link";
      artistLink.textContent = r.artist;
      artistLink.title = "Browse this artist's discography";
      artistLink.addEventListener("click", () => showDiscography(r.artist));
      titleEl.appendChild(artistLink);
    } else {
      titleEl.append("Unknown artist");
    }
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
      queueBtn.textContent = "+ Queue";
      queueBtn.title = "Add to queue";
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

function buildPlaylistPickerPanel(r) {
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
      body: JSON.stringify({ name: name.trim(), track_ids: [r.id] }),
    });
    loadPlaylists();
    panel.hidden = true;
  });

  let loaded = false;
  panel.load = async () => {
    if (loaded) return;
    loaded = true;
    const res = await fetch("/playlists");
    const data = await res.json();
    if (!data.playlists.length) {
      listEl.textContent = "No playlists yet.";
    } else {
      for (const p of data.playlists) {
        const btn = document.createElement("button");
        btn.textContent = p.name;
        btn.addEventListener("click", async () => {
          await fetch(`/playlists/${p.id}/tracks`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ track_id: r.id }),
          });
          loadPlaylists();
          panel.hidden = true;
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

// Used by both "play this album" paths. Keeps the tracks visible in the main results list (with
// their full set of controls - Info, Edit tags, Similar, etc.) rather than only in the compact
// queue list, which has none of that.
function playTrackList(results, bannerText) {
  previousResults = lastResults;
  previousSearchHasMore = searchHasMore;
  loadMoreBtn.hidden = true;
  lastResults = results;
  renderResults(lastResults);
  queue = results.filter((r) => r.file_path).map(toQueueEntry);
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

// Shared by search result rows and the queue panel's "now playing" track, so both get the
// same Similar/Versions/Info/Edit tags/+Playlist actions instead of search results being the
// only place you can do anything with a track.
function appendDiscoveryActions(r, tagsEl, buttonHost, panelHost) {
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
  infoBtn.addEventListener("click", () => {
    infoPanel.hidden = !infoPanel.hidden;
    infoBtn.textContent = infoPanel.hidden ? "Info" : "Hide info";
    if (!infoPanel.hidden) infoPanel.load();
  });
  buttonHost.appendChild(infoBtn);
  panelHost.appendChild(infoPanel);

  const editBtn = document.createElement("button");
  editBtn.textContent = "Edit tags";
  editBtn.className = "edit-tags-btn";
  const editPanel = buildEditPanel(r, tagsEl, editBtn);
  editBtn.addEventListener("click", () => {
    editPanel.hidden = !editPanel.hidden;
    editBtn.textContent = editPanel.hidden ? "Edit tags" : "Cancel";
  });
  buttonHost.appendChild(editBtn);
  panelHost.appendChild(editPanel);

  const playlistBtn = document.createElement("button");
  playlistBtn.textContent = "+ Playlist";
  playlistBtn.title = "Add this track to a playlist";
  const playlistPanel = buildPlaylistPickerPanel(r);
  playlistBtn.addEventListener("click", () => {
    playlistPanel.hidden = !playlistPanel.hidden;
    if (!playlistPanel.hidden) playlistPanel.load();
  });
  buttonHost.appendChild(playlistBtn);
  panelHost.appendChild(playlistPanel);
}

function renderNowPlayingActions(track) {
  nowPlayingActionsEl.innerHTML = "";
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

// Cheap, no-extra-query heuristic for whether the "Versions" button is worth showing: does
// this track's own title already look like a mix/edit/remix (mirrors backend _base_title's
// stripping logic)? Under-shows rather than over-shows - a plain-titled original won't get the
// button even if remixes of it exist elsewhere, but every remix-titled track will.
function looksLikeVersionedTitle(title) {
  if (!title) return false;
  const stripped = title.replace(/[\(\[][^\)\]]*[\)\]]/g, "").replace(/\s+-\s+.+$/, "").trim();
  return stripped.toLowerCase() !== title.trim().toLowerCase();
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
  // Keep the full result object (not just id/title/artist/file_path) so the "now playing" bar
  // can offer the same Similar/Info/Edit tags/+Playlist actions search results have, including
  // genre/mood pre-filled correctly in the edit panel.
  return r;
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
  saveBtn.disabled = !hasQueue;
  clearBtn.disabled = !hasQueue;
}

function removeFromQueue(i) {
  if (nextPrefetch && queue[i] && queue[i].id === nextPrefetch.id) cancelPrefetch();
  queue.splice(i, 1);
  if (i === currentIndex) {
    queuePlayer.pause();
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
  const duration = queuePlayer.duration;
  if (!duration || !isFinite(duration)) return;
  const percentListened = queuePlayer.currentTime / duration;
  fetch(`/track/${track.id}/play-event`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ percent_listened: percentListened }),
  }).catch(() => {});
}

async function playAt(i) {
  if (i < 0 || i >= queue.length) return;
  recordCurrentPlayProgress();
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
  renderNowPlayingActions(track);
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
saveBtn.addEventListener("click", async () => {
  const name = prompt("Playlist name?");
  if (!name || !name.trim()) return;
  await fetch("/playlists", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name.trim(), track_ids: queue.map((t) => t.id) }),
  });
  loadPlaylists();
});
clearBtn.addEventListener("click", () => {
  recordCurrentPlayProgress();
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
  renderNowPlayingActions(null);
  renderQueue();
});

async function loadPlaylists() {
  const res = await fetch("/playlists");
  const data = await res.json();
  renderPlaylists(data.playlists);
}

function renderPlaylists(playlists) {
  playlistsListEl.innerHTML = "";
  if (!playlists.length) {
    playlistsListEl.innerHTML = "<li>No saved playlists yet.</li>";
    return;
  }
  for (const p of playlists) {
    const li = document.createElement("li");

    const label = document.createElement("span");
    label.textContent = `${p.name} (${p.track_count} track${p.track_count === 1 ? "" : "s"})`;
    label.addEventListener("click", () => playPlaylist(p.id, p.name));
    li.appendChild(label);

    const deleteBtn = document.createElement("button");
    deleteBtn.textContent = "×";
    deleteBtn.title = "Delete playlist";
    deleteBtn.addEventListener("click", async () => {
      if (!confirm(`Delete playlist "${p.name}"?`)) return;
      await fetch(`/playlists/${p.id}`, { method: "DELETE" });
      loadPlaylists();
    });
    li.appendChild(deleteBtn);

    playlistsListEl.appendChild(li);
  }
}

async function playPlaylist(id, name) {
  const res = await fetch(`/playlists/${id}`);
  const data = await res.json();
  playTrackList(data.results, `Playlist: "${name}"`);
}

loadPlaylists();

async function showStats(endpointPath, label) {
  const res = await fetch(`/stats/${endpointPath}`);
  const data = await res.json();
  previousResults = lastResults;
  previousSearchHasMore = searchHasMore;
  loadMoreBtn.hidden = true;
  lastResults = data.results;
  renderResults(lastResults);
  setContextBanner(label, restorePreviousResults);
}

document.getElementById("stats-most-played").addEventListener("click", () => showStats("most-played", "Most played"));
document.getElementById("stats-most-skipped").addEventListener("click", () => showStats("most-skipped", "Most skipped"));
document.getElementById("stats-never-played").addEventListener("click", () => showStats("never-played", "Never played"));

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

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

const queuePlayer = document.getElementById("queue-player");
const castStatusEl = document.getElementById("cast-status");
const queueListEl = document.getElementById("queue-list");
const nowPlayingEl = document.getElementById("queue-now-playing");
const shuffleBtn = document.getElementById("queue-shuffle");
const prevBtn = document.getElementById("queue-prev");
const playPauseBtn = document.getElementById("queue-playpause");
const nextBtn = document.getElementById("queue-next");
const repeatBtn = document.getElementById("queue-repeat");
const saveBtn = document.getElementById("queue-save");
const cloneBtn = document.getElementById("queue-clone");
const clearBtn = document.getElementById("queue-clear");
const queueListToggleBtn = document.getElementById("queue-list-toggle");
const playlistsListEl = document.getElementById("playlists-list");
const playlistsToggleBtn = document.getElementById("playlists-toggle");
const loadMoreBtn = document.getElementById("load-more-btn");
const nowPlayingActionsEl = document.getElementById("queue-now-playing-actions");

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

let queue = []; // [{id, title, artist, file_path}]
let currentIndex = -1;
let currentPlaylistId = null; // set when the queue was loaded from a saved playlist (see playTrackList)
let currentPlaylistName = null;
let lastResults = [];
let previousResults = null;

let shuffle = false;
let shuffledOrder = []; // indices into queue, in shuffled playback order (only meaningful when shuffle is true)
let repeatMode = "none"; // "none" -> "all" -> "one" -> "none"

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

// trackIdsSource is either a fixed array (single track) or a function returning the current
// ids to add (e.g. "add all" needs the live result list, since it can grow via Load more or
// shrink via dismiss between opening the panel and clicking a playlist).
function buildPlaylistPickerPanel(trackIdsSource) {
  const trackIds = () => (typeof trackIdsSource === "function" ? trackIdsSource() : trackIdsSource);

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
      body: JSON.stringify({ name: name.trim(), track_ids: trackIds() }),
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
          for (const id of trackIds()) {
            await fetch(`/playlists/${p.id}/tracks`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ track_id: id }),
            });
          }
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
function playTrackList(results, bannerText, sourcePlaylist = null) {
  previousResults = lastResults;
  previousSearchHasMore = searchHasMore;
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
  aboutBtn.textContent = "Tell me more (AI)";
  const aboutEl = document.createElement("div");
  aboutEl.className = "info-about";

  const aboutEditBtn = document.createElement("button");
  aboutEditBtn.textContent = "Edit";
  const aboutTextarea = document.createElement("textarea");
  aboutTextarea.className = "info-about-edit";
  aboutTextarea.hidden = true;
  const aboutSaveBtn = document.createElement("button");
  aboutSaveBtn.textContent = "Save";
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
      aboutEditBtn.textContent = "Edit";
      return;
    }
    aboutTextarea.value = aboutEl.dataset.rawText || "";
    aboutTextarea.hidden = false;
    aboutEl.hidden = true;
    aboutSaveBtn.hidden = false;
    aboutEditBtn.textContent = "Cancel";
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
    aboutEl.dataset.rawText = data.text || "";
    aboutEl.textContent = data.text || "No background available (is ANTHROPIC_API_KEY configured?).";
    aboutTextarea.hidden = true;
    aboutEl.hidden = false;
    aboutSaveBtn.hidden = true;
    aboutEditBtn.textContent = "Edit";
  });

  const lyricsBtn = document.createElement("button");
  lyricsBtn.textContent = "Lyrics";
  const lyricsEl = document.createElement("div");
  lyricsEl.className = "info-lyrics";

  // Both buttons sit in a row together - without this, the block-level aboutEl/lyricsEl divs
  // between them (even empty) would force each button onto its own line.
  const actionsRow = document.createElement("div");
  actionsRow.className = "info-actions";
  actionsRow.append(aboutBtn, aboutEditBtn, lyricsBtn);

  // Stays visible (not hidden after first use) and re-labels to "Refresh" so a stale cached
  // lookup - e.g. looked up before a mislabeled artist/title got corrected - can be retried
  // rather than stuck forever (the backend caches lyrics, including "none found", indefinitely).
  lyricsBtn.addEventListener("click", async () => {
    const refreshing = lyricsBtn.textContent === "Refresh";
    lyricsBtn.disabled = true;
    lyricsBtn.textContent = "Looking up...";
    try {
      const res = await fetch(`/track/${r.id}/lyrics${refreshing ? "?refresh=true" : ""}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      // "error" (e.g. lrclib rate-limited) isn't cached server-side, unlike a confirmed "none" -
      // say so distinctly so a transient failure doesn't read as "definitely no lyrics exist".
      lyricsEl.textContent = data.text || (data.source === "error" ? "Lookup failed - try again." : "No lyrics found.");
    } catch (err) {
      lyricsEl.textContent = "Lyrics lookup failed.";
    } finally {
      lyricsBtn.textContent = "Refresh";
      lyricsBtn.disabled = false;
    }
  });

  // Stays visible (not hidden after first use) and re-labels to "Regenerate" so a stale cached
  // writeup - e.g. generated before a mislabeled artist/title got corrected - can be refreshed
  // rather than stuck forever (the backend caches ai_background indefinitely to save API calls).
  aboutBtn.addEventListener("click", async () => {
    const refreshing = aboutBtn.textContent === "Regenerate";
    aboutBtn.disabled = true;
    aboutBtn.textContent = "Thinking...";
    const res = await fetch(`/track/${r.id}/about${refreshing ? "?refresh=true" : ""}`);
    const data = await res.json();
    aboutEl.dataset.rawText = data.text || "";
    aboutEl.textContent = data.text || "No background available (is ANTHROPIC_API_KEY configured?).";
    aboutBtn.textContent = "Regenerate";
    aboutBtn.disabled = false;
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

  panel.append(factsEl, actionsRow, aboutEl, aboutTextarea, aboutSaveBtn, lyricsEl);
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
  });
  buttonHost.appendChild(favoriteBtn);

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
  if (!track) {
    nowPlayingActionsEl.hidden = true;
    return;
  }
  nowPlayingActionsEl.hidden = false;

  if (track.file_path) {
    const art = document.createElement("img");
    art.className = "album-art now-playing-art";
    art.src = `/track/${track.id}/art`;
    art.alt = "";
    art.addEventListener("error", () => art.remove(), { once: true });
    nowPlayingActionsEl.appendChild(art);
  }

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

  const titleInput = document.createElement("input");
  titleInput.placeholder = "Title";
  titleInput.value = r.title || "";

  const artistInput = document.createElement("input");
  artistInput.placeholder = "Artist";
  artistInput.value = r.artist || "";

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
        genre: genreInput.value.trim() || null,
        mood: moodInput.value.trim() || null,
        tags: tagsEditor.getTags(),
      }),
    });
    saveBtn.disabled = false;
    if (!res.ok) return;
    const updated = await res.json();
    const identityChanged = r.artist !== updated.artist || r.title !== updated.title;
    r.title = updated.title;
    r.artist = updated.artist;
    r.genre = updated.genre;
    r.mood = updated.mood;
    r.tags = updated.tags;
    tagsEditor.setTags(updated.tags);
    setTagsText(tagsEl, r);
    panel.hidden = true;
    editBtn.textContent = "Edit tags";
    // The title/artist are shown in several places built from this same track object (the
    // results list, the queue list, the now-playing bar) - re-render whichever of those
    // currently hold it rather than patching each one's DOM by hand.
    if (identityChanged) {
      if (lastResults.includes(r)) renderResults(lastResults);
      if (queue.includes(r)) renderQueue();
      if (queue[currentIndex] === r) {
        nowPlayingEl.textContent = `${r.title || "Untitled"} — ${r.artist || "Unknown artist"}`;
      }
    }
  });

  const row1 = document.createElement("div");
  row1.className = "edit-row";
  row1.append(titleInput, artistInput, genreInput, moodInput, suggestBtn, saveBtn);

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
  if (current !== queuePlayer) queuePlayer.pause();
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

const QUEUE_STATE_KEY = "pan_queue_state";
let lastPersistedTime = 0;

function persistQueueState() {
  try {
    localStorage.setItem(
      QUEUE_STATE_KEY,
      JSON.stringify({
        queue,
        currentIndex,
        shuffle,
        repeatMode,
        currentPlaylistId,
        currentPlaylistName,
        currentTime: queuePlayer.currentTime || 0,
      })
    );
  } catch {
    // localStorage unavailable (private browsing etc) - the queue just won't survive a refresh
  }
}

function restoreQueueState() {
  let saved;
  try {
    saved = JSON.parse(localStorage.getItem(QUEUE_STATE_KEY));
  } catch {
    return;
  }
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
    nowPlayingEl.textContent = `${track.title || "Untitled"} — ${track.artist || "Unknown artist"} (paused — press play to resume)`;
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

    queueListEl.appendChild(li);
  });

  const hasQueue = queue.length > 0;
  shuffleBtn.disabled = !hasQueue;
  prevBtn.disabled = !hasQueue;
  playPauseBtn.disabled = !hasQueue;
  nextBtn.disabled = !hasQueue;
  repeatBtn.disabled = !hasQueue;
  saveBtn.disabled = !hasQueue;
  cloneBtn.disabled = !hasQueue;
  clearBtn.disabled = !hasQueue;
  persistQueueState();
  refreshQueueButtons();
  updateSaveControls();
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

// Chromecast's receiver hardware fetches this URL itself, directly over the LAN, and does real
// TLS certificate validation - unlike a browser, it has no way to click through a warning for
// our self-signed cert. The app itself stays on HTTPS (needed for PWA install), but casting
// specifically needs a plain-HTTP path, served in parallel on :8080 (see Makefile).
const CAST_STREAM_ORIGIN = `http://${location.hostname}:8080`;

function isCasting() {
  return !!(castPlayer && castPlayer.isConnected);
}

// The native <audio controls> bar's own play/scrubber UI isn't wired to the Chromecast session
// at all - while casting it just sits there looking broken (stuck, unresponsive to clicks) since
// actual playback is happening on the receiver, not locally. Swap it for a plain status line;
// the real transport buttons (prev/play/next) already branch on isCasting() and work correctly.
function updateCastUi() {
  const casting = isCasting();
  queuePlayer.hidden = casting;
  castStatusEl.hidden = !casting;
  if (casting) {
    const session = cast.framework.CastContext.getInstance().getCurrentSession();
    const deviceName = session?.getCastDevice()?.friendlyName || "Chromecast";
    castStatusEl.textContent = `Casting to ${deviceName} - use the controls above to play/pause/skip.`;
  }
}

function castCurrentTrack() {
  if (!isCasting() || currentIndex === -1) return;
  const session = cast.framework.CastContext.getInstance().getCurrentSession();
  if (!session) return;
  const track = queue[currentIndex];
  const mediaInfo = new chrome.cast.media.MediaInfo(`${CAST_STREAM_ORIGIN}/stream/${track.id}`, guessCastContentType(track));
  mediaInfo.metadata = new chrome.cast.media.MusicTrackMediaMetadata();
  mediaInfo.metadata.title = track.title || "Untitled";
  mediaInfo.metadata.artist = track.artist || "";
  session.loadMedia(new chrome.cast.media.LoadRequest(mediaInfo)).then(() => queuePlayer.pause());
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
    const finished = castPlayer.playerState === chrome.cast.media.PlayerState.IDLE &&
      castPlayer.idleReason === chrome.cast.media.IdleReason.FINISHED;
    if (finished) advanceOnEnded();
  });
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
    nowPlayingEl.textContent = `${track.title || "Untitled"} — ${track.artist || "Unknown artist"}`;
    renderNowPlayingActions(track);
    renderQueue();
    updateMediaSessionMetadata(track);
    return;
  }

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
  updateMediaSessionMetadata(track);

  schedulePrefetch(queue[nextQueueIndex(i)]);
}

// Shared by the local <audio> "ended" event and the Chromecast receiver's IDLE/FINISHED state,
// so repeat-one/repeat-all/shuffle behave the same regardless of where playback is happening.
function advanceOnEnded() {
  if (repeatMode === "one") {
    playAt(currentIndex);
    return;
  }
  const next = nextQueueIndex(currentIndex);
  if (next !== -1) playAt(next);
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
  navigator.mediaSession.setActionHandler("play", () => (isCasting() ? castController.playOrPause() : queuePlayer.play()));
  navigator.mediaSession.setActionHandler("pause", () => (isCasting() ? castController.playOrPause() : queuePlayer.pause()));
  navigator.mediaSession.setActionHandler("previoustrack", () => playAt(prevQueueIndex(currentIndex)));
  navigator.mediaSession.setActionHandler("nexttrack", () => playAt(nextQueueIndex(currentIndex)));
}

queuePlayer.addEventListener("play", () => {
  playPauseBtn.innerHTML = "&#10074;&#10074;";
  pauseOtherPlayers(queuePlayer);
  if ("mediaSession" in navigator) navigator.mediaSession.playbackState = "playing";
});
queuePlayer.addEventListener("pause", () => {
  playPauseBtn.innerHTML = "&#9654;";
  if ("mediaSession" in navigator) navigator.mediaSession.playbackState = "paused";
  persistQueueState();
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
  } else if (queuePlayer.paused) {
    queuePlayer.play();
  } else {
    queuePlayer.pause();
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

    const actions = document.createElement("span");
    actions.className = "playlist-actions";

    const suggestBtn = document.createElement("button");
    suggestBtn.className = "suggest-btn";
    suggestBtn.textContent = "+ Suggest";
    suggestBtn.title = `Find more tracks like "${p.name}"`;
    suggestBtn.addEventListener("click", () => showPlaylistSuggestions(p.id, p.name));
    actions.appendChild(suggestBtn);

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
  loadMoreBtn.hidden = true;
  lastResults = data.results;
  renderResults(lastResults);
  setContextBanner(`Suggested for "${name}"`, restorePreviousResults);
}

loadPlaylists();
updateShuffleRepeatButtons();
restoreQueueState();

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

document.getElementById("stats-recently-played").addEventListener("click", () => showStats("recently-played", "Recently played"));
document.getElementById("stats-favorites").addEventListener("click", () => showStats("favorites", "Favorites"));
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

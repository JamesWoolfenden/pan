// Minimal app-shell cache so the UI frame (not live library data) loads instantly/offline.
// Search, streaming, stats, playlists, and app.js (already cache-busted via its own ?v= query
// string - see backend/app.py's index()) always go straight to the network: caching any of
// those would mean stale search results or broken playback, which defeats the point of this app.
const CACHE_NAME = "pan-shell-v2";
const SHELL_ASSETS = [
  "/",
  "/static/style.css",
  "/static/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) => Promise.all(names.filter((name) => name !== CACHE_NAME).map((name) => caches.delete(name))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (!SHELL_ASSETS.includes(url.pathname)) return;

  // Network-first, not cache-first/stale-while-revalidate: this app's own house rule (see
  // backend/app.py's no_store_static) is that a ui/ edit must show up on the very next load,
  // not one load behind. The cache here is purely an offline fallback, never the first choice.
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok) caches.open(CACHE_NAME).then((cache) => cache.put(event.request, response.clone()));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});

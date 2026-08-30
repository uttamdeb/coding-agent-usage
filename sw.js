/* Service worker for AI Usage Analytics PWA.
 * Caches the app shell so the installed PWA still loads when the server is
 * running; dynamic /api/* data always goes to the network first. */
const CACHE = "ai-usage-shell-v1";
const SHELL = [
  "/",
  "/static/app.css",
  "/static/core.js",
  "/static/charts.js",
  "/static/views.js",
  "/chart.js",
  "/manifest.json",
  "/static/icon-192.png",
  "/static/icon-512.png"
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);

  // API calls must be live; fall back to cache only when offline.
  if (url.pathname.startsWith("/api/")) {
    e.respondWith(
      fetch(e.request)
        .then((res) => res)
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // Static shell: prefer cache, then network.
  e.respondWith(
    caches.match(e.request).then((cached) => {
      const fetchPromise = fetch(e.request).then((res) => {
        if (res && res.status === 200) {
          const clone = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, clone));
        }
        return res;
      }).catch(() => cached);
      return cached || fetchPromise;
    })
  );
});

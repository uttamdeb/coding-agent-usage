/* Service worker for the AI Usage Analytics PWA.
 *
 * NETWORK-FIRST on purpose. The obvious choice for a PWA is cache-first, but the
 * server here is on the same machine — the network IS localhost, so there is no
 * latency to win — while the cost of cache-first is real: this app is updated with
 * `git pull`, and a cached shell keeps serving the OLD css/js until a second reload.
 * Worse, it can pair a new index.html with a stale views.js and break the page. So
 * the cache exists only as an offline fallback, never as the preferred source.
 *
 * The cache is therefore a safety net, not a speed-up: you always get what the
 * server currently has, and the copy on disk is only used when the server is down.
 */
const CACHE = "ai-usage-shell-v2";
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
  // Pre-seeding is best-effort: one 404 must not abort the whole install.
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => Promise.all(SHELL.map((u) => c.add(u).catch(() => {}))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;

  // Never touch anything that mutates state. POST /api/settings is CSRF-guarded
  // server-side on Origin and Content-Type; re-issuing it from here would put a
  // second actor between the page and that check for no benefit.
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;   // leave cross-origin alone

  // Live data must never be served from a cache — a stale token count or cost is
  // worse than an honest "cannot reach /api/data", which the UI already shows.
  if (url.pathname.startsWith("/api/")) return;

  e.respondWith(
    fetch(req)
      .then((res) => {
        if (res && res.status === 200 && res.type === "basic") {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      })
      .catch(() => caches.match(req).then((cached) => cached || Response.error()))
  );
});

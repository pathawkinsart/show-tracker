/* Show Tracker service worker — installable app shell only.
 * RULE: show-tracker-data.json is NEVER cached. The page fetches it
 * with cache: "no-store" and polls every 5 minutes; a stale cached
 * copy would show old counts. Every other same-origin request is
 * network-first with a cache fallback for the app shell only.
 */
const CACHE = "show-tracker-v1";
const SHELL = ["./", "index.html", "manifest.json", "icon-192.png", "icon-512.png", "apple-touch-icon.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  // Data file: network only, never cached, never served from cache.
  if (url.pathname.endsWith("show-tracker-data.json")) {
    event.respondWith(fetch(event.request, { cache: "no-store" }));
    return;
  }
  // Everything else same-origin: network first, cache fallback.
  if (url.origin === self.location.origin) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
          return response;
        })
        .catch(() => caches.match(event.request))
    );
  }
});

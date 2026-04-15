const CACHE_NAME = "dinary-v18";
const ASSETS = [
  "/",
  "/css/style.css",
  "/js/app.js",
  "/js/api.js",
  "/js/offline-queue.js",
  "/js/categories.js",
  "/js/qr-scanner.js",
  "/js/qr-scanner-lib.js",
  "/js/qr-scanner-worker.min.js",
  "/manifest.json",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS)),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)),
      ),
    ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // API calls: network only (offline queue handles failures in the app)
  if (url.pathname.startsWith("/api/")) {
    return;
  }

  // Static assets: cache-first, then network
  event.respondWith(
    caches.match(event.request).then(
      (cached) => cached || fetch(event.request).then((resp) => {
        if (resp.ok) {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return resp;
      }),
    ),
  );
});

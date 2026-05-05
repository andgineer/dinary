// One-time cleanup of leftovers from the legacy vanilla-JS PWA.
//
// The old `static/sw.js` named its caches `dinary-${APP_VERSION}` and
// hand-maintained an asset list. After cutover the new Workbox SW takes
// over the same /sw.js scope, but Workbox does NOT touch caches it did
// not create. Without this sweep, browsers that previously installed the
// old PWA would keep the stale `dinary-<old-version>` caches occupying
// quota until the user manually clears site data.
//
// Old IndexedDB queue data is acceptable to discard during the cutover:
// any expense queued by the legacy app that has not yet been flushed to
// the server is recoverable by the operator from device-side backups,
// and keeping two parallel offline databases alive risks divergence.
// We therefore delete the legacy `dinary` database here too so the
// new queue store starts from a clean slate.
//
// Idempotent: subsequent runs find nothing to delete and exit fast.

const LEGACY_CACHE_PREFIX = "dinary-";
// The legacy vanilla-JS PWA used IndexedDB database name "dinary"
// (see static/js/offline-queue.js). The Vue rewrite uses "dinary-v2"
// (stores/queue.js) so the new and old databases never collide.
const LEGACY_QUEUE_DB = "dinary";
const SWEEP_FLAG = "dinary:legacy-pwa-sweep-done";

async function deleteLegacyCaches() {
  if (!("caches" in self)) return;
  const names = await caches.keys();
  await Promise.all(
    names
      .filter((name) => name.startsWith(LEGACY_CACHE_PREFIX))
      .map((name) => caches.delete(name)),
  );
}

function deleteLegacyQueueDb() {
  if (!("indexedDB" in self)) return;
  try {
    indexedDB.deleteDatabase(LEGACY_QUEUE_DB);
  } catch {
    // Best-effort cleanup; swallow errors so we never block app boot.
  }
}

// Unregister any service-worker registrations that pre-date Workbox.
// vite-plugin-pwa will register its own SW after Vue mounts, but if the
// browser still has the legacy `static/sw.js` registration active it
// will keep serving precached vanilla-JS assets ahead of any update.
// We unregister every registration whose scope is the app origin; the
// new Workbox SW re-registers on the same scope once Vue has booted.
async function unregisterStaleServiceWorkers() {
  if (!("serviceWorker" in navigator)) return;
  try {
    const regs = await navigator.serviceWorker.getRegistrations();
    await Promise.all(regs.map((r) => r.unregister().catch(() => {})));
  } catch {
    // getRegistrations may throw under hardened privacy settings; fine.
  }
}

export async function runLegacyPwaCleanup() {
  if (typeof localStorage === "undefined") return;
  if (localStorage.getItem(SWEEP_FLAG) === "1") return;
  try {
    await unregisterStaleServiceWorkers();
    await deleteLegacyCaches();
    deleteLegacyQueueDb();
  } finally {
    try {
      localStorage.setItem(SWEEP_FLAG, "1");
    } catch {
      // Private mode / quota: cleanup will be retried next launch.
    }
  }
}

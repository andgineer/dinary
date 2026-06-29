# PWA Offline Architecture

## IndexedDB reconnect pattern

The offline queue never caches the database connection object across calls.
Instead it caches a Promise that resolves to the connection, and nulls that
Promise on `onclose` and `onversionchange` events. The next operation
re-opens cleanly by re-entering the open path.

Caching the connection object directly causes a "Database is disconnecting"
error when the browser closes the connection (e.g. on tab re-focus after a
long idle). That error froze the queue silently — no more expenses could be
submitted until the user reloaded. Caching the Promise instead of the
connection avoids this: a stale Promise is detected and replaced, not a stale
connection handle that throws.

## Queued expenses do not freeze exchange rates

When an expense is queued offline, only the amount and currency code are
stored. No exchange rate is frozen into the payload at queue time. The server
resolves the rate when the queue is flushed.

If the server cannot resolve a rate for a queued item at flush time, the item
stays in the queue and the error is surfaced to the user. This is preferable to
freezing a client-side rate that may be stale (the client's rate cache is at
most 30 minutes old) or unavailable (no rate was loaded yet offline).

## Service worker update strategy

`registerType: 'autoUpdate'` with `skipWaiting` and `clientsClaim` means a
newly deployed build takes effect on the next page reload without requiring the
user to manually dismiss an update prompt. Silent auto-update is the right
trade-off — there is no multi-tab coordination concern and no risk of
disrupting concurrent sessions.

## Online flag and request gating

`isOnline` (derived from `navigator.onLine` and browser `online`/`offline` events) gates background and automatic requests only — infinite scroll, auto-loads on mount, and the retry timer. These are suppressed when `isOnline = false` to avoid flooding the user with connection errors when the device is genuinely offline.

User-initiated actions (pressing Save, Refresh, Confirm, Delete, etc.) always proceed regardless of `isOnline`. On success they dispatch a synthetic `online` event, which clears the flag if it was stuck and triggers queue flush. This ensures that a stuck offline state — e.g. caused by a stale service worker or a browser event that fired without a corresponding reconnect event — can always be escaped by a single user action without requiring a page reload.

## QR scanner is fully offline

The `zbar-wasm` library is bundled into the PWA build (not loaded from a CDN). Workbox precaches it on first load. The scanner requires no network access after initial install.

## Rollback is image-level

There is no in-tree rollback path for the PWA. Rollback means redeploying a
prior container image. Keeping a dead fallback path in-tree would accumulate
drift and create false confidence in a code path that is never tested.

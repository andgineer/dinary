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

## A server that does not answer

An unreachable server is never reported as a bare network error. Sending a
queued expense or receipt gives up after a bounded wait instead of the
browser's own minute, and the failure is told apart by how it looked from the
page: the device offline, the server's name not reachable (Tailscale off on the
device fails at once, because public DNS does not know tailnet names), the
server silent (a stopped VM behind a connected Tailscale never answers), or the
VM up with the app not running (Tailscale serve answers 502, which the app never
sends itself). The queue window, opened from the queued-items strip, shows what
the failure looked like, when the server last answered on this device, and an
ordered list of what to check — the stopped VM's **Start** button among them.
The kind of failure decides which check comes first; the app never claims to
know the cause.

The service worker's self-repair — unregistering the worker and reloading after
repeated connection failures while the device is online — first fetches the
worker's own script, which bypasses the worker. It resets only when that fetch
succeeds: when the server itself cannot be reached, a reset would leave the
installed app nothing to load from, and it would open on the browser's error
page until the server came back.

## QR scanner is fully offline

The `zbar-wasm` library is bundled into the PWA build (not loaded from a CDN). Workbox precaches it on first load. The scanner requires no network access after initial install.

## Rollback is image-level

There is no in-tree rollback path for the PWA. Rollback means redeploying a
prior container image. Keeping a dead fallback path in-tree would accumulate
drift and create false confidence in a code path that is never tested.

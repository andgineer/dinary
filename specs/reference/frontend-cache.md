# Frontend Cache and Stale-Data Policy

## useStaleCache composable

All Pinia stores that talk to the server share the `useStaleCache` composable.
It maintains two pieces of localStorage-persisted state per store:

- **`dirtyFlag`** (`dinary:<store>:dirty = "1"`) — set explicitly when something
  happened that makes the cached data suspect; cleared by `stampFresh()`.
- **`lastFetchedAt`** (timestamp) — set by `stampFresh()` after every successful
  fetch; drives the 24-hour TTL check.

`isStale()` returns `true` when any of: `dirtyFlag` is set, `lastFetchedAt` is
absent, or the age exceeds the TTL (default 24 h).  Any of these conditions
causes `loadIfNeeded()` to reset the cache and fetch page 1.

`stampFresh()` clears `dirtyFlag` **and** writes `lastFetchedAt`.  It must be
called after every successful full-refresh so the 24-hour clock starts.
`bumpFetchTime()` only writes `lastFetchedAt` without clearing `dirtyFlag`; it
is not used by any store — prefer `stampFresh()` always.

## Review store dirty-flag sources

`reviewStore.markDirty()` is called in two places:

1. `flushReceiptQueue` — immediately after a receipt URL is successfully POSTed
   (the server now has a new receipt to classify).
2. `review.loadNextPage()` — while the server-side receipt queue is non-empty
   (see re-mark-dirty rule below).

## Review store re-mark-dirty rule

After every successful `loadNextPage()` the review store inspects the server's
`receipts_queue` counters.  If any bucket (`pending`, `in_progress`, `sleeping`,
`poisoned`) is non-zero the store immediately calls its own `markDirty()` **and**
`useLlmStore().markDirty()`.

Effect: as long as receipts are being processed on the server, every call to
`loadIfNeeded()` (tab switch to review, app foreground, online event) will
re-fetch.  Once all buckets reach zero the dirty flag is not re-set, `stampFresh()`
from the last fetch starts the 24-hour clock, and subsequent opens skip the request.

## LLM store

`markDirty()` is called on the LLM store in two places:

1. `flushReceiptQueue` — immediately after a receipt URL is successfully POSTed to
   the server (LLM provider status may change).
2. `review.loadNextPage()` — while the server-side receipt queue is non-empty (see
   above).

After each `refresh()` the LLM store always calls `stampFresh()`, regardless of
provider rate-limit state.  Rate-limit display is informational; it does not
justify re-fetching on every page open.

## Catalog store

The catalog store keeps its own freshness timestamp rather than a boolean dirty
flag, because catalog mutations made on this device patch the cached snapshot in
place and bump its version locally. Cross-device changes are picked up on the
same TTL-based refresh.

Foreground and visibility events refresh the catalog only when that TTL has
expired — they never force an unconditional refetch. Combined with the
ETag-based conditional GET, returning to the app within the freshness window
costs no catalog traffic.

## Badge visibility

`showReviewBadge` (App.vue computed) is true when **any** of:

- `reviewStore.dirtyFlag` — stale or unconfirmed data
- `reviewStore.doubtfulCount > 0` — rules awaiting user approval
- any `receiptsQueue` bucket > 0 — server still processing receipts

The badge disappears automatically once all three conditions are false, which
happens when the server confirms an empty queue and the user has approved all
doubtful rules.

## Background probe on visibility / online events

App.vue triggers `reviewStore.loadIfNeeded()` in three situations beyond the
user navigating to the review tab:

| Trigger | Condition | Effect |
|---|---|---|
| Cold start (`init()`) | online AND `dirtyFlag` | Fetches before user opens review |
| `visibilitychange: visible` | `navigator.onLine` AND `dirtyFlag` | Handles iOS app-switcher return |
| `watch(isOnline)` goes true | `dirtyFlag` | Handles reconnect after offline gap |

The visibility handler uses `navigator.onLine` (always current) rather than the
reactive `isOnline` ref, because iOS may delay firing the `online` event after a
wake-from-background.  If `isOnline.value` lags behind `navigator.onLine`, the
handler dispatches a synthetic `online` event to sync the ref and unblock other
components.

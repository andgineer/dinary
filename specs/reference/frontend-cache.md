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
A dirty mark made while a fetch is in flight survives that fetch: the server
may have built its response before the change behind the mark, so the fetch
still records its time, but the store stays dirty until a fetch that started
after the mark completes.
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

## Analytics store dirty-flag sources

The stats page is marked dirty by every action that can change a figure it shows:

1. An expense reaching the server from the offline queue.
2. A receipt URL successfully POSTed (not a duplicate).
3. An expense correction, edit or delete, a stuck-receipt resolution, or a receipt
   delete on the review page.
4. An income added, edited or deleted.
5. Any catalog change that can alter a name or an entry the page shows: adding,
   editing, deactivating or deleting an event, a category group or a tag;
   switching the category template; renaming a category or moving it to another
   group; and activating a category, which can place it in a group. Hiding or
   unhiding a category changes nothing the page shows, so it does not mark it.
6. The review feed reporting receipts still being processed.

Bulk rule confirmation does not mark it: it changes only confidence, never an
amount, category, event or date.

The page has no badge and no background probe; it refetches only when opened
while stale.

Per-event breakdowns are held in memory only and are dropped whenever the summary
is refetched, so they follow the same dirty flag. A breakdown requested before
that refetch landed is requested again rather than kept.

## Analytics store re-mark-dirty rule

The summary response carries the same receipt-queue counters as the review feed,
read before the aggregates so a job finishing mid-request can cost at most one
extra refetch, never a fresh stamp on stale totals. After every successful fetch
the store stamps itself fresh and immediately re-marks itself dirty while any
receipt is still processing — pending, in progress or sleeping until its next
retry. This applies on a cold start too, when nothing marked the store dirty
beforehand.

A poisoned job is terminal, not "still processing", and does not re-mark the
store: only a re-submission of the receipt can revive it, and that already marks
the store dirty. Counting it would force a refetch on every open for as long as
one failed receipt exists. A sleeping job does count, even one that keeps
failing, because it may still complete on its own; the cost is one request per
page open while it stays asleep.

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

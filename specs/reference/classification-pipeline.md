# Classification Pipeline — Architecture

## No DB connections during HTTP

The pipeline never holds an open database connection while making a network call.
Connections are opened for synchronous SQLite work, closed before any `await`
that touches the network, then re-opened for the next write phase. This prevents
connection contention under concurrent job processing.

## Confidence rules

**Rule-hit threshold**: a stored rule with `confidence_level = 1` is treated as
a miss and its item is re-queued for LLM classification. Confidence-1 means "seen
but uncertain" — the rule exists only to record normalised name history, not to
make a definitive assignment.

**Confidence penalty**: if the broker fell back to a lower-priority provider or
a provider failed mid-loop, a penalty is applied to LLM-classified items'
confidence. Rule-hit items are not penalised — their stored confidence is already
calibrated from a prior LLM call. The penalty signals that the result comes from
a less reliable source than the configured primary.

**Always-unconditional alternatives**: the system prompt always requests
alternative categories, regardless of confidence. Gating on confidence was tried
and caused the model to inflate confidence-4 scores to avoid the extra work of
listing alternatives. Requiring them unconditionally keeps confidence calibrated.

**User corrections set confidence 4 and clear alternatives**: a user correction
is the highest authority. Clearing stored alternatives prevents stale LLM
suggestions from surfacing in future review flows after the user has expressed a
definitive preference.

## Error handling strategy

Network/parse errors on receipt fetch are treated differently:
- Transient network errors: release the job for retry later.
- Structural parse errors (receipt can't be parsed at all): poison the job to
  prevent infinite retries.

This distinction matters because the government fiscal API is unreliable;
treating all failures as permanent would silently discard valid receipts. See
[receipt-fetching.md](receipt-fetching.md) for the fetch strategy and
reliability characteristics of `suf.purs.gov.rs`.

## Sync DB calls on the event loop

Every sync DB call that involves `BEGIN IMMEDIATE` is wrapped in
`asyncio.to_thread`. SQLite's write lock wait (up to `busy_timeout`, 5 s) would
block the event loop and freeze all API responses if called directly from a
coroutine. This matters in two cases: two receipts processed in parallel both
reaching the persist step simultaneously, and an API write racing with the
classification drain.

Sheet logging already used `asyncio.to_thread` for the same reason; the
classification drain follows the same pattern.

## FastAPI routes are plain `def`

Receipt and expense route handlers are plain `def` (synchronous), not
`async def` wrapping `asyncio.to_thread`. FastAPI automatically runs `def`
handlers in its thread pool — the `async def + to_thread` double-hop adds a
pointless context switch with no benefit.

The consequence of plain `def` routes is that `notify_new_receipt()` (which
wakes the classification drain) must use `loop.call_soon_threadsafe()` rather
than calling `asyncio.Event.set()` directly. `asyncio.Event.set()` is not
thread-safe; calling it from a thread-pool worker races with the event loop.
`notify_new_work()` in sheet logging uses the same pattern.

## Provider error diagnostics

The call log stores the first 300 characters of the HTTP response body on any
non-2xx provider response. When a provider has a bad API key, exhausted quota,
or a billing block, the error text surfaces in the admin UI without requiring
the operator to dig through server logs.

## Parallelism cap

No cap on concurrent job processing. For a personal expense tracker the realistic
backlog is tens of jobs; a bounded semaphore would add complexity without
providing meaningful protection.

## Auto-attach events

When a job is processed, the pipeline checks for an active event whose date range
covers the receipt's purchase timestamp and automatically attaches it to every
expense from that receipt. This means vacation or travel events tag all purchases
without requiring the user to manually attach them in the review flow.

## LLM provider dispatch

Provider selection, failover, rate-limit tracking, and storage implementations
are covered in [llm-providers.md](llm-providers.md).

## Classification rules attach to chains, not stores

A rule learned at one branch of a retail chain applies to all branches. Storing
rules at store granularity would waste LLM calls and leave new branches without
coverage until they accumulated their own history. See
[stores.md](stores.md) for the store/chain data model.

# Classification Pipeline — Architecture

## Overview

Receipt classification runs as a background asyncio task that drains
`receipt_classification_jobs` in parallel coroutines. LLM provider dispatch is
handled by `LLMBroker` — a self-contained module with no DB connections on its
hot path, designed to be extractable as a standalone package.

---

## Drain loop (`background/classification/task.py`)

The drain returns immediately if `settings.receipt_classification_enabled` is `False`
(controlled by the `DINARY_RECEIPT_CLASSIFICATION_ENABLED` env var, default `True`).

```python
async def receipt_classification_task(broker: LLMBroker) -> None:
    if not settings.receipt_classification_enabled:
        return
    await _drain_all_pending(broker)   # pick up jobs surviving a restart
    while True:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_wakeup_event.wait(), timeout=300)
        _wakeup_event.clear()          # clear BEFORE drain so arrivals during drain re-set it
        await _drain_all_pending(broker)
```

`_drain_all_pending` claims all pending jobs then runs them as parallel coroutines:

```python
async def _drain_all_pending(broker: LLMBroker) -> None:
    jobs = _claim_all_pending()        # synchronous SQLite, fine on event loop
    if jobs:
        await asyncio.gather(*[_process_job(job, broker) for job in jobs], return_exceptions=True)
```

`_claim_all_pending` opens one connection and loops `claim_next_job(conn)` until
it returns `None`, collecting all claimable jobs before closing the connection.

No cap on parallelism — realistic backlog for a personal tracker is tens of jobs.
`return_exceptions=True` ensures one failing job does not cancel others.

### Rule-hit threshold

`_run_rules_pass` only treats a rule match as a hit when `confidence_level > 1`.
A stored rule with `confidence_level = 1` is treated as a miss and its item is
added to the LLM queue instead:

```python
if rule and rule[1] > 1:
    rule_hits[item.id] = rule
else:
    llm_queue.append((item.id, norm))
```

### Connection discipline

Each job opens DB connections only when doing synchronous work; every connection
is closed before the next `await` that touches the network.

`_process_job` flow:

```
await parse_receipt(url)                     ← network; no connection held
_save_parsed()                               ← short-lived conn, opens/closes internally
open conn → check existing store_id → close conn
await resolve_store(broker, ...)             ← network; no connection held
open conn → UPDATE receipts.store_id → close conn
open conn → idempotency check + get_receipt_items → close conn
await _classify_and_persist(broker, ...)     ← see below
```

Inside `_classify_and_persist` (takes no `conn` parameter):

```
open conn → idempotency check + rule hits + categories + tags → close conn
await _run_llm_pass(broker, ...)             ← network; zero connections held
open conn → write expenses + complete job + trim_llm_call_log → close conn
```

### Per-item expense creation

`_persist_classification_results` iterates items individually. For each item with
`confidence >= 2` and a non-null `category_id`, one `expenses` row is inserted with
`amount = item.total_price` and `currency_original = 'RSD'` (hardcoded).

Before the per-item loop: `_find_auto_attach_event` checks for an active event whose
date range covers the receipt's `purchase_datetime`. If found, its `id` is written
into `event_id` on every expense inserted from this receipt, and its auto-tag IDs
(`resolve_event_auto_tag_ids`) are merged into each expense's `expense_tags`.

Items at confidence ≤ 1 or with no category receive no expense row; their
`receipt_items` row is updated with `category_id = NULL` and `confidence_level = 1`.

After each expense insert: `enqueue_for_logging`, `update_receipt_item`, and
`create_or_update_rule` (guarded by `conf >= 2` and item not already in `rule_hits`)
are called per item. `expense_tags` rows are inserted for every tag id from the
matched rule or LLM result, plus any event auto-tag ids, deduplicated.

### Confidence penalty

`total_penalty` is computed before the write phase:

```python
total_penalty = (1 if job.used_journal_fallback else 0) + (1 if used_failover else 0)
```

Each LLM-classified item's confidence is reduced by `total_penalty` (floor 1).
Rule-hit items are not penalised — stored confidence is used as-is.

### Error handling per job

| Exception | Action |
|---|---|
| `ParserRequestException`, `OSError` | release job (retry later) |
| `ParserParseException` | poison job |
| Any other | poison job |

---

## LLM provider dispatch (`adapters/llmbroker.py`)

`LLMBroker` knows nothing about receipts or categories. It accepts OpenAI-style
messages and returns a string. All business logic (prompt building, response
parsing) lives in `background/classification/llm_client.py`.

### Public interface

```python
class LLMBroker:
    async def start(self) -> None   # load providers, start background tasks
    async def stop(self) -> None    # cancel background tasks; flush remaining log queue

    async def complete(
        self,
        messages: list[dict],
        context_id: Any | None = None,  # opaque audit key — passed through to storage
    ) -> tuple[str, bool]:              # (content, used_fallback)

    async def try_complete(
        self,
        messages: list[dict],
    ) -> str | None                     # None if no provider available right now
```

`used_fallback=True` means either a lower-priority provider was chosen (round-robin
skipped higher-priority ones that were cooling) or a provider failed mid-loop and
the next provider was tried. Callers apply a confidence penalty in either case.

`context_id` flows from caller → `CallEvent` → `BrokerStorage.on_call_logged`.
For dinary it carries `receipt_id`. `llm_client.classify_receipt` converts the
`int | None` receipt_id to `str | None` before passing it to `broker.complete()`,
so `CallEvent.context_id` and the `llmbroker_call_log.receipt_id` column are stored
as strings.

### Hot-path loop — zero DB connections during HTTP

Each iteration of `complete()`'s retry loop has three phases:

**Phase 1 — pick provider (no I/O):**
Reads `_providers` and `_provider_events` from memory. If all providers are
cooling down, `asyncio.wait`s on their events. No connection opened.

**Phase 2 — HTTP call:**
`await _call_provider(provider, messages)` via `httpx.AsyncClient`. No connection
held at this point.

**Phase 3 — fire-and-forget:**
Broker computes `until = now + (Retry-After header value OR provider.rate_limit_sec)`.
`_log_queue.put_nowait(CallEvent(..., rate_limited_until=until))` — non-blocking.
If 429/503: clears the provider's `asyncio.Event` and schedules `call_later`
to re-set it at `until` (in-memory only, no connection).

### Internal state

| Field | Type | Purpose |
|---|---|---|
| `_providers` | `list[ProviderConfig]` | In-memory cache, sorted by (priority, id) |
| `_provider_events` | `dict[Any, Event]` | Rate-limit events; set = available |
| `_log_queue` | `asyncio.Queue[CallEvent]` | Async bridge to storage writes |
| `_next_idx` | `int` | Round-robin counter; resets on restart |

### Background tasks

**`_refresh_task`** — reloads providers from storage every `refresh_interval` seconds
(default 60). Preserves event state for known providers; initialises events for
newly added ones.

**`_log_drain_task`** — pulls `CallEvent`s from the queue, calls
`storage.on_call_logged(event)`. For events where `rate_limited_until is not None`,
also calls `storage.on_rate_limited(event.provider_id, event.rate_limited_until)`
so the DB value survives restarts.

---

## Storage abstraction (`adapters/llmbroker.py`, `adapters/llm_storage.py`)

`BrokerStorage` is a Protocol — the broker has no import of `dinary.db` or any
SQLite module.

```python
class BrokerStorage(Protocol):
    async def load_providers(self) -> list[ProviderConfig]: ...
    async def on_call_logged(self, event: CallEvent) -> None: ...
    async def on_rate_limited(self, provider_id: Any, until: datetime) -> None: ...
```

`NullStorage` (no-op) ships alongside the broker for tests and no-persistence usage.

`LLMBrokerStorage` in `adapters/llm_storage.py` is the `aiosqlite`-backed
implementation. Every method is a genuine `async def` using
`async with aiosqlite.connect(db_path)` — no `asyncio.to_thread`, no sync helpers.
Each method opens a short-lived connection, does its work, commits, and closes it.

`LLMBrokerStorage.load_providers()` auto-seeds `llmbroker_providers` from
`.deploy/llm_providers.toml` on first call when the table is empty. Falls back
to `DINARY_LLM_*` env vars when the TOML is absent.

---

## DB tables

LLM-related tables are prefixed `llmbroker_` to signal ownership and ease future
extraction as a standalone package:

| Table | Purpose |
|---|---|
| `llmbroker_providers` | LLM provider config |
| `llmbroker_call_log` | Per-call audit log (trimmed to last 200 rows after each drain sweep) |

`llmbroker_providers` has a `default_rate_limit_sec INTEGER NOT NULL DEFAULT 60`
column — the fallback wait when the LLM API returns 429 without a `Retry-After`
header. Configurable per provider in `.deploy/llm_providers.toml` via `rate_limit_sec`.

---

## Classification client (`background/classification/llm_client.py`)

`llm_client.py` owns prompt-building and response-parsing. Two functions
call the broker:

```python
async def classify_receipt(
    broker: LLMBroker, items, store_name_raw, categories, tags,
    context_id: int | None = None,
) -> tuple[list[ClassificationResult], bool]: ...

async def get_chain_name(broker: LLMBroker, store_name_raw: str) -> str: ...
```

`get_chain_name` uses `broker.try_complete()` — returns the raw store name immediately
if no provider is available rather than waiting. Used by `store_resolver.resolve_store`.

`OpenAICompatibleClient` is also in this module — a direct `httpx`-based client used
by the admin test endpoint (`api/controllers/llm.py`) and the CLI `inv classify-receipt`
task. It is not used by the drain loop, which goes through `LLMBroker` instead.

`ClassificationResult` carries:
- `category_id: int | None`
- `confidence_level: int` (1–4)
- `item_name_normalized: str`
- `alternative_category_ids: list[int]` — 2–3 next-best category IDs; always requested, capped at 3 by `_parse_response`
- `tag_ids: list[int]` — tag IDs from the provided tag set that clearly apply; empty when none fit

The system prompt always requests alternatives unconditionally. Gating on confidence
was tried but caused the model to inflate confidence-4 scores to avoid the extra
work of listing alternatives; always requiring them keeps confidence calibrated.
Tags are requested as a subset of the active tag dict passed by the drain; the LLM
is instructed not to guess. `_parse_response` caps alternatives at 3 and filters
tags to IDs present in the provided set.

`load_tags(conn)` loads `SELECT id, name FROM tags WHERE is_active = 1` alongside
`load_categories(conn)`; both are public functions in this module used by the drain
task to build the dicts passed to `classify_receipt` on every LLM pass.

---

## Classification rules (`db/classification_rules.py`)

`RuleSpec` carries `alternative_category_ids: tuple[int, ...]` and
`tag_ids: tuple[int, ...]` in addition to the base fields.

`create_or_update_rule` behaviour by source:

- `source='llm'`: persists `alternative_category_ids` and `tag_ids` as JSON arrays.
- `source='user_correction'`: sets `confidence_level = 4` regardless of what is passed
  in `RuleSpec`. On UPDATE: overwrites `tag_ids` with the user-supplied value and
  explicitly clears `alternative_category_ids` to `'[]'`. On INSERT (no existing rule):
  stores `alternative_category_ids` and `tag_ids` as-is from the spec; the
  `alternative_category_ids = []` invariant is not enforced by the code for the insert
  path — callers are expected to pass an empty tuple.

`classify_by_rules` returns `(category_id, confidence_level, tag_ids)` so the
drain can apply stored tags without an LLM call on rule hits.

---

## Broker lifecycle (`main.py`)

```python
@asynccontextmanager
async def _lifespan(_app: FastAPI):
    storage.init_db()
    broker = LLMBroker(LLMBrokerStorage())
    await broker.start()
    await warm_sheet_mapping()
    sheet_logging_bg = asyncio.create_task(sheet_logging_task())
    rate_prefetch_bg = asyncio.create_task(rate_prefetch_task())
    receipt_classification_bg = asyncio.create_task(receipt_classification_task(broker))
    try:
        yield
    finally:
        sheet_logging_bg.cancel()
        rate_prefetch_bg.cancel()
        receipt_classification_bg.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sheet_logging_bg
        with contextlib.suppress(asyncio.CancelledError):
            await rate_prefetch_bg
        with contextlib.suppress(asyncio.CancelledError):
            await receipt_classification_bg
        await broker.stop()
```

One broker instance per process, started at lifespan and injected wherever LLM
access is needed. All three background tasks are stored so they can be cancelled
and awaited cleanly on shutdown before the broker is stopped.

---

## API extensions

### Frequent categories (`api/controllers/catalog.py`)

`FrequentCategory(id, name)` is computed by `frequent_categories_sync`:

```sql
SELECT e.category_id, c.name, COUNT(*) AS cnt
  FROM expenses e JOIN categories c ON c.id = e.category_id
 WHERE c.is_active = 1 AND e.receipt_id IS NULL
   AND e.datetime >= datetime('now', '-3 months')
 GROUP BY e.category_id ORDER BY cnt DESC LIMIT 5
```

`receipt_id IS NULL` excludes LLM-classified items — only manual choices count
toward quick-pick suggestions. The list is included in `CatalogResponse` and in
every `POST /api/expenses` response.

### `GET /api/expenses`

Returns expenses paginated newest-first (`page` and `page_size` query params,
default `page_size=20`). Response shape:

```json
{ "items": [...], "has_more": bool, "total": int }
```

Each `ExpenseListItem` includes:
`id`, `datetime`, `category_id`, `category_name`, `event_id`, `event_name`,
`store_id`, `store_name`, `receipt_id`, `confidence_level`, `amount_original`,
`currency_original`, `tags: [{id, name}]`, `has_rule: bool`, `item_name: str | None`.

`has_rule` is `true` when a `classification_rules` row exists for the
`name_normalized` of the linked `receipt_items` row (store-specific or generic);
always `false` for expenses where `receipt_id IS NULL`.

`item_name` is `name_raw` from the linked `receipt_items` row; `null` for
non-receipt expenses.

### `PATCH /api/expenses/{id}`

```python
class ExpenseEditRequest(BaseModel):
    category_id: int | None = None
    tag_ids: list[int] = Field(default_factory=list)
    event_id: int | None = None
    clear_event: bool = False
    scope: CorrectionScope = CorrectionScope.single
    update_rule: bool = False
```

`edit_expense_sync` applies changes in this order:
1. If `category_id` provided: delegates to `correct_category_sync` (handles `scope`).
2. Replaces `expense_tags` for this expense only with `tag_ids`; other expenses
   affected by the scope correction retain their own tags.
3. Updates `event_id`: `clear_event=True` sets NULL; non-None `event_id` updates;
   otherwise keeps current.
4. If `update_rule=True` and the expense has a linked `receipt_items` row with
   `name_normalized` set: looks up whether a matching rule exists in
   `classification_rules` (store-specific or generic). If a rule exists, calls
   `create_or_update_rule` with `source='user_correction'`, the current
   `category_id`, and `tag_ids`. If no rule exists, logs an error and skips — this
   indicates a classification bug since `update_rule=True` is only valid for
   receipt-derived expenses that should already have a rule from the drain.

Returns `ExpenseEditResponse(id, category_id, category_name, tag_ids, event_id, event_name)`.

### Rules feed (`api/controllers/rules.py`)

Each rule row in the feed includes:
- `alternative_categories: [{id, name}]` — resolved from the stored
  `alternative_category_ids` JSON array; inactive categories are silently dropped.
  Empty when the array is empty or all stored ids are inactive.
- `tags: [{id, name}]` — resolved from `tag_ids`; empty list when none.

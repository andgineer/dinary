# Classification Background Task — Architecture

## Overview

Receipt classification runs as a background asyncio task that drains
`receipt_classification_jobs` in parallel coroutines. LLM provider dispatch is
handled by `LLMBroker` — a self-contained module with no DB connections on its
hot path, ready to be extracted as a standalone package.

---

## Drain loop (`background/classification/task.py`)

```python
async def receipt_classification_task(broker: LLMBroker) -> None:
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
        await asyncio.gather(*[_process_job(broker, job) for job in jobs], return_exceptions=True)
```

No cap on parallelism — realistic backlog for a personal tracker is tens of jobs.
`return_exceptions=True` ensures one failing job does not cancel others.

### Connection discipline in `_process_job`

Each job opens DB connections only when doing synchronous work; every connection
is closed before the next `await` that touches the network:

```
open conn → items + idempotency check → close conn
open conn → rule hits + categories + tags → close conn
await broker.complete(...)        ← zero connections held here
open conn → write expenses + complete job → close conn
```

`_classify_and_persist` owns the middle two phases and takes no `conn` parameter —
it opens and closes its own connections around the sync work and the write transaction.

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
parsing) lives in `adapters/llm_client.py`.

### Public interface

```python
class LLMBroker:
    async def start(self) -> None   # load providers, start background tasks
    async def stop(self) -> None    # drain log queue, cancel background tasks

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

`used_fallback=True` means the highest-priority available provider was not the
first-priority one — callers apply a confidence penalty.

`context_id` flows untouched from caller → `CallEvent` → `BrokerStorage.on_call_logged`.
For dinary it carries `receipt_id` so call-log rows can be traced back to a receipt.

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
SQLite module. This makes it extractable as a standalone package.

```python
class BrokerStorage(Protocol):
    async def load_providers(self) -> list[ProviderConfig]: ...
    async def on_call_logged(self, event: CallEvent) -> None: ...
    async def on_rate_limited(self, provider_id: Any, until: datetime) -> None: ...
```

`NullStorage` (no-op) ships alongside the broker for tests and no-persistence usage.

`DinaryStorage` in `adapters/llm_storage.py` is the SQLite implementation. Every
method uses `asyncio.to_thread` so synchronous SQLite calls never block the event
loop. Each method opens a short-lived connection, does its work, closes it.

`DinaryStorage.load_providers()` auto-seeds `llmbroker_providers` from
`.deploy/llm_providers.toml` on first call when the table is empty. Falls back
to `DINARY_LLM_*` env vars when the TOML is absent. `llm_bootstrap.py` is removed;
its logic lives here.

---

## DB tables

LLM-related tables are prefixed `llmbroker_` to signal ownership and ease future
extraction of `llmbroker` as a standalone package:

| Table | Purpose |
|---|---|
| `llmbroker_providers` | LLM provider config (formerly `llm_providers`) |
| `llmbroker_call_log` | Per-call audit log (formerly `llm_call_log`) |

`llmbroker_providers` has a `default_rate_limit_sec INTEGER NOT NULL DEFAULT 60`
column — the fallback wait when the LLM API returns 429 without a `Retry-After`
header. Configurable per provider in `.deploy/llm_providers.toml` via `rate_limit_sec`.

---

## Classification adapter (`adapters/llm_client.py`)

`ProviderPool` is gone. `llm_client.py` keeps prompt-building and response-parsing;
two adapter functions call the broker:

```python
async def classify_receipt(
    broker: LLMBroker, items, store_name_raw, categories, tags,
    context_id: int | None = None,
) -> tuple[list[ClassificationResult], bool]: ...

async def get_chain_name(broker: LLMBroker, store_name_raw: str) -> str: ...
```

`get_chain_name` uses `broker.try_complete()` — returns the raw name immediately
if no provider is available rather than waiting.

`get_chain_name` is also used in `background/classification/store_resolver.py`
(`resolve_store`), which takes `broker: LLMBroker` instead of the former
`pool: ProviderPool` and calls `get_chain_name(broker, ...)` without a `conn`.

---

## Broker lifecycle (`main.py`)

```python
_broker = LLMBroker(DinaryStorage())

@asynccontextmanager
async def lifespan(app):
    await _broker.start()
    asyncio.create_task(receipt_classification_task(_broker))
    yield
    await _broker.stop()
```

One broker instance per process, started at lifespan and injected wherever LLM
access is needed.

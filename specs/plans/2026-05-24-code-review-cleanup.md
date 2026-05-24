# Code Review Cleanup — Receipt Pipeline & Review Page

Findings from the 2026-05-24 code review. Five phases ordered by risk and dependency:
bugs → dead code → duplicates → style → isolation.

Each phase is independently shippable and must pass `inv pre` + `pytest` before merge.

---

## Phase 1 — Bug Fix: fetchCounts field name mismatch

**Files:** `src/dinary/api/controllers/rules.py`, `tests/api/`

### Problem
`build_rules_counts()` returns `"doubtful_rules"` but the frontend's `fetchCounts()` reads
`data.doubtful_count`. The badge silently resets to 0 on every background poll.

### Change
- `rules.py`: rename the key `"doubtful_rules"` → `"doubtful_count"` in `build_rules_counts()`.
- Add/update a test asserting `GET /api/rules/counts` returns `doubtful_count` (not `doubtful_rules`).

---

## Phase 2 — Dead Code Removal

**Files:** `webapp/src/stores/review.js`, `src/dinary/api/controllers/rules.py`,
`src/dinary/db/storage.py`, `src/dinary/background/classification/task.py`,
`tests/conftest.py`, `tests/ledger/test_ledger_repo_catalog.py`

### 2a — `totalLoaded` and `fromCache` in review store
Both refs are tracked and persisted but never consumed by any template or component.
- Remove `totalLoaded` ref, its increment in `loadNextPage()`, its reset in `reset()` and
  `confirmAll()`, its cache slot in `_persistState()`, and its `return` entry.
- Remove `fromCache` ref, its assignments, and its `return` entry.
- Update `webapp/tests/store-review.test.js` to remove any assertions on these fields.

### 2b — `count_total()` dead call
`build_rules_feed()` calls `count_total(con)` and stores the result in `total`, but
`total` is only used when `doubtful_only=False` — a code path the frontend never invokes.
- Remove the `total = count_total(con)` call and the `total` variable from `build_rules_feed()`.
- Change `effective_total = d_total if doubtful_only else total` to just `effective_total = d_total`.
- `count_total()` itself becomes unused; delete it.

### 2c — `close_connection()` no-op in storage
The function body is `pass`. It exists only as a legacy test fixture hook.
- Delete `close_connection()` from `storage.py`.
- Remove the two call sites: `tests/conftest.py` and `tests/ledger/test_ledger_repo_catalog.py`.

### 2d — Redundant `has_expenses` guard in `_classify_and_persist()`
`_process_job()` already checks for existing expenses and returns early. The identical check
at the top of `_classify_and_persist()` (task.py, first block inside the function) is
unreachable in any non-concurrent scenario and is superseded by the authoritative check
inside the write transaction in `persist_classification_results()`.
- Remove the `SELECT 1 FROM expenses WHERE receipt_id = ?` block and the surrounding
  `BEGIN IMMEDIATE / complete_job / COMMIT / return` pattern from `_classify_and_persist()`.

---

## Phase 3 — Duplicate Elimination

**Files:** `src/dinary/api/controllers/rules.py`,
`src/dinary/api/controllers/expense_corrections.py`,
`src/dinary/background/classification/task.py`,
`src/dinary/background/classification/persist.py`

### 3a — `build_rules_counts()` duplicates `count_doubtful()` query
The inline `SELECT COUNT(DISTINCT cr.id) ...` in `build_rules_counts()` is the same JOIN as
`count_doubtful()`.
- Replace the inline query with a call to `count_doubtful(con)`.
- Delete the now-unused inline SQL.

### 3b — `_query_other_items()` dual branches
The function returns one of two identical queries differing only by a `created_at` filter.
- Collapse to a single query using `AND (? IS NULL OR rec.created_at >= ?)` with
  params `[name_norm, store_id, expense_id, since, since]`.
- Delete the if/else branch.

### 3c — `normalize_item_name` recomputed in persist
`_run_rules_pass()` already computes `norm` per item and stores it in `llm_queue` as
`(item_id, norm)`. `persist_classification_results()` recomputes it.
- Change `_run_rules_pass()` to return a third value: `dict[int, str]` mapping
  `item_id → normalized_name`.
- Update `_classify_and_persist()` to thread the norms dict through to
  `persist_classification_results()`.
- In `persist_classification_results()`, replace `normalize_item_name(item.name_raw)` with
  a lookup in the norms dict (falling back to `normalize_item_name` only if missing).
- Update the return type annotation of `_run_rules_pass()`.

---

## Phase 4 — Style & Consistency

**Files:** `src/dinary/background/classification/task.py`,
`src/dinary/background/classification/persist.py`,
`src/dinary/background/classification/store_resolver.py`,
`src/dinary/db/receipts.py`

### 4a — Replace manual transaction blocks with `transaction()` CM
The codebase has `storage.transaction(con)` (BEGIN IMMEDIATE / COMMIT / ROLLBACK) but the
background layer uses raw SQL for transactions. Replace the following:

| File | Location | Pattern to replace |
|------|----------|--------------------|
| `task.py` | `_process_job()` short `complete_job` block | `with transaction(conn):` |
| `persist.py` | `write_fetch_fallback_metadata()` | `with transaction(conn):` |
| `receipts.py` | `save_parsed_receipt()` | `with transaction(conn):` |
| `receipts.py` | `delete_receipt_cascade()` | `with transaction(conn):` |

Note: `persist_classification_results()` and `claim_next_job()` retain their manual patterns
because both have early-return paths that commit before a post-commit side-effect
(`sheet_logging.notify_new_work()` and the `return result` respectively) — the CM's
`yield`-then-`COMMIT` model doesn't fit these flows.

### 4b — Missing `sqlite3.Connection` type annotations
Add `conn: sqlite3.Connection` to:
- `_upsert_chain(conn, chain_name)` in `store_resolver.py`
- `_run_rules_pass(conn, items, store_id)` in `task.py`

---

## Phase 5 — LLMBroker Isolation

**Files:** `src/dinary/adapters/llmbroker.py`,
`src/dinary/adapters/llm_direct.py` (delete),
`src/dinary/background/classification/receipt_classifier.py`,
`src/dinary/adapters/llm_storage.py`,
`src/dinary/db/migrations/0004_receipt_pipeline.sql`,
`src/dinary/api/controllers/llm.py`,
`src/dinary/api/llm.py`,
`tasks/receipt.py`,
`tests/conftest.py`,
`tests/services/test_llm_direct.py` (delete),
`tests/services/test_llm_storage.py`,
`tests/services/test_receipt_classifier.py`,
`webapp/src/api/adminLlm.js`,
`webapp/src/stores/llm.js`,
`webapp/src/views/LLMView.vue`

### 5a — Rename `receipt_id` → `context_id` in `llmbroker_call_log`
The column name is a dinary-specific concern bleeding into what should be a generic broker
table. Migration 0004 has not been deployed; edit it in place.

- In `0004_receipt_pipeline.sql`: rename `receipt_id INTEGER REFERENCES receipts(id) ON DELETE SET NULL`
  to `context_id TEXT` in the `llmbroker_call_log` CREATE TABLE. Remove the FK constraint —
  the broker's `context_id` is typed `Any` and need not be an integer FK.
- In `llm_storage.py`: update `on_call_logged()` to write `context_id` instead of `receipt_id`.
- Run `inv migrate` locally to verify the schema applies cleanly after the edit.

### 5b — Delete `llm_direct.py` and its `OpenAICompatibleClient`

`OpenAICompatibleClient` was created as a direct-HTTP bypass for admin and CLI tasks but is
the wrong abstraction: it duplicates broker HTTP logic, `get_chain_name()` on it is dead code
(production uses `receipt_classifier.get_chain_name(broker, ...)`), and `test_provider`
admin endpoint built on it gave no advantage over the broker.

- Delete `src/dinary/adapters/llm_direct.py`.
- Delete `tests/services/test_llm_direct.py`.
- Update the module docstring in `receipt_classifier.py` to remove the reference to
  `OpenAICompatibleClient`.

### 5c — Remove `DINARY_LLM_*` env vars

Providers are configured via `.deploy/llm_providers.toml`. The env var settings were a legacy
fallback that predates the toml; they are now dead.

- `src/dinary/config.py`: delete the three fields `llm_base_url`, `llm_api_key`, `llm_model`
  and their comment.
- `src/dinary/adapters/llm_storage.py`: in `_seed()`, remove the entire env var fallback
  block (the `if not providers:` branch that reads from `settings`). If no toml or toml has
  no valid entries, `_seed()` simply logs a warning and returns without inserting anything.
  Remove the `from dinary.config import settings` import if it becomes unused.
- `CLAUDE.md`: remove the three `DINARY_LLM_*` rows from the configuration table.
- `.deploy.example/.env`: remove the three commented-out `DINARY_LLM_*` lines.
- `tests/services/test_llm_storage.py`: delete the test cases that monkeypatch
  `settings.llm_base_url / llm_api_key / llm_model` — they cover code being deleted.

### 5d — Rename `LLMBrokerStorage` → `SqliteLLMBrokerStorage`, add `TomlLLMBrokerStorage`, move `NullStorage` to tests

**Rename** `LLMBrokerStorage` → `SqliteLLMBrokerStorage` everywhere (production code + tests).

**Add `TomlLLMBrokerStorage`** to `llm_storage.py` — CLI/standalone path, no DB:
- `load_providers()` → delegates to `_providers_from_toml()`, returns `list[ProviderConfig]`
- `on_call_logged(event)` → `logger.info("provider=%s status=%s latency=%dms", ...)`
- `on_rate_limited(provider_id, until)` → `logger.warning("provider %s rate-limited until %s", ...)`

**Move `NullStorage` out of `llmbroker.py`** — it is test infrastructure, not production code.
- Delete `NullStorage` from `src/dinary/adapters/llmbroker.py`.
- Add it to `tests/conftest.py` so all test files can import it from there.
- Update the five test files that currently import it from `llmbroker`.

### 5e — Switch `tasks/receipt.py` to `LLMBroker(TomlLLMBrokerStorage())`

- Remove `from dinary.adapters.llm_direct import OpenAICompatibleClient` and all
  `settings.llm_base_url / llm_api_key / llm_model` references.
- Remove the manual env var validation block (`if not settings.llm_base_url ...`).
- Add imports:
  ```python
  from dinary.adapters.llmbroker import LLMBroker
  from dinary.adapters.llm_storage import TomlLLMBrokerStorage
  from dinary.background.classification.receipt_classifier import classify_receipt as llm_classify_receipt
  ```
- Replace the `llm = OpenAICompatibleClient(...)` block with:
  ```python
  broker = LLMBroker(TomlLLMBrokerStorage())
  await broker.start()
  ```
- Replace `asyncio.run(llm.classify_receipt(...))` with
  `results, _ = asyncio.run(llm_classify_receipt(broker, ...))`.
- Call `await broker.stop()` in a `finally` block after the classification loop.
- Update tests for `_run_receipt` accordingly.

### 5e — Remove `test_provider` endpoint and Test button

The per-provider test endpoint fires a synthetic `["хлеб"]` call bypassing the broker's
accounting. Provider health is better surfaced via the enriched call log (Phase 8).

- Delete `test_provider()` from `src/dinary/api/controllers/llm.py`.
- Remove the `POST /api/llm/providers/{provider_id}/test` route from `src/dinary/api/llm.py`.
- Remove `testProvider` from `webapp/src/api/adminLlm.js`.
- Remove `test()` action from `webapp/src/stores/llm.js`.
- Remove the Test button (`@test` handler) from `webapp/src/views/LLMView.vue`.

---

## Phase 6 — Remove Unnecessary `to_thread` in API Routes + Fix `notify_new_receipt` Thread-Safety

**Files:** `src/dinary/api/expenses.py`, `src/dinary/api/receipts.py`,
`src/dinary/api/expense_corrections.py`, `src/dinary/background/classification/task.py`

### 6a — Convert `async def` routes to `def`

All API route handlers are `async def` wrapping sync business logic via `asyncio.to_thread`.
FastAPI already runs `def` (sync) handlers in its thread pool — the `to_thread` layer adds a
pointless context switch with no benefit.

Convert the following routes from `async def` + `to_thread` to plain `def` with a direct call:

- `api/expenses.py`: `create_expense`, `get_expenses`, `patch_expense`, `delete_expense`
- `api/receipts.py`: `create_receipt`, `get_receipt`, `delete_receipt`
- `api/expense_corrections.py`: `correct_category`

Remove the `import asyncio` from each file once `to_thread` is gone.

### 6b — Fix `notify_new_receipt()` thread-safety bug

`notify_new_receipt()` calls `_wakeup_event.set()` directly. `asyncio.Event.set()` is not
thread-safe — calling it from a FastAPI thread-pool worker (which is what a `def` route runs
in) can race with the event loop.

`notify_new_work()` in `sheet_logging.py` already does this correctly with
`loop.call_soon_threadsafe(ev.set)`. Apply the same pattern to `notify_new_receipt()`:

- Store both the event and its owning loop at startup, mirroring `sheet_logging.py`'s
  `register_wake_channel` / `clear_wake_channel` pattern.
- Replace `_wakeup_event.set()` with `loop.call_soon_threadsafe(_wakeup_event.set)` plus a
  `RuntimeError` guard for the loop-closing race.

This fix is a prerequisite for 6a on the receipt route; the other routes (`expenses`,
`expense_corrections`) call `notify_new_work()` which is already thread-safe.

---

## Phase 7 — Wrap Sync DB-Heavy Paths in Classification Task

**Files:** `src/dinary/background/classification/task.py`,
`src/dinary/background/classification/persist.py`

### Problem

Every sync DB call in the classification drain loop runs directly on the asyncio event loop.
When SQLite's `BEGIN IMMEDIATE` cannot immediately acquire the write lock it waits up to
`busy_timeout` (5000 ms) — blocking the event loop and freezing all API requests for up to
5 seconds. The most likely triggers are:

- Two receipts processed in parallel by `asyncio.gather` both reaching `persist_classification_results()` simultaneously.
- An API write (POST /api/expenses, POST /api/receipts) racing with `persist_classification_results()`.

Sheet logging already handles this correctly with `asyncio.to_thread`; apply the same pattern.

### Changes

Extract each sync DB-heavy unit into a standalone sync helper and wrap the call with
`asyncio.to_thread`:

| Current call site | Extract to | Wrap with |
|---|---|---|
| `_claim_all_pending()` (already a sync def, called from async) | no change needed | `await asyncio.to_thread(_claim_all_pending)` in `_drain_all_pending` |
| `_save_parsed(receipt_id, parsed)` | already a sync def | `await asyncio.to_thread(_save_parsed, receipt_id, parsed)` in `_process_job` |
| sync connection block in `_process_job` (lines ~107–138) | extract to `_check_and_complete_if_done(receipt_id)` → `bool` | `await asyncio.to_thread(_check_and_complete_if_done, receipt_id)` |
| `persist_classification_results(conn, ...)` call in `_classify_and_persist` | extract to `_persist_sync(job, items, classifications, rule_hits, llm_results, store_id)` that opens its own connection | `await asyncio.to_thread(_persist_sync, ...)` |

`_release()` and `_poison()` are single-statement UPDATEs without `BEGIN IMMEDIATE` — wrap
them too for consistency: `await asyncio.to_thread(_release, ...)` / `await asyncio.to_thread(_poison, ...)`.

Each extracted helper must open and close its own connection internally (the existing
discipline: never hold a connection across an `await`). The connection parameter on
`persist_classification_results()` is removed; it opens its own connection.

---

## Phase 8 — Provider Error Diagnostics

**Files:** `src/dinary/adapters/llmbroker.py`,
`src/dinary/adapters/llm_storage.py`,
`src/dinary/db/migrations/0004_receipt_pipeline.sql`,
`src/dinary/api/controllers/llm.py`,
`webapp/src/components/ProviderCard.vue` (or equivalent)

### Problem

When a provider has a bad API key, an exhausted quota, or a billing issue, the call log
only stores `"error"` or `"429"` in `status`. The admin has no way to see *why* a provider
is failing without digging through server logs.

### Changes

**Schema** (`0004_receipt_pipeline.sql`):
- Add `error_detail TEXT` column to `llmbroker_call_log`. Stores the first 300 chars of the
  HTTP response body on non-2xx responses; NULL on success.

**Broker** (`llmbroker.py`):
- In `_call_provider()`, on `httpx.HTTPStatusError`, extract `exc.response.text[:300]` and
  pass it through to `_log_call()` as `error_detail`.
- Add `error_detail: str | None = None` to `CallEvent`.

**Storage** (`llm_storage.py`):
- In `on_call_logged()`, write `event.error_detail` to the new column.

**Status endpoint** (`controllers/llm.py`):
- In `llm_status()`, add a subquery for `last_error_detail`:
  ```sql
  (SELECT error_detail FROM llmbroker_call_log
    WHERE provider_id = p.id AND error_detail IS NOT NULL
    ORDER BY id DESC LIMIT 1) AS last_error_detail
  ```
- Include `last_error_detail` in each provider dict in the response.

**Frontend** (`ProviderCard.vue`):
- Show `last_error_detail` below the provider status when it is non-null (e.g. a muted
  red text line: "401 Incorrect API key provided").

---

## Phase 9 — Chain-Level Classification Rules

**Files:** `src/dinary/db/migrations/0004_receipt_pipeline.sql` (done — already edited),
`src/dinary/db/classification_rules.py`,
`src/dinary/background/classification/store_resolver.py`,
`src/dinary/background/classification/task.py`,
`src/dinary/background/classification/persist.py`,
`src/dinary/api/controllers/expense_corrections.py`,
`src/dinary/api/controllers/rules.py`,
`tests/services/test_receipt_classifier.py`

### Problem

`classification_rules.store_id` scopes rules to a physical store location. Every new
Lidl branch starts with no rules and wastes LLM calls re-learning what all other Lidl
branches already know. The chain concept exists in `shop_chains` and `stores.chain_id`
but was not wired into the rules engine.

### Change

Migration 0004 has been updated in place (not yet deployed):
- `store_id INTEGER REFERENCES stores(id)` → `chain_id INTEGER REFERENCES shop_chains(id)`
- Indexes renamed: `classification_rules_store_item` → `classification_rules_chain_item`,
  `idx_cr_store_name` → `idx_cr_chain_name`.

Remaining code changes:

**`db/classification_rules.py`**
- `classify_by_rules(conn, store_id, ...)` → `classify_by_rules(conn, chain_id, ...)`
  — update the `WHERE (store_id = ? OR store_id IS NULL)` query to use `chain_id`.
- `create_or_update_rule(conn, store_id, ...)` → `create_or_update_rule(conn, chain_id, ...)`
  — update all queries referencing `store_id` to `chain_id`.

**`background/classification/store_resolver.py`**
- `resolve_store()` currently returns `int` (store_id). Change to return `tuple[int, int]`
  — `(store_id, chain_id)` — so callers don't need a second DB round-trip.
- The `chain_id` is already available in the upsert block after `_upsert_chain()`.

**`background/classification/task.py`**
- Thread `chain_id` from `resolve_store()` through `_process_job()` to `_classify_and_persist()`.
- Pass `chain_id` (not `store_id`) to `_run_rules_pass()` and on to `classify_by_rules()`.

**`background/classification/persist.py`**
- `persist_classification_results(conn, job, ..., store_id)` → replace `store_id` parameter
  with `chain_id` and pass it to `create_or_update_rule()`.

**`api/controllers/expense_corrections.py`**
- Before calling `create_or_update_rule()`, look up `chain_id`:
  ```sql
  SELECT s.chain_id FROM expenses e JOIN stores s ON s.id = e.store_id WHERE e.id = ?
  ```
- Pass `chain_id` (not `store_id`) to `create_or_update_rule()`.

**`api/controllers/rules.py`**
- `build_rules_feed()`: join to `shop_chains` via `cr.chain_id` directly (drop the
  intermediate `stores s` join for the chain name). Update the `WHERE` filter from
  `rec.store_id = cr.store_id` to `s.chain_id = cr.chain_id`.
- `build_rules_counts()` / `count_doubtful()`: update the same join.
- Return field name stays `"store"` (it is the chain name in both old and new code).

**Tests**
- Update any fixture or assertion that passes `store_id` to `classify_by_rules` or
  `create_or_update_rule` to pass `chain_id` instead.
- Add a test: two stores with the same `chain_id` share a rule — classifying an item at
  store A creates a rule, classifying the same item at store B hits that rule.

---

## Done Gate (all phases)

Each phase ships independently. Before marking a phase complete:

```
uv run inv pre    # ruff + pyrefly + hooks → "All checks passed!" + 0 errors
uv run pytest     # N passed, 0 failures
cd webapp && npm test  # vitest green
```

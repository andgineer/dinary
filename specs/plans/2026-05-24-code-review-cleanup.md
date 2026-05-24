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

**Files:** `src/dinary/adapters/llmbroker.py` (no changes — already clean),
`src/dinary/adapters/llm_direct.py` (new),
`src/dinary/background/classification/receipt_classifier.py`,
`src/dinary/adapters/llm_storage.py`,
`src/dinary/db/migrations/0004_receipt_pipeline.sql`,
`src/dinary/api/controllers/llm.py`,
`tasks/receipt.py`,
`tests/services/test_receipt_classifier.py`

### 5a — Rename `receipt_id` → `context_id` in `llmbroker_call_log`
The column name is a dinary-specific concern bleeding into what should be a generic broker
table. Migration 0004 has not been deployed; edit it in place.

- In `0004_receipt_pipeline.sql`: rename `receipt_id INTEGER REFERENCES receipts(id) ON DELETE SET NULL`
  to `context_id TEXT` in the `llmbroker_call_log` CREATE TABLE. Remove the FK constraint —
  the broker's `context_id` is typed `Any` and need not be an integer FK.
- In `llm_storage.py`: update `on_call_logged()` to write `context_id` instead of `receipt_id`.
- Run `inv migrate` locally to verify the schema applies cleanly after the edit.

### 5b — Move `OpenAICompatibleClient` to `adapters/llm_direct.py`
`OpenAICompatibleClient` lives in `receipt_classifier.py` but has no connection to receipt
classification — it is a general-purpose direct-HTTP bypass used by admin and CLI tasks.

- Create `src/dinary/adapters/llm_direct.py` containing `OpenAICompatibleClient`.
- The class can delegate to the shared helpers in `receipt_classifier.py`
  (`_build_user_message`, `_parse_response`, `_SYSTEM_PROMPT`, `_CHAIN_NAME_PROMPT`) to avoid
  duplicating logic; import them from `receipt_classifier`.
- Remove `OpenAICompatibleClient` from `receipt_classifier.py`.
- Update importers:
  - `src/dinary/api/controllers/llm.py`: `from dinary.adapters.llm_direct import OpenAICompatibleClient`
  - `tasks/receipt.py`: same
- Move `TestOpenAICompatibleClient` from `tests/services/test_receipt_classifier.py` to a new
  `tests/services/test_llm_direct.py`.

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

## Done Gate (all phases)

Each phase ships independently. Before marking a phase complete:

```
uv run inv pre    # ruff + pyrefly + hooks → "All checks passed!" + 0 errors
uv run pytest     # N passed, 0 failures
cd webapp && npm test  # vitest green
```

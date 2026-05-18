# Services Refactor — Implementation Plan

Dissolve the flat `src/dinary/services/` package into purpose-named locations.
Reorganize `background/`, dissolve `imports/`, `reports/`, `tools/` into root `tasks/`.

## Target structure

```
src/dinary/
  api/
    expenses.py             thin router: /api/expenses
    expense_corrections.py  thin router: /api/expense-corrections/*
    catalog.py              thin router: /api/catalog + /api/catalog/* (merges admin_catalog.py)
    llm.py                  thin router: /api/llm/* (was admin_llm.py, drops /admin/ prefix)
    currencies.py           thin router: /api/currencies (imports from db/currencies.py)
    qr.py                   thin router: /api/qr/parse
    receipts.py             thin router: /api/receipts
    rules.py                thin router: /api/rules/* (was receipt_review.py, /receipts/review → /rules)

    expenses/               handlers.py, corrections.py
    catalog/                writer.py, writer_categories.py, writer_events.py,
                            writer_groups.py, writer_errors.py
    llm/                    handlers.py
    qr/                     qr_parser.py
    rules/                  handlers.py

  taxonomy/                 shared classification — used by api/rules/ and background/classification/
    classification_rules.py (was services/classification_rules.py)

  background/
    classification/         task.py, item_normalizer.py, store_resolver.py
    rate_prefetch/          task.py
    sheet_logging/          task.py, sheet_logging.py, logging_jobs.py, sheets_write.py

  adapters/                 external service clients — no business logic, flat
    sheets_client.py        (was services/sheets_client.py)
    exchange_rates.py       (was services/exchange_rates.py)
    rate_helpers.py         (was services/rate_helpers.py)
    nbp.py                  (was services/nbp.py)
    nbs.py                  (was services/nbs.py)
    serbian_receipt_parser.py (was services/receipt_parser.py)
    llm_client.py           (was services/llm_client.py)
    llm_bootstrap.py        (was services/llm_bootstrap.py)

  sheets/                   Google Sheets domain objects shared across api/, background/, imports/
    sheet_mapping.py        (was services/sheet_mapping.py)
    sheets.py               (was services/sheets.py)

  db/
    storage.py              (absorbs sqlite_types.py — adapters + connect() merged in)
    db_migrations.py, sql_loader.py
    expenses.py             (was services/expenses.py)
    currencies.py           (was services/currencies.py)
    receipts.py             (was services/receipts.py)
    catalog.py              (was services/catalog.py)
    migrations/             (was top-level migrations/)
    sql/                    (was top-level sql/)

tasks/                      root invoke tasks — subfolders added:
  imports/                  (was src/dinary/imports/)
  reports/                  (was src/dinary/reports/ + tools/report_helpers.py)
  backup/                   (was tools/backup_snapshots.py + tools/backup_retention.py)
  sql.py                    (was tools/sql.py — used by tasks/db.py and tasks/reports.py)
  seed_config.py            (was services/seed_config.py — catalog taxonomy data consumed primarily by tasks/imports/*)
```

**Import direction rule:** `src/dinary/` never imports from root `tasks/`. Only `tasks/` imports from `src/dinary/`.

Dissolved entirely: `src/dinary/services/`, `src/dinary/imports/`, `src/dinary/reports/`, `src/dinary/tools/`.

### Placement rationale for non-obvious files

| File | Location | Why |
|---|---|---|
| `receipt_parser.py` | `adapters/serbian_receipt_parser.py` | Serbian fiscal receipt parser — external-facing adapter; `db/receipts.py` imports `ParsedReceipt` type from it so it must live in a layer reachable by both `api/receipts.py` and `background/classification/` |
| `item_normalizer.py` | `background/classification/` | Only called by classification task + `tasks/receipt.py` |
| `store_resolver.py` | `background/classification/` | Only called by classification task |
| `sheet_mapping.py` | `sheets/` | Cross-cutting Sheets domain object used by `api/catalog`, `api/expenses`, `imports/`, and `background/sheet_logging/` — too broad for any single feature subfolder |
| `sheets_client.py` | `adapters/` | Generic Google API client used by imports, sheet_logging, sheet_mapping — not specific to any feature |
| `sheets.py` | `sheets/` | Google Sheets row utilities; sits alongside `sheet_mapping.py` in the shared `sheets/` domain package |
| `sheets_write.py` | `background/sheet_logging/` | Only used by `sheet_logging.py` — expense-row-specific, not a generic adapter |
| `exchange_rates.py` | `adapters/` | Shared by api/expenses, background/rate_prefetch, imports, sheet_logging |
| `nbp.py`, `nbs.py` | `adapters/` | External bank API clients, only called by exchange_rates.py |
| `llm_client.py` | `adapters/` | Consumed by `background/classification/task.py`, `background/classification/store_resolver.py`, and `tasks/receipt.py` — not API-layer-only |
| `llm_bootstrap.py` | `adapters/` | Imported by `db/storage.py` on startup — must sit at or below the db/ layer |
| `classification_rules.py` | `taxonomy/` | Used by both `background/classification/task.py` and `api/expense_corrections.py` — shared domain logic, not API-layer |
| `expenses.py` | `db/` | Core expense repository used by `background/classification/`, `background/sheet_logging/`, and `imports/` — cannot live in `api/` |
| `currencies.py` | `db/` | Seeded by `db/storage.py` on first boot — must sit at or below the db/ layer |
| `receipts.py` | `db/` | Receipt repository consumed by `background/classification/task.py` — cannot live in `api/` |
| `catalog.py` | `db/` | Catalog repository used by `background/sheet_logging/` and `imports/` — cannot live in `api/` |
| `seed_config.py` | `tasks/` | Catalog taxonomy data — primary callers are `tasks/imports/seed.py`, `tasks/imports/seed_derivation.py`, `tasks/imports/expense_import.py`, `tasks/deploy.py`, `tasks/dev.py`; not an API handler |
| `is_sheet_logging_enabled` | `config.py` | Config property — `bool(settings.sheet_logging_spreadsheet)`, not a service function |
| `notify_new_work` | `background/sheet_logging/` | Task wake-up signal; api/expenses imports it from there |

---

## Phase 1 — Create `db/` package

**Move files:**
- `services/storage.py` → `db/storage.py`
- `services/db_migrations.py` → `db/db_migrations.py`
- `services/sql_loader.py` → `db/sql_loader.py`
- `services/expenses.py` → `db/expenses.py`
- `services/currencies.py` → `db/currencies.py`
- `services/receipts.py` → `db/receipts.py`
- `services/catalog.py` → `db/catalog.py`
- `migrations/` → `db/migrations/`
- `sql/` → `db/sql/`

**Merge `sqlite_types.py` into `db/storage.py`:**
- Move type adapter/converter registrations (Decimal, date, datetime, boolean) into `storage.py`
- Move `connect()` into `storage.py`; replace the two `_sqlite_types.connect(...)` call sites with direct calls
- Rewrite `connect()` docstring: list PRAGMAs applied, note that `read_only=True` requires the file to exist, note that converters don't fire for aggregates (`SUM(amount)` returns str — coerce explicitly). Drop per-PRAGMA justification paragraphs.
- Delete `sqlite_types.py`

**Update imports:**
- `from dinary.services.storage import` → `from dinary.db.storage import`
- `from dinary.services.db_migrations import` → `from dinary.db.db_migrations import`
- `from dinary.services.sql_loader import` → `from dinary.db.sql_loader import`
- `from dinary.services.sqlite_types import` → remove (absorbed into storage)
- `from dinary.tools import sqlite_types` (in `tools/sql.py`) → remove
- `sql_loader.py` loads `.sql` files from `dinary.sql` package — update to load from `dinary.db.sql`
- `from dinary.services.expenses import` → `from dinary.db.expenses import`
- `from dinary.services import expenses` → `from dinary.db import expenses`
- `from dinary.services.currencies import` → `from dinary.db.currencies import`
- `from dinary.services import currencies` → `from dinary.db import currencies`
- `from dinary.services.receipts import` → `from dinary.db.receipts import`
- `from dinary.services.catalog import` → `from dinary.db.catalog import`
- `from dinary.services import catalog` → `from dinary.db import catalog`

**Run:** `inv pre`

---

## Phase 2 — Create `adapters/` package

**Move files (flat, no subfolders):**
- `services/sheets_client.py` → `adapters/sheets_client.py`
- `services/exchange_rates.py` → `adapters/exchange_rates.py`
- `services/rate_helpers.py` → `adapters/rate_helpers.py`
- `services/nbp.py` → `adapters/nbp.py`
- `services/nbs.py` → `adapters/nbs.py`
- `services/receipt_parser.py` → `adapters/serbian_receipt_parser.py`
- `services/llm_client.py` → `adapters/llm_client.py`
- `services/llm_bootstrap.py` → `adapters/llm_bootstrap.py`

**Update imports:**
- `from dinary.services.sheets_client import` → `from dinary.adapters.sheets_client import`
- `from dinary.services.exchange_rates import` → `from dinary.adapters.exchange_rates import`
- `from dinary.services.rate_helpers import` → `from dinary.adapters.rate_helpers import`
- `from dinary.services.nbp import` → `from dinary.adapters.nbp import`
- `from dinary.services.nbs import` → `from dinary.adapters.nbs import`
- `from dinary.services.receipt_parser import` → `from dinary.adapters.serbian_receipt_parser import`
- `from dinary.services.llm_client import` → `from dinary.adapters.llm_client import`
- `from dinary.services.llm_bootstrap import` → `from dinary.adapters.llm_bootstrap import`

**Run:** `inv pre`

---

## Phase 3 — Create `api/` controller subfolders, `sheets/`, and `taxonomy/`

**Create `sheets/` package:**
- `services/sheet_mapping.py` → `sheets/sheet_mapping.py`
- `services/sheets.py` → `sheets/sheets.py`

**Update imports:**
- `from dinary.services.sheet_mapping import` → `from dinary.sheets.sheet_mapping import`
- `from dinary.services import sheet_mapping` → `from dinary.sheets import sheet_mapping`
- `from dinary.services.sheets import` → `from dinary.sheets.sheets import`
- `from dinary.services import sheets` → `from dinary.sheets import sheets`

**Create `taxonomy/` package:**
- `services/classification_rules.py` → `taxonomy/classification_rules.py`

**Update imports:**
- `from dinary.services.classification_rules import` → `from dinary.taxonomy.classification_rules import`

**Create `api/catalog/`:**
- `services/catalog_writer.py` → `api/catalog/writer.py`
- `services/catalog_writer_categories.py` → `api/catalog/writer_categories.py`
- `services/catalog_writer_events.py` → `api/catalog/writer_events.py`
- `services/catalog_writer_groups.py` → `api/catalog/writer_groups.py`
- `services/catalog_writer_errors.py` → `api/catalog/writer_errors.py`
- (`catalog.py` already moved to `db/` in Phase 1; `seed_config.py` moves to `tasks/` in Phase 6)

**Create `api/rules/`:**
- Extract feed/counts query logic from `api/receipt_review.py` → `api/rules/handlers.py`
- (`classification_rules.py` already moved to `taxonomy/` above)

**Create `api/llm/`:**
- (`llm_client.py` and `llm_bootstrap.py` already moved to `adapters/` in Phase 2; subpackage created here for Phase 4 handlers)

**Create `api/qr/`:**
- `services/qr_parser.py` → `api/qr/qr_parser.py`

**Create `api/expenses/`:**
- (`expenses.py` already moved to `db/` in Phase 1; subpackage created here for Phase 4 handler extraction)

**`api/currencies.py`** — update import to use `dinary.db.currencies` (moved to `db/` in Phase 1; no subfolder needed, no inlining).

**Expose `is_sheet_logging_enabled` via config:**
- Add `sheet_logging_enabled: bool` computed property to `config.py` settings (`return bool(self.sheet_logging_spreadsheet)`)
- Replace all `is_sheet_logging_enabled()` call sites with `settings.sheet_logging_enabled`
- Remove the function from `services/sheet_logging.py`

**Update imports** across all callers of the above modules.

**Run:** `inv pre`

---

## Phase 4 — Refactor `api/` routers to thin files + drop `/admin/` prefix

**`api/expenses.py`** — extract handler logic:
- `api/expenses/handlers.py` ← `_create_expense_sync`, `_is_replay`, `_resolve_category_for_write`, `_validate_event`, `_validate_tags`
- `api/expenses.py` becomes thin: Pydantic schemas + `@router` decorators calling `handlers.*`

**`api/expense_corrections.py`** — extract handler logic:
- `api/expenses/corrections.py` ← handler functions
- `api/expense_corrections.py` becomes thin router

**`api/catalog.py` + `api/admin_catalog.py`** — merge into single thin `api/catalog.py`:
- `api/catalog/` already has all the logic from Phase 3
- Drop `/api/admin/catalog` prefix → endpoints become `/api/catalog/*`
- Delete `api/admin_catalog.py`

**`api/admin_llm.py`** → rename to `api/llm.py`:
- `api/llm/` already created in Phase 3; `llm_client.py` and `llm_bootstrap.py` are in `adapters/` from Phase 2
- Move handler functions to `api/llm/handlers.py`
- Drop `/api/admin/llm-providers` prefix → endpoints become `/api/llm/*`
- Delete `api/admin_llm.py`

**`api/receipt_review.py`** → rename to `api/rules.py`:
- handler logic already in `api/rules/handlers.py` from Phase 3
- endpoint paths: `/api/receipts/review/*` → `/api/rules/*`
- delete `api/receipt_review.py`

**Update `main.py`** — router registrations for new file locations and removed `admin_*` files.

**Update any client code / specs** referencing `/api/admin/*` or `/api/receipts/review/*` URLs.

**Run:** `inv pre`

---

## Phase 5 — Reorganize `background/`

**Create `background/classification/`:**
- `background/receipt_classification_task.py` → `background/classification/task.py`
- `services/item_normalizer.py` → `background/classification/item_normalizer.py`
- `services/store_resolver.py` → `background/classification/store_resolver.py`
- (receipt_parser already in `adapters/serbian_receipt_parser.py` from Phase 2)

**Create `background/rate_prefetch/`:**
- `background/rate_prefetch_task.py` → `background/rate_prefetch/task.py`
- (exchange_rates and bank clients already in `adapters/` from Phase 2)

**Create `background/sheet_logging/`:**
- `background/sheet_logging_task.py` → `background/sheet_logging/task.py`
- `services/sheet_logging.py` → `background/sheet_logging/sheet_logging.py`
- `services/logging_jobs.py` → `background/sheet_logging/logging_jobs.py`
- `services/sheets_write.py` → `background/sheet_logging/sheets_write.py`
- (sheets_client already in `adapters/` from Phase 2; sheets.py already in `sheets/` from Phase 3)

**Update imports** across all callers. `services/` should now be empty — delete it.

**Run:** `inv pre`

---

## Phase 6 — Move `imports/`, `reports/`, `tools/` to root `tasks/`

**Move:**
- `src/dinary/imports/` → `tasks/imports/` (all files, as-is)
- `src/dinary/reports/` → `tasks/reports/` (all files)
- `src/dinary/tools/report_helpers.py` → `tasks/reports/report_helpers.py`
- `src/dinary/tools/backup_snapshots.py` → `tasks/backup/backup_snapshots.py`
- `src/dinary/tools/backup_retention.py` → `tasks/backup/backup_retention.py`
- `src/dinary/tools/sql.py` → `tasks/sql.py`
- `services/seed_config.py` → `tasks/seed_config.py`

**Update hardcoded path in `tasks/backups_replica.py`** (line 316):
- `Path(__file__).parent.parent / "src/dinary/tools/backup_retention.py"` → `Path(__file__).parent / "backup/backup_retention.py"`

**Update SSH invocations** in `tasks/imports.py` and `tasks/reports.py`:
- `from dinary.imports.*` → `from tasks.imports.*`
- `from dinary.reports.*` → `from tasks.reports.*`
- `from dinary.tools.sql import` → `from tasks.sql import`
- `from dinary.tools.report_helpers import` → `from tasks.reports.report_helpers import`
- `from dinary.services.seed_config import` → `from tasks.seed_config import`
- Remote `python -c '...'` strings referencing `dinary.imports.*` / `dinary.reports.*` → `tasks.imports.*` / `tasks.reports.*`
- `remote_snapshot_cmd(f"dinary.reports.{module}", ...)` → `remote_snapshot_cmd(f"tasks.reports.{module}", ...)`

**Delete** `src/dinary/imports/`, `src/dinary/reports/`, `src/dinary/tools/`.

**Run:** `inv pre` + full test suite

---

## Phase 7 — Cleanup and verify

- Confirm `src/dinary/services/` is empty and delete it
- Grep for any remaining `dinary.services`, `dinary.imports`, `dinary.reports`, `dinary.tools` imports and fix stragglers
- Grep for `/api/admin/` and `/api/receipts/review` in specs, docs, frontend — update hardcoded URLs
- Run full test suite
- Update `specs/architecture/` if it references the old structure

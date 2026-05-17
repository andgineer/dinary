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
    currencies.py           thin router: /api/currencies (logic stays inline, file is small)
    qr.py                   thin router: /api/qr/parse
    receipts.py             thin router: /api/receipts
    rules.py                thin router: /api/rules/* (was receipt_review.py, /receipts/review → /rules)

    expenses/               expenses.py, handlers.py, corrections.py
    catalog/                catalog.py, writer.py, writer_categories.py, writer_events.py,
                            writer_groups.py, writer_errors.py, seed_config.py, sheet_mapping.py
    llm/                    llm_client.py, llm_bootstrap.py, handlers.py
    qr/                     qr_parser.py
    receipts/               receipts.py
    rules/                  classification_rules.py, handlers.py

  background/
    classification/         task.py, receipt_parser.py, item_normalizer.py, store_resolver.py
    rate_prefetch/          task.py
    sheet_logging/          task.py, sheet_logging.py, logging_jobs.py, sheets_write.py

  adapters/                 external service clients — no business logic, flat
    sheets_client.py        (was services/sheets_client.py)
    sheets.py               (was services/sheets.py)
    exchange_rates.py       (was services/exchange_rates.py)
    rate_helpers.py         (was services/rate_helpers.py)
    nbp.py                  (was services/nbp.py)
    nbs.py                  (was services/nbs.py)

  db/
    storage.py              (absorbs sqlite_types.py — adapters + connect() merged in)
    db_migrations.py, sql_loader.py
    migrations/             (was top-level migrations/)
    sql/                    (was top-level sql/)

tasks/                      root invoke tasks — subfolders added:
  imports/                  (was src/dinary/imports/)
  reports/                  (was src/dinary/reports/ + tools/report_helpers.py)
  backup/                   (was tools/backup_snapshots.py)
  sql.py                    (was tools/sql.py — used by tasks/db.py and tasks/reports.py)
```

**Import direction rule:** `src/dinary/` never imports from root `tasks/`. Only `tasks/` imports from `src/dinary/`.

Dissolved entirely: `src/dinary/services/`, `src/dinary/imports/`, `src/dinary/reports/`, `src/dinary/tools/`.

### Placement rationale for non-obvious files

| File | Location | Why |
|---|---|---|
| `receipt_parser.py` | `background/classification/` | Called by classification task + `tasks/receipt.py`, never by the receipts API (which only saves the URL) |
| `item_normalizer.py` | `background/classification/` | Same — only called by classification task + `tasks/receipt.py` |
| `store_resolver.py` | `background/classification/` | Only called by classification task |
| `sheet_mapping.py` | `api/catalog/` | 3D→2D map tab is a catalog concept; primary callers are `api/catalog`, `api/expenses`, imports |
| `sheets_client.py` | `adapters/sheets/` | Generic Google API client used by imports, sheet_logging, sheet_mapping — not specific to any feature |
| `sheets.py` | `adapters/sheets/` | Generic row utilities used by imports + sheet_logging |
| `sheets_write.py` | `background/sheet_logging/` | Only used by `sheet_logging.py` — expense-row-specific, not a generic adapter |
| `exchange_rates.py` | `adapters/rates/` | Shared by api/expenses, background/rate_prefetch, imports, sheet_logging |
| `nbp.py`, `nbs.py` | `adapters/rates/` | External bank API clients, only called by exchange_rates.py |
| `seed_config.py` | `api/catalog/` | Catalog taxonomy data; callers are in tasks/ which may import from src/dinary/ |
| `is_sheet_logging_enabled` | `config.py` | Config property — `bool(settings.sheet_logging_spreadsheet)`, not a service function |
| `notify_new_work` | `background/sheet_logging/` | Task wake-up signal; api/expenses imports it from there |

---

## Phase 1 — Create `db/` package

**Move files:**
- `services/storage.py` → `db/storage.py`
- `services/db_migrations.py` → `db/db_migrations.py`
- `services/sql_loader.py` → `db/sql_loader.py`
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

**Run:** `inv pre`

---

## Phase 2 — Create `adapters/` package

**Move files (flat, no subfolders):**
- `services/sheets_client.py` → `adapters/sheets_client.py`
- `services/sheets.py` → `adapters/sheets.py`
- `services/exchange_rates.py` → `adapters/exchange_rates.py`
- `services/rate_helpers.py` → `adapters/rate_helpers.py`
- `services/nbp.py` → `adapters/nbp.py`
- `services/nbs.py` → `adapters/nbs.py`

**Update imports:**
- `from dinary.services.sheets_client import` → `from dinary.adapters.sheets_client import`
- `from dinary.services.sheets import` → `from dinary.adapters.sheets import`
- `from dinary.services.exchange_rates import` → `from dinary.adapters.exchange_rates import`
- `from dinary.services.rate_helpers import` → `from dinary.adapters.rate_helpers import`
- `from dinary.services.nbp import` → `from dinary.adapters.nbp import`
- `from dinary.services.nbs import` → `from dinary.adapters.nbs import`

**Run:** `inv pre`

---

## Phase 3 — Create `api/` controller subfolders

**Create `api/catalog/`:**
- `services/catalog.py` → `api/catalog/catalog.py`
- `services/catalog_writer.py` → `api/catalog/writer.py`
- `services/catalog_writer_categories.py` → `api/catalog/writer_categories.py`
- `services/catalog_writer_events.py` → `api/catalog/writer_events.py`
- `services/catalog_writer_groups.py` → `api/catalog/writer_groups.py`
- `services/catalog_writer_errors.py` → `api/catalog/writer_errors.py`
- `services/seed_config.py` → `api/catalog/seed_config.py`
- `services/sheet_mapping.py` → `api/catalog/sheet_mapping.py`

**Create `api/rules/`:**
- `services/classification_rules.py` → `api/rules/classification_rules.py`
- Extract feed/counts query logic from `api/receipt_review.py` → `api/rules/handlers.py`

**Create `api/llm/`:**
- `services/llm_client.py` → `api/llm/llm_client.py`
- `services/llm_bootstrap.py` → `api/llm/llm_bootstrap.py`

**Create `api/qr/`:**
- `services/qr_parser.py` → `api/qr/qr_parser.py`

**Create `api/receipts/`:**
- `services/receipts.py` → `api/receipts/receipts.py`

**Create `api/expenses/`:**
- `services/expenses.py` → `api/expenses/expenses.py`

**Inline `services/currencies.py`** into `api/currencies.py` (only caller; no subfolder needed).

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
- `api/llm/` already has client + bootstrap from Phase 3
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
- `services/receipt_parser.py` → `background/classification/receipt_parser.py`
- `services/item_normalizer.py` → `background/classification/item_normalizer.py`
- `services/store_resolver.py` → `background/classification/store_resolver.py`

**Create `background/rate_prefetch/`:**
- `background/rate_prefetch_task.py` → `background/rate_prefetch/task.py`
- (exchange_rates and bank clients already in `adapters/rates/` from Phase 2)

**Create `background/sheet_logging/`:**
- `background/sheet_logging_task.py` → `background/sheet_logging/task.py`
- `services/sheet_logging.py` → `background/sheet_logging/sheet_logging.py`
- `services/logging_jobs.py` → `background/sheet_logging/logging_jobs.py`
- `services/sheets_write.py` → `background/sheet_logging/sheets_write.py`
- (sheets_client and sheets already in `adapters/sheets/` from Phase 2)

**Update imports** across all callers. `services/` should now be empty — delete it.

**Run:** `inv pre`

---

## Phase 6 — Move `imports/`, `reports/`, `tools/` to root `tasks/`

**Move:**
- `src/dinary/imports/` → `tasks/imports/` (all files, as-is)
- `src/dinary/reports/` → `tasks/reports/` (all files)
- `src/dinary/tools/report_helpers.py` → `tasks/reports/report_helpers.py`
- `src/dinary/tools/backup_snapshots.py` → `tasks/backup/backup_snapshots.py`
- `src/dinary/tools/sql.py` → `tasks/sql.py`

**Update SSH invocations** in `tasks/imports.py` and `tasks/reports.py`:
- `from dinary.imports.*` → `from tasks.imports.*`
- `from dinary.reports.*` → `from tasks.reports.*`
- `from dinary.tools.sql import` → `from tasks.sql import`
- `from dinary.tools.report_helpers import` → `from tasks.reports.report_helpers import`
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

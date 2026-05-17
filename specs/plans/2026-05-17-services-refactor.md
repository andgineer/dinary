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
                            writer_groups.py, writer_errors.py, seed_config.py
    llm/                    llm_client.py, llm_bootstrap.py
    qr/                     qr_parser.py
    receipts/               receipts.py, receipt_parser.py, item_normalizer.py, store_resolver.py
    rules/                  classification_rules.py, handlers.py

  background/
    classification/         task.py
    rate_prefetch/          task.py, exchange_rates.py, rate_helpers.py, nbp.py, nbs.py
    sheet_logging/          task.py, sheet_logging.py, logging_jobs.py,
                            sheets_client.py, sheets.py, sheets_write.py, sheet_mapping.py

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

Dissolved entirely: `src/dinary/services/`, `src/dinary/imports/`, `src/dinary/reports/`, `src/dinary/tools/`.

---

## Phase 1 — Create `db/` package

**Move files:**
- `services/storage.py` → `db/storage.py`
- `services/db_migrations.py` → `db/db_migrations.py`
- `services/sql_loader.py` → `db/sql_loader.py`
- `services/sqlite_types.py` → `db/sqlite_types.py`
- `tools/sql.py` → `db/sql.py`
- `migrations/` → `db/migrations/`
- `sql/` → `db/sql/`

**Merge `sqlite_types.py` into `storage.py`:**
- Move type adapter/converter registrations (Decimal, date, datetime, boolean) into `storage.py`
- Move `connect()` into `storage.py`; replace the two `_sqlite_types.connect(...)` call sites with direct `connect(...)` calls
- Rewrite `connect()` docstring: keep what PRAGMAs are applied, the `read_only` caveat (file must exist), and the aggregate coercion note (`SUM(amount)` bypasses converters — callers must coerce explicitly). Drop the per-PRAGMA justification paragraphs.
- Delete `sqlite_types.py`

**Update imports** — callers of the above (across `api/`, `background/`, `services/`, `imports/`, `reports/`, `tasks/`, `main.py`):
- `from dinary.services.storage import` → `from dinary.db.storage import`
- `from dinary.services.db_migrations import` → `from dinary.db.db_migrations import`
- `from dinary.services.sql_loader import` → `from dinary.db.sql_loader import`
- `from dinary.services.sqlite_types import` → remove (no longer a separate module)
- `sql_loader.py` loads `.sql` files from `dinary.sql` package — update to load from `dinary.db.sql`

**Run:** `inv pre`

---

## Phase 2 — Create `api/` controller subfolders

**Create `api/catalog/`:**
- `services/catalog.py` → `api/catalog/catalog.py`
- `services/catalog_writer.py` → `api/catalog/writer.py`
- `services/catalog_writer_categories.py` → `api/catalog/writer_categories.py`
- `services/catalog_writer_events.py` → `api/catalog/writer_events.py`
- `services/catalog_writer_groups.py` → `api/catalog/writer_groups.py`
- `services/catalog_writer_errors.py` → `api/catalog/writer_errors.py`
- `services/seed_config.py` → `api/catalog/seed_config.py`

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
- `services/receipt_parser.py` → `api/receipts/receipt_parser.py`
- `services/item_normalizer.py` → `api/receipts/item_normalizer.py`
- `services/store_resolver.py` → `api/receipts/store_resolver.py`

**Create `api/expenses/`:**
- `services/expenses.py` → `api/expenses/expenses.py`

**Inline `services/currencies.py`** into the existing `api/currencies.py` (file is small enough; no subfolder needed).

**Update imports** across all callers of the above modules.

**Run:** `inv pre`

---

## Phase 3 — Refactor `api/` routers to thin files + drop `/admin/` prefix

**`api/expenses.py`** — extract handler logic:
- `api/expenses/handlers.py` ← `_create_expense_sync`, `_is_replay`, `_resolve_category_for_write`, `_validate_event`, `_validate_tags`
- `api/expenses.py` becomes thin: Pydantic schemas + `@router` decorators calling `handlers.*`

**`api/expense_corrections.py`** — extract handler logic:
- `api/expenses/corrections.py` ← handler functions
- `api/expense_corrections.py` becomes thin router

**`api/catalog.py` + `api/admin_catalog.py`** — merge into single thin `api/catalog.py`:
- `api/catalog/` already has all the logic from Phase 2
- Drop `/api/admin/catalog` prefix → endpoints become `/api/catalog/*`
- Delete `api/admin_catalog.py`

**`api/admin_llm.py`** → rename to `api/llm.py`:
- `api/llm/` already has client + bootstrap from Phase 2
- Move handler functions to `api/llm/handlers.py`
- Drop `/api/admin/llm-providers` prefix → endpoints become `/api/llm/*`
- Delete `api/admin_llm.py`

**`api/receipt_review.py`** → rename to `api/rules.py`:
- handler logic already in `api/rules/handlers.py` from Phase 2
- endpoint paths: `/api/receipts/review/*` → `/api/rules/*`
- delete `api/receipt_review.py`

**Update `main.py`** — router registrations to reflect new file locations and removed `admin_*` files.

**Update any client code / specs** referencing `/api/admin/*` URLs.

**Run:** `inv pre`

---

## Phase 4 — Reorganize `background/`

**Create `background/rate_prefetch/`:**
- `background/rate_prefetch_task.py` → `background/rate_prefetch/task.py`
- `services/exchange_rates.py` → `background/rate_prefetch/exchange_rates.py`
- `services/rate_helpers.py` → `background/rate_prefetch/rate_helpers.py`
- `services/nbp.py` → `background/rate_prefetch/nbp.py`
- `services/nbs.py` → `background/rate_prefetch/nbs.py`

**Create `background/classification/`:**
- `background/receipt_classification_task.py` → `background/classification/task.py`

**Create `background/sheet_logging/`:**
- `background/sheet_logging_task.py` → `background/sheet_logging/task.py`
- `services/sheet_logging.py` → `background/sheet_logging/sheet_logging.py`
- `services/logging_jobs.py` → `background/sheet_logging/logging_jobs.py`
- `services/sheets_client.py` → `background/sheet_logging/sheets_client.py`
- `services/sheets.py` → `background/sheet_logging/sheets.py`
- `services/sheets_write.py` → `background/sheet_logging/sheets_write.py`
- `services/sheet_mapping.py` → `background/sheet_logging/sheet_mapping.py`

**Update imports** across all callers. `services/` should now be empty — delete it.

**Run:** `inv pre`

---

## Phase 5 — Move `imports/`, `reports/`, `tools/` to root `tasks/`

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
- Remote `python -c '...'` strings that reference `dinary.imports.*` and `dinary.reports.*` module paths — update to `tasks.imports.*` / `tasks.reports.*`
- `remote_snapshot_cmd(f"dinary.reports.{module}", ...)` → `remote_snapshot_cmd(f"tasks.reports.{module}", ...)`

**Delete** `src/dinary/imports/`, `src/dinary/reports/`, `src/dinary/tools/`.

**Run:** `inv pre` + full test suite

---

## Phase 6 — Cleanup and verify

- Confirm `src/dinary/services/` is empty and delete it
- Grep for any remaining `dinary.services`, `dinary.imports`, `dinary.reports`, `dinary.tools`, `dinary.background` imports and fix stragglers
- Grep for `/api/admin/` in specs, docs, frontend code — update any hardcoded URLs
- Run full test suite
- Update `specs/architecture/` if it references the old structure

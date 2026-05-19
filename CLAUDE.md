# Dinary — Codebase Guide

## Project overview

Expense-tracking app for Serbia: scan fiscal receipts via QR code, classify items with an LLM, and sync to Google Sheets. Consists of a FastAPI backend and a Vue 3 PWA frontend.

---

## Layout

```
src/dinary/          Python package (FastAPI server)
  api/               FastAPI routers (thin HTTP layer)
  api/controllers/   Business logic called by routers
  adapters/          External service clients (LLM, exchange rates, sheets, sr-invoice-parser)
  background/        Async drain loops (classification, rate_prefetch, sheet_logging)
  db/                SQLite layer — SQL files in db/sql/, migrations in db/migrations/
  sheets/            Google Sheets sheet-mapping / routing logic
  config.py          Pydantic-settings (env prefix DINARY_, file .deploy/.env)
  main.py            FastAPI app factory + uvicorn entry point
webapp/              Vue 3 PWA
  src/api/           Fetch wrappers (one file per API resource)
  src/components/    Vue SFCs
  src/composables/   Reusable composables (useSwipeRow, flushQueue, …)
  src/stores/        Pinia stores
  src/views/         Page-level views (AddView, ReviewView, LLMView)
  tests/             Vitest tests (one file per component / composable / store)
tasks/               Invoke task modules (deploy, imports, reports, backups, devtools)
tests/               pytest tests
specs/               Architecture docs and feature specs
.deploy/             Runtime config (not committed): .env, import_sources.json
```

---

## Key commands

| Task | Command |
|---|---|
| Lint + type-check + format | `uv run inv pre` |
| Python tests | `uv run pytest` |
| Dev server (auto-reload) | `uv run inv dev` |
| Build Vue PWA into `_static/` | `uv run inv build-static` |
| Apply DB migrations (local) | `uv run inv migrate` |
| Frontend tests | `cd webapp && npm test` |
| List all tasks | `uv run inv --list` |

**Never call ruff directly.** Always use `inv pre` — it runs ruff, ruff-format, pyrefly, and pre-commit hygiene hooks in the correct order.

---

## Non-negotiable done gate

Before claiming anything is done, both must be green:

1. `uv run inv pre` → "All checks passed!" + `0 errors` from pyrefly
2. `uv run pytest` → `N passed` with zero failures or errors

Run `inv pre` after each discrete batch of changes, not only at the end.

---

## Backend architecture

**FastAPI** app (`src/dinary/main.py`) with these routers:

- `expenses` — CRUD for expense records
- `expense_corrections` — corrections / re-classification from the review UI
- `catalog` — category groups, categories, tags, events
- `currencies` — exchange rates
- `qr` — parse Serbian fiscal QR codes
- `receipts` — receipt pipeline API (submit URL, check status)
- `rules` — view/manage classification rules
- `llm` — LLM provider management (seed providers via admin API)

Three background async tasks start at lifespan and drain in loops:

| Task | Purpose |
|---|---|
| `sheet_logging_task` | Drains `sheet_logging_jobs` queue → Google Sheets |
| `rate_prefetch_task` | Prefetches NBS/NBP exchange rates into the DB |
| `receipt_classification_task` | Drains `receipt_classification_jobs`: fetches fiscal receipts, runs LLM classification, creates expense rows |

### Receipt classification pipeline

1. Client POSTs a fiscal receipt URL → `POST /api/receipts`
2. A job row is inserted into `receipt_classification_jobs`
3. `receipt_classification_task` wakes up (immediately via `notify_new_receipt()` or on `receipt_drain_interval_sec`)
4. `sr-invoice-parser` fetches and parses the receipt from eFiscal
5. Per-item names are normalised → checked against `classification_rules` table
6. Items not covered by rules are sent to `ProviderPool` (LLM, OpenAI-compatible API)
7. LLM results with confidence ≥ 2 become expense rows; rules are created/updated automatically
8. Sheet-logging jobs are enqueued for the new expenses

Circuit breaker: on `AllProvidersExhausted`, the drain backs off exponentially (60 s → 30 min).

**LLM providers must be seeded via the admin API** (`POST /api/llm/providers`) before the classification drain can run. In local dev this is done manually.

---

## Database

SQLite at `data/dinary.db`. Schema managed with **yoyo-migrations** (`db/migrations/`).

Current migrations:
- `0001` initial schema (expenses, categories, groups, tags, events, exchange_rates)
- `0002` exchange rates source/target columns
- `0003` app currencies table
- `0004` receipt pipeline (receipts, receipt_items, receipt_classification_jobs, classification_rules, stores, llm_providers, llm_call_log)

SQL for reusable queries lives in `db/sql/` and is loaded by `db/sql_loader.py`.

---

## Configuration

All settings use the `DINARY_` prefix and are read from `.deploy/.env` (or environment).

Key settings:

| Env var | Default | Purpose |
|---|---|---|
| `DINARY_DATA_PATH` | `data/dinary.db` | SQLite DB path |
| `DINARY_APP_CURRENCY` | `RSD` | Currency the PWA UI works in |
| `DINARY_ACCOUNTING_CURRENCY` | `EUR` | Currency all `amount` columns are denominated in |
| `DINARY_SHEET_LOGGING_SPREADSHEET` | — | Google Sheets spreadsheet ID/URL for expense logging |
| `DINARY_LLM_BASE_URL` | — | OpenAI-compatible LLM API base URL |
| `DINARY_LLM_API_KEY` | — | LLM API key |
| `DINARY_LLM_MODEL` | `gemini-2.5-flash` | LLM model name |
| `DINARY_RECEIPT_DRAIN_INTERVAL_SEC` | `300` | Receipt drain interval (matches Gemini free-tier 20 RPM) |
| `DINARY_GOOGLE_SHEETS_CREDENTIALS_PATH` | `~/.config/gspread/service_account.json` | Google service account JSON |

Import sources (per-year Google Sheets spreadsheets) are configured in `.deploy/import_sources.json` — this file is optional and only needed for `inv import-*` tasks.

---

## Frontend

Vue 3 + Pinia + Vite PWA. No router — views are shown/hidden by state.

**Stores**: `catalog`, `currency`, `review`, `queue`, `receiptQueue`, `llm`, `toast`, `frequentCategories`

**New in recent work**:
- `CategoryQuickPicks.vue` — quick-pick frequent categories in the review flow
- `CategorySheet.vue` — full category selector sheet
- `ExpenseEditSheet.vue` — swipe-to-edit sheet on an expense row
- `ExpenseRow.vue` — expense row component with swipe affordance
- `useSwipeRow.js` — composable for swipe-to-reveal row actions
- `frequentCategories.js` store — tracks recently used categories

**Testing**: Vitest with `happy-dom`. Run `npm test` from `webapp/`. One test file per component/composable/store.

---

## Code conventions

These rules come from `AGENTS.md` and supplement the defaults in this file.

### Language
- All comments, docstrings, plan files, and in-repo docs: **English only**.
- Data literals (category names, sheet headers, envelope names like `"командировка"`) stay in their **original script** (Cyrillic, etc.) — do not transliterate.
- Reply to the user in whichever language they used.

### Imports
- **No local (in-function) imports.** All imports at module top level, always.
- **No `from __future__ import annotations`** — the project targets Python 3.13+.
- **No re-export patterns** — callers import directly from the module that owns the symbol.

### Plan files
- Never reference `.plans/*.md` files or step numbers from plans inside code comments or docstrings. Plans are ephemeral; the code is the source of truth.

### Tests
- Every new function needs tests in the same session. Never skip.

### Linting
- `inv pre` is the only gate. Never run ruff directly; never bypass hooks with `--no-verify`.

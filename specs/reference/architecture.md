# Architecture

## What this system is

A personal expense tracker for a single user in Serbia. Fiscal receipts are
scanned via QR code, line items are classified with an LLM, and the result
optionally syncs to Google Sheets for familiar pivot-table views. The system
prioritises clean data, scriptability, and low hosting cost over UI polish.

**Pain points in the prior Google Sheets workflow that motivated this:**

- Monthly setup: ~33 category rows duplicated by hand every month.
- Supermarket receipts are opaque — one receipt total mapped to `"еда&бытовые"`,
  no line-item granularity.
- No item-level data: impossible to answer "how much on coffee beans this year".
- Category changes required retroactive manual edits to every historical row.
- Limited analytics: no arbitrary time ranges, no cross-year comparison, no AI
  insights.
- Context-dependent spending (trips, business) handled by duplicating category
  rows with a context prefix — inflexible and hard to query across.

---

## Hard constraints

- **1 OCPU / 1 GB RAM VPS.** Every infrastructure decision is bounded by this.
  No extra daemons, no in-process AI, no multi-worker fanout.
- **Single user.** No in-app auth layer yet; network perimeter (Cloudflare
  Access / Tailscale) is the boundary.
- **Always-on required.** The PWA on iOS cannot run background sync; the server
  must respond within 1–2 seconds while the app is open. Sleeping/serverless
  hosting (Render free tier, Lambda) is unsuitable.

---

## Technology decisions

### SQLite over DuckDB

Three concrete frictions make DuckDB unsuitable:

1. **Exclusive file lock.** DuckDB 1.x holds an exclusive write lock for the
   lifetime of any connection, including `read_only=True`. Any concurrent
   inspector (`inv sql`, DBeaver, ad-hoc scripts) must wait or crash. The
   workaround was a `/tmp` snapshot before every inspection. DuckDB documents
   this as a design choice with no fix planned.
2. **No WAL replication.** DuckDB has no WAL-streaming story — no logical
   replication, no change-data-capture. The only backup primitive is a full
   `EXPORT DATABASE` dump, not a hot backup.
3. **OOM risk on 1 GB RAM.** DuckDB's default `memory_limit` is 80 % of system
   RAM. Any non-trivial analytical query on the server would consume ~800 MB,
   leaving almost nothing for uvicorn.

SQLite in WAL mode gives: single-file embedded storage, zero configuration,
live readers concurrent with a single writer, and a Litestream sidecar that
streams WAL segments off-box without blocking writes.

### SQL files over Ibis

Evaluated both approaches during early development with a synthetic but
representative workload (5 000 mappings, 20 000 expenses, 1 000 inserts):

| Metric | SQL files | Ibis | Ratio |
|---|---:|---:|---:|
| Cold import time | 0.09 s | 0.56 s | 6× slower |
| Cold import RSS | 41 MB | 134 MB | 3× more |
| `resolve_mapping` × 5 000 | 2.0 s | 19.9 s | 10× slower |
| `get_month_expenses` × 20 | 1.1 s | 6.6 s | 6× slower |
| `.venv` size | 107 MB | 340 MB | 3× larger |

The extra cost comes from Ibis building query expressions instead of executing
SQL directly, materialising results through `expr.execute()`, and mapping from
pandas objects to dataclasses. Small lookup-heavy reads pay the penalty on every
call — which is exactly the dominant pattern here.

**Decision:** plain SQL files + `yoyo` migrations. Query SQL lives in
`src/dinary/db/sql/`; migrations in `src/dinary/db/migrations/`.

### PWA over alternatives

Evaluation criteria (a tool was disqualified if it failed any):

| # | Criterion |
|---|---|
| 0 | No app store — install without Apple/Google review |
| 1 | Offline operation — data persisted locally, synced on reconnect; loss is unacceptable |
| 2 | Cross-platform — Android and iOS |
| 3 | Custom REST API — POST to a FastAPI backend |
| 4 | Free for single-user load |
| 5 | QR scanning — camera access for Serbian fiscal QR codes |

| Candidate | Offline (#1) | QR (#5) | Verdict |
|---|---|---|---|
| **PWA (custom)** | Service Worker + IndexedDB | Camera API + `zbar-wasm` | **Pass** |
| Telegram Bot | No | No | Disqualified on #1 and #5 |
| Retool | No per docs | Unclear | Disqualified on #1 |
| Glide Apps | Unclear | Unclear | Unverified on #1, #5 |
| Appsmith | Unclear | Unclear | Unverified on #1, #5 |

**Decision:** custom PWA. Vue 3 + Pinia + Vite + `vite-plugin-pwa`. Source in
`webapp/`, built into `_static/` by `inv build-static`, served by FastAPI's
`StaticFiles` mount at `/`.

### 3D data model over the legacy 2D sheet model

The legacy Google Sheets mixed several orthogonal concepts in the
`(Расходы, Конверт)` pair: hierarchy (`здоровье` = `медицина` + `БАД` +
`лекарства`), beneficiary (`ребенок`), temporary context (`путешествия`), and
purpose (`профессиональное`). Cross-cutting queries — "how much for the child on
the Bosnia trip?" — were impossible.

The model collapses everything to three independent dimensions:

```
expense
  ├── category_id → categories → category_groups    WHAT
  ├── event_id → events                              WHEN/CONTEXT (bounded trips, relocation)
  └── expense_tags → tags                            WHY / FOR WHOM (flat, user-extensible)
```

Category group is derived via `category_id → categories.group_id`, not stored
on the expense. Changing a category's group assignment instantly affects all
historical data — no retroactive migration needed.

See `src/dinary/db/migrations/0001_initial_schema.sql` for the full schema.

### Committed ledger

`expenses` is the final record. Once a row is written, its
`(category_id, event_id, tag set)` is the committed decision. Future pipelines
— receipt queues, AI suggestions, user corrections — live in separate tables,
not as intermediate states overloaded onto `expenses`. This makes historical
data trustworthy and auditable without snapshot isolation tricks.

### Two-currency model

- **`settings.app_currency`** (default `RSD`) — the PWA's display/input
  currency. Amounts the user types, `amount_original`, and `currency_original`
  are in this currency for typical entries.
- **`settings.accounting_currency`** (default `EUR`) — what every
  `expenses.amount`, `income.amount`, and report total is denominated in.
  Stable across RSD fluctuations; comparable year-over-year.

The server converts at NBS rates on write and stores the result in
`expenses.amount`; `amount_original`/`currency_original` preserve the input
verbatim for audit. The accounting currency is pinned to an `app_metadata` row
on first boot and cannot change without explicit migration — see
`src/dinary/db/db_migrations.py` (`_reconcile_accounting_currency`).

### LMDB for analytics.db

`analytics.db` (dashboard configs, tag bucket definitions, LLM conversation
history) uses LMDB. PoloDB was evaluated but its PyPI package ships wheels only
for Python 3.9/3.10; the project requires 3.13+. LMDB installs cleanly on 3.13,
is ACID-compliant, and its key-value model maps naturally to named config keys
and sequence-keyed history entries with standard-library JSON encode/decode.

### Google Sheets as export-only

From Phase 1 onward Sheets are append-only output, never the source of truth.
A `sheet_logging_jobs` queue row is inserted in the same DB transaction as each
new expense. A single lifespan-managed drain task appends rows asynchronously
and handles retries, poisoning, and a circuit breaker for transient Sheets
failures. See [sheets.md](sheets.md) for the column
layout, map-tab resolver, and idempotency design.

---

## System structure

```
src/dinary/          Python package (FastAPI server)
  api/               FastAPI routers (thin HTTP layer)
  api/controllers/   Business logic called by routers
  adapters/          External service clients (LLM, exchange rates, sheets, sr-invoice-parser)
  background/        Async drain loops (classification, rate_prefetch, sheet_logging)
  db/                SQLite layer — SQL in db/sql/, migrations in db/migrations/
  sheets/            Google Sheets sheet-mapping / routing logic
  config.py          Pydantic-settings (env prefix DINARY_, file .deploy/.env)
  main.py            FastAPI app factory + lifespan entry point
webapp/              Vue 3 PWA (source); built into _static/ by inv build-static
tasks/               Invoke task modules (deploy, imports, reports, backups, devtools)
tests/               pytest tests
specs/reference/     Per-subsystem design decisions (authoritative for implementation detail)
```

For implementation detail on each subsystem see `specs/reference/`:

| File | Covers |
|---|---|
| `catalog-api.md` | Versioning, FK-safe sync, soft/hard delete, auth stance |
| `classification-pipeline.md` | Pipeline design, confidence rules, error handling, event auto-attach |
| `llm-providers.md` | Broker design, provider pool rationale, failover, prompt design |
| `pwa-offline.md` | Offline queue, IndexedDB, reconnect pattern |
| `receipt-fetching.md` | Three-path fetch, `suf.purs.gov.rs` reliability |
| `sheets.md` | Column layout, map-tab resolver, atomic reload, idempotency |
| `stores.md` | Chain vs location model, PIB-based normalisation |
| `timestamps.md` | Timezone storage policy |
| `currencies.md` | PWA/server responsibility split |
| `sql-tool.md` | `inv sql` design |

---

## Deployment

**Backend:** FastAPI + SQLite (WAL) on an Oracle Cloud Free Tier AMD Micro VM
(1 OCPU, 1 GB RAM). Litestream streams WAL segments to a secondary VM over
SFTP. Accessible via Cloudflare Tunnel or Tailscale. No Docker in production —
saves RAM. See `docs/src/en/operations.md` for the ops runbook.

**Dependency isolation:** The server is deployed with `uv sync --no-dev --no-group analytics`,
keeping heavy analytics dependencies (DuckDB, Polars, LMDB, Marimo, LLM SDKs) off the VM.
On a 1 GB RAM host these packages would materially increase resident memory and import time.
CI enforces this boundary: server tests run without the `analytics` group installed; analytics
tests run in a separate step after `uv sync --group analytics`. A test failure in the server
step that is caused by a missing analytics import means the boundary has been violated.

---

## Build phases

| Phase | Status | Scope |
|---|---|---|
| 0 — Manual entry + QR total | Done | PWA + FastAPI; QR scanning; Google Sheets write path |
| 1 — 3D ledger | Done | SQLite ledger; idempotent ingestion on `client_expense_id`; export-only Sheets; catalog |
| 2 — Receipt pipeline | Done | `sr-invoice-parser`; LLM classification; confidence levels; review UI |
| 3 — Mobile input expansion | Planned | Full receipt-oriented PWA flow; review ergonomics |
| 4 — AI classification + desktop | Planned | Rust daemon + GUI; `claude -p` batch analysis |
| 5 — Dashboards | Planned | Operational and analytical dashboards; DuckDB-on-laptop analytics |
| 6 — AI analysis | Planned | Spending analysis via `claude -p`; scheduled Sheets sync |

---

## Open questions

- **Cross-year events.** A trip spanning December–January is trivially
  `WHERE event_id = ?` with single-file SQLite. Open: should reporting default
  to the event's full span or a calendar-year slice for year-over-year
  comparisons?
- **Archiving cold years.** The single-file model removed per-year detach.
  Expected answer when the need arises: `COPY ... TO 'YYYY.parquet'` + ranged
  `DELETE`, not re-introducing per-year DB files.
- **`categories.sheet_name` / `categories.sheet_group`.** Declared in
  `0001_initial_schema.sql` as a hook for a future pipeline that projects
  classifications directly to the logging sheet. Still unreferenced by the drain
  worker; revisit in a later phase.

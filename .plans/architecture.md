# Architecture

> **Status note (2026-04):** Phase 1 shipped, then was reset to a **3-dimensional model**: `category`, `event`, `tags[]`. The earlier 5D (with `beneficiary`, `sphere_of_life`, `store`) and the intermediate 4D (`beneficiary` + `sphere_of_life`) are gone. `beneficiary` and `sphere_of_life` collapsed into the flat tag dictionary; `store` was dropped entirely. Google Sheets are **never the source of truth at runtime**: DuckDB is. Historical sheet import is bootstrap-only and runs through the destructive `inv import-budget` path. Optional **sheet logging** (off by default; enabled via `DINARY_SHEET_LOGGING_SPREADSHEET`) appends each new expense to a separate spreadsheet so the operator can build pivot tables in Google Sheets alongside Dinary's analytics. The authoritative source for concrete category/group/tag/event values is [src/dinary/services/seed_config.py](../src/dinary/services/seed_config.py); when this document disagrees with the seed code, the code wins.

### Overview

A personal expense tracking system for a single user living in Serbia.
Receipts are entered via mobile (QR scan or manual), stored in a local database with item-level granularity, automatically categorized,
and analyzed through dashboards and AI-powered insights.

The system is designed to be built incrementally as a vibe-coding project by the user (an experienced developer),
prioritizing clean data model and scriptability over UI polish.

### Repositories

| Repository         | Language | Role                                                                                                                                                                       |
|--------------------|---|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **dinary-server**  | Python (FastAPI + DuckDB) | Backend — REST API, data storage, rule-based classification, dashboards, Google Sheets sync. Also: PWA mobile frontend (in `static/`), user manuals (MkDocs in `docs/`), deployment configs. |
| **dinary** | Rust | Desktop app (macOS/Windows): daemon for background AI tasks via `claude -p`, and GUI for analysis parameters, interactive results view, and quick data entry (text/PDF receipt import). Communicates with dinary-server API. |

#### Documentation convention

- **`docs/`** in dinary-server is a **MkDocs site** with bilingual content (`docs/src/en/`, `docs/src/ru/`). All user-facing manuals (PWA install, deployment guides, Cloudflare setup) go here. Do not place standalone markdown files directly in `docs/` — they will break the MkDocs build.
- **`.plans/`** is for development docs (architecture, phase plans, evaluation notes). These are not published to the MkDocs site.

---

## Data Layer: DuckDB, Partitioned by Year

### Why DuckDB

- Single-file embedded database, zero configuration, runs everywhere (laptop, VPS, Raspberry Pi).
- First-class analytical SQL: window functions, PIVOT/UNPIVOT, native Parquet/CSV/JSON import and export.
- ATTACH allows querying multiple year-files simultaneously for cross-year comparisons.
- Python-native: `import duckdb` — no server, no driver, no ORM needed.
- At the expected scale (~30K item rows/year), every query completes in milliseconds.

### Server Memory Constraint

The production design must fit on an always-on VPS with **1 OCPU / 1 GB RAM**.
This is a hard architectural constraint, not just a deployment preference.

Implications:

- Prefer embedded/local components over additional server daemons. DuckDB is acceptable precisely because it runs in-process and avoids a separate database service.
- The backend must remain a **small FastAPI + DuckDB process**, not a multi-service stack.
- Do not require Docker in production on the 1 GB instance.
- Do not run AI/LLM workloads, heavy batch classification, or other memory-hungry jobs on the server. Those stay on the laptop-side `dinary` agent.
- Keep background work bounded: no fan-out worker pools beyond the asyncio event loop and its default thread pool, no parallel sync pipelines, no large in-memory queues. A small, fixed set of long-lived background tasks (e.g. the sheet-logging periodic drain) is fine; spawning a worker per pending row is not.
- Optional sheet logging is **single-row append per `expense_id`** via the `sheet_logging_jobs` queue; never a full-sheet or full-month recomputation. Disabled (no queue rows are even created) when `DINARY_SHEET_LOGGING_SPREADSHEET` is unset.
- Caches must stay small and optional. Correctness must not depend on large resident in-memory datasets.

### Partitioning Strategy

One DuckDB file per year:

```
data/
├── budget_2025.duckdb
├── budget_2026.duckdb
├── config.duckdb          # categories, groups, events, tags, import_mapping, exchange rates, app_metadata
└── archive/
    └── budget_2024.duckdb
```

**Yearly files** contain transactional data: `expenses`, `expense_tags`, `sheet_logging_jobs`, `income`.

**config.duckdb** contains classification metadata (`category_groups`, `categories`, `events`, `tags`, `import_mapping`, `import_mapping_tags`, `logging_mapping`, `logging_mapping_tags`), the global `expense_id_registry`, import sources, exchange rates, and the singleton `app_metadata` row that holds `catalog_version`.

Archiving a year = moving the file to `archive/`. Cross-year queries use ATTACH:

```sql
ATTACH 'data/budget_2025.duckdb' AS y2025;
ATTACH 'data/budget_2026.duckdb' AS y2026;

SELECT * FROM y2025.main.expenses
UNION ALL
SELECT * FROM y2026.main.expenses;
```

### Schema (3D)

The authoritative SQL lives in [src/dinary/migrations/](../src/dinary/migrations/). The blocks below are a current snapshot.

#### config.duckdb — Classification & Reference Data

```sql
CREATE TABLE category_groups (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE categories (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,
    group_id INTEGER NOT NULL REFERENCES category_groups(id)
);

-- Events stay first-class because trips/camps/relocation are the one analytical
-- object that personal-finance tools usually approximate via categories or memos.
-- `auto_attach_enabled` is a hint for the future receipt-processing pipeline,
-- not for current server behavior. Stored expenses are never silently re-attached.
CREATE TABLE events (
    id                  INTEGER PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    date_from           DATE NOT NULL,
    date_to             DATE NOT NULL,
    auto_attach_enabled BOOLEAN NOT NULL DEFAULT true,
    CHECK (date_to >= date_from)
);

-- Phase-1 fixed flat tag dictionary; PWA hardcodes the same list. Tags absorb
-- everything the old `beneficiary` and `sphere_of_life` axes used to carry.
CREATE TABLE tags (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

-- Maps legacy Google Sheets `(sheet_category, sheet_group)` rows to the 3D
-- model. Used exclusively for bootstrap historical import (year=Y or
-- year=0 fallback). Runtime sheet logging uses the separate, year-agnostic
-- `logging_mapping` table.
CREATE TABLE import_mapping (
    id             INTEGER PRIMARY KEY,
    year           INTEGER NOT NULL DEFAULT 0,
    sheet_category TEXT NOT NULL,
    sheet_group    TEXT NOT NULL DEFAULT '',
    category_id    INTEGER NOT NULL REFERENCES categories(id),
    event_id       INTEGER REFERENCES events(id),
    UNIQUE (year, sheet_category, sheet_group)
);

CREATE TABLE import_mapping_tags (
    mapping_id INTEGER NOT NULL REFERENCES import_mapping(id),
    tag_id     INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (mapping_id, tag_id)
);

-- Global cross-year ownership of `expense_id`: the API rejects re-using the
-- same UUID across `datetime.year` boundaries.
CREATE TABLE expense_id_registry (
    expense_id TEXT PRIMARY KEY,
    year       INTEGER NOT NULL
);

CREATE TABLE import_sources (
    year                  INTEGER PRIMARY KEY,
    spreadsheet_id        TEXT NOT NULL,
    worksheet_name        TEXT NOT NULL DEFAULT '',
    layout_key            TEXT NOT NULL DEFAULT 'default',
    notes                 TEXT,
    income_worksheet_name TEXT DEFAULT '',
    income_layout_key     TEXT DEFAULT ''
);

CREATE TABLE exchange_rates (
    date     DATE NOT NULL,
    currency TEXT NOT NULL,
    rate     DECIMAL(10,4) NOT NULL,
    PRIMARY KEY (date, currency)
);

-- Singleton row carrying the monotonic `catalog_version`. Bumped only by
-- `inv import-catalog` (previous + 1). Echoed by `GET /api/categories` and
-- `POST /api/expenses` so the PWA can opportunistically invalidate caches.
CREATE TABLE app_metadata (
    id              INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    catalog_version INTEGER NOT NULL DEFAULT 1 CHECK (catalog_version >= 1)
);
INSERT OR IGNORE INTO app_metadata (id, catalog_version) VALUES (1, 1);
```

#### budget_YYYY.duckdb — Yearly Transactional Data

```sql
-- expenses is a committed ledger, not a staging area. Once written, the row's
-- (category_id, event_id, tag set) is final; no auto-re-attach happens server-side.
-- `category_id` and `event_id` are cross-DB references into config.duckdb (not
-- enforced by DuckDB; enforced by application code).
-- `sheet_category` / `sheet_group` are populated together for bootstrap-imported
-- rows as audit provenance, and stay NULL for runtime rows.
CREATE TABLE expenses (
    id                TEXT PRIMARY KEY,
    datetime          TIMESTAMP NOT NULL,
    amount            DECIMAL(10,2) NOT NULL,
    amount_original   DECIMAL(10,2) NOT NULL,
    currency_original TEXT NOT NULL DEFAULT 'RSD',
    category_id       INTEGER NOT NULL,
    event_id          INTEGER,
    comment           TEXT,
    sheet_category    TEXT,
    sheet_group       TEXT
);

-- tag_id is a cross-DB ref into config.duckdb.tags; enforced by application code.
CREATE TABLE expense_tags (
    expense_id TEXT NOT NULL REFERENCES expenses(id),
    tag_id     INTEGER NOT NULL,
    PRIMARY KEY (expense_id, tag_id)
);

-- Durable queue for "this expense still needs to be appended to Google Sheets".
-- Producer: POST /api/expenses (inserts the queue row in the same DuckDB
-- transaction as the expenses row).
-- Consumers: an asyncio task spawned by the API handler (opportunistic
-- fast path) and the lifespan-managed periodic `drain_pending` task
-- (durable retry). Both run the same single-row append.
-- A row is deleted on success; failure leaves it as `pending` for retry.
-- `claim_token` + `claimed_at` implement lease-style atomic claim with stale
-- recovery: a later worker may reclaim an `in_progress` row once `claimed_at`
-- is older than the configured timeout.
-- `inv import-budget` does NOT populate this table (historical rows already
-- live in Sheets and are not projected back).
CREATE TABLE sheet_logging_jobs (
    expense_id  TEXT PRIMARY KEY REFERENCES expenses(id),
    status      TEXT NOT NULL DEFAULT 'pending',
    claim_token TEXT,
    claimed_at  TIMESTAMP,
    CHECK (status IN ('pending', 'in_progress'))
);

-- Income keeps `year` as an explicit column so cross-year analytics over
-- ATTACHed budget_YYYY.duckdb files stay uniform.
CREATE TABLE income (
    year   INTEGER NOT NULL,
    month  INTEGER NOT NULL,
    amount DECIMAL(12,2) NOT NULL,
    PRIMARY KEY (year, month),
    CHECK (month BETWEEN 1 AND 12)
);
```

### Three Dimensions

The legacy Google Sheets mixed several unrelated concepts in the `(Расходы, Конверт)` pair: hierarchical grouping (`здоровье` = `медицина` + `БАД` + `лекарства`), beneficiary (`ребенок`, `лариса`), temporary context (`путешествия`), expense purpose (`профессиональное`), and relocation overhead (`релокация`). This caused duplicated category rows and made cross-cutting analysis impossible.

The Phase-1 model collapses everything to three orthogonal dimensions:

```
expense row
  │
  ├── category_id ──→ categories ──→ category_groups     WHAT (фрукты → "Еда")
  │
  ├── event_id ──→ events                                WITHIN WHAT (поездка в Боснию, релокация-в-Сербию)
  │
  └── expense_tags ──→ tags                              WHY SPECIAL / FOR WHOM
                                                         (Аня, Лариса, собака, релокация,
                                                          профессиональное, дача)
```

`beneficiary` and `sphere_of_life` no longer exist as first-class axes — their semantics live in the flat tag dictionary. `store` was dropped entirely and may return in a later phase if a real use case emerges.

Examples:

- **"How much on fruit?"** → `category = фрукты`.
- **"How much on Еда group?"** → group = "Еда" (sums `еда`, `фрукты`, `деликатесы`, `алкоголь`).
- **"How much on the child?"** → `tag = Аня`.
- **"How much on the Bosnia trip, and on what?"** → `event = Босния`, `GROUP BY category`.
- **"All trips this year?"** → `SELECT * FROM events WHERE year = 2026`.
- **"How much does relocation cost me?"** → `event = релокация-в-Сербию` (one long event with `auto_attach_enabled=false`) or `tag = релокация` for older bootstrap rows.

### Event Semantics

- `event` is an optional single-valued dimension for trips, camps, business trips, relocation, and other bounded contexts.
- Each event has `date_from`, `date_to`, and `auto_attach_enabled`.
- **Attachment is decided before insert; stored rows are never auto-re-attached.** `expenses` is the committed ledger, not a staging area.
- In Phase 1 the only path that originates `event_id` is the historical sheet import (via `import_mapping`). The PWA does not know about events — `POST /api/expenses` always stores `event_id=NULL`. The optional sheet-logging worker may read `event_id` when looking up `logging_mapping`, but never originates it.
- Future receipt-processing pipeline (out of scope for Phase 1) will use `auto_attach_enabled` and overlap rules:
  - exactly one auto-attach-enabled event covers the date → suggest/attach;
  - more than one covers the date → user or rule must pick;
  - zero cover the date → no event unless explicit override;
  - manual override allowed in either direction.

### Tag Semantics

- Tags are flat labels, many-to-many with expenses, and replace both the former `beneficiary` and `sphere_of_life` axes.
- Phase 1 tag set = the distinct labels referenced by `import_mapping_tags` plus the hardcoded `PHASE1_TAGS` list in `seed_config.py`. The PWA hardcodes the same list — there is no `GET /api/tags`, no `POST /api/tags`, no `GET /api/events`, no `POST /api/category-groups`.
- `POST /api/expenses` validates `tag_ids[]` against the seed tag table; unknown ids are rejected with 4xx.
- No `tag_type`, no hierarchy, no namespaces, no user-extensible creation in Phase 1.

### Key Design Decisions

**Raw data is immutable; classification is a layer on top.** `expenses.amount`, `amount_original`, `currency_original`, and `datetime` are never rewritten by the server.

**Category group is derived, not stored on expenses.** An expense's group is resolved via `category_id → categories.group_id`. Changing a category's group assignment instantly affects all historical data.

**`expenses` is a committed ledger.** Once a row is written, its `(category_id, event_id, tag set)` is the final decision. There is no silent re-attach/re-detach. Any future receipt queue, raw receipt storage, AI suggestions, or user-resolution tasks will live in *separate* pipeline tables — not as intermediate states overloaded onto `expenses`.

**Tag dictionary is fixed in Phase 1.** Unlike events (which grow by ~5-10/year) or categories (which grow with QR parsing), tags are a small flat dictionary that practically never changes within Phase 1. User-extensible tags, analytics-only tags, and admin UI for tags are deferred to Phase 2.

**`sheet_category` / `sheet_group` are import provenance, not runtime metadata.** Imported rows populate the pair together (with `sheet_group=''` when the legacy row had no envelope); runtime rows leave both NULL. The async append worker does not read these columns.

### Catalog versioning

`config.duckdb.app_metadata.catalog_version` is a monotonic integer. The only Phase-1 bump path is `inv import-catalog`: it preserves the previous value before wipe, runs the migration (`INSERT OR IGNORE` seeds the singleton with `1` on a fresh DB), runs `seed_classification_catalog`, then writes `previous + 1`. `previous = 0` on first-ever run, so the post-seed value is `1`.

`GET /api/categories` and `POST /api/expenses` echo the current `catalog_version` (no bump). The PWA uses it to opportunistically invalidate the cached category list. Phase 2 will reintroduce non-destructive bumps (tag admin, receipt pipeline, etc.).

### Cross-database references and year boundaries

- `expenses.category_id`, `expenses.event_id`, and `expense_tags.tag_id` reference entities in `config.duckdb`.
- DuckDB does not enforce these cross-DB FKs; application code (API validation + ingestion) and verification tasks enforce them.
- `expenses` is partitioned by year in `budget_YYYY.duckdb`, so any lookup keyed by `expense_id` must be interpreted together with the target year/file.
- `expense_id_registry` (in `config.duckdb`) is the global source of truth for cross-year ownership: `POST /api/expenses` reserves `(expense_id → year)` on first insert, allows replay only when the registry already points to the same year, and rejects cross-year reuse with 4xx.
- The registry tracks **runtime** inserts only. `inv import-budget --year=YYYY` truncates `budget_YYYY.duckdb.expenses` and re-imports the historical sheet, but it deliberately does **not** touch `expense_id_registry`: legacy `legacy-YYYYMM-...` ids are not registered (they are deterministic and never collide with the UUIDs the PWA generates), and the runtime-inserted ids that survive in the registry are the correct authoritative mapping — the next runtime POST for one of them will simply re-create the lost row as `created` and the registry stays consistent. Only `inv import-catalog` wipes the registry (it `unlink`s `config.duckdb` wholesale).
- Destructive wipe + full re-import may reassign integer IDs. That is acceptable for the one-time Phase-1 bootstrap/reset workflow because the operator is explicitly warned by `--yes`.

---

## Input Layer

### Receipt Scanning (Serbian Fiscal QR Codes)

Serbian fiscal receipts contain a QR code with a URL to `suf.purs.gov.rs`. The HTML page contains all line items with names, quantities, and prices.

**Existing open-source parsers:**

- [Innovigo/sr-invoice-parser](https://github.com/Innovigo/sr-invoice-parser) — Python library that crawls the SUF PURS page and extracts items as structured data (name, quantity, price, total_price). MIT license.
- [turanjanin/serbian-fiscal-receipts-parser](https://github.com/turanjanin/serbian-fiscal-receipts-parser) — PHP library for the same purpose.

**Flow:**

1. User scans QR code on phone → extracts URL.
2. URL is sent to the backend.
3. Backend fetches the HTML page from SUF PURS, parses line items.
4. Raw HTML is cached in `receipts.raw_html` for reproducibility.
5. Each line item is inserted into `expenses` with `classification_status = 'pending'`.
6. Category rules are applied immediately (pattern matching); matched items get `classification_status = 'auto'`.
7. Unmatched items remain `'pending'` for batch AI classification (see below).

#### Future Receipt Queue Note

The flow above reflects the original design intent, but it is **not** the desired
target architecture for future receipt ingestion.

In future versions, receipt processing should use a **separate asynchronous
pipeline**:

1. A scanned receipt creates a receipt-ingestion job in a dedicated queue / staging area.
2. Parsing, rule-based classification, AI classification, and user-required
   disambiguation happen **outside** the `expenses` table.
3. Only after the receipt is fully resolved are the final expense rows inserted
   into `expenses`.

This means `expenses` is the **committed ledger of finalized expenses**, not a
staging table for partially classified receipt lines. Any future receipt queue,
raw receipt storage, AI suggestions, or user-resolution tasks should live in
separate pipeline tables rather than overloading `expenses` with intermediate
states.

### Manual Entry

For expenses without QR codes (cafés, services, cash payments, foreign purchases):
- User enters: amount, category (from list), optional comment.
- Stored in `expenses` — same table as parsed receipt items, just without `receipt_id`, `quantity`, or `unit_price`.
- Category is assigned at entry time (user picks from a list or types a shortcut).

### Mobile Input Interface (dinary-app)

The specific mobile client is a build-time decision.
The architecture is agnostic — the input layer is a thin client that sends structured data to the backend via a simple REST API.

**Phase 0 (MVP) requirements:**

- Camera access for QR scanning. In the implemented MVP the browser decodes the Serbian fiscal QR locally with `zbar-wasm`, and the client can extract amount/date from the QR URL path without waiting for a backend roundtrip.
- Fast manual entry: amount + group selector + category selector + optional comment, one tap to submit. Entry saves instantly to IndexedDB first; network send happens only after local persistence is secured.
- Offline data persistence via IndexedDB (reliable for installed PWAs — iOS Safari eviction only affects non-installed sites). `navigator.storage.persist()` for additional protection.
- QR scan with parallel processing: while user selects group/category, the app finishes local QR parsing and can still fall back to backend parsing when needed.

**Full requirements (Phase 3 target):**

- All Phase 0 capabilities, plus:
- Confirmation screen after QR scan: shows parsed line items, allows quick category corrections before saving.
- Event selector: if the expense date falls within an active event's date range, auto-suggest it. If multiple active events overlap, show a dropdown. Allow manual assignment/removal.
- Beneficiary selector: defaults to "семья", quick switch to a specific family member.

#### Frontend Tool Evaluation

**Evaluation result**: .plans/frontend-evaluation.md

**Initial candidate list:**

| Tool | Type | Evaluate for |
|------|------|-------------|
| ~~**Telegram Bot**~~ | Chat-based UI | **Disqualified:** does not work offline (fails must-have #1). Lowest dev effort otherwise. Native camera for QR photo/URL sharing. Inline keyboards for category selection. No app install needed. Limitation: no true "form" UX — interaction is sequential, not a single screen. |
| **Glide Apps** | No-code app builder (Google Sheets/SQL backend) | Can it connect to a custom REST API or DuckDB directly? Does it support camera/QR scanning? Free tier limits? Good for rapid prototyping if it can talk to our backend. Check offline support. |
| **Retool** | Low-code internal tool builder | Strong on forms, tables, and API integration. Mobile-responsive. Free tier (5 users) is sufficient. Can it do QR scanning natively or via a component? Overkill for input-only, but could double as an admin/review UI for classifications. Check offline support — likely none. |
| **Appsmith** | Open-source Retool alternative | Self-hostable (important for data ownership). Same evaluation criteria as Retool. Check: mobile UX quality, QR scanning support, DuckDB/REST connectivity, offline mode. |
| ~~**Appgyver (SAP Build Apps)**~~ | No-code native app builder | **Likely disqualified:** produces native mobile apps that require App Store / Google Play publishing (fails must-have #0). QR scanning is a built-in component. Free tier available. Has offline data storage capabilities. Only viable if it supports a web/PWA deployment mode that bypasses store publishing — verify before evaluating further. |
| ~~**Tally / Typeform**~~ | Form builders | **Disqualified:** no offline support (fails must-have #1), no QR scanning (fails must-have #6). Good for quick data capture otherwise. Tally is free and supports webhooks. Likely too rigid for the QR→review→confirm flow. |
| **PWA (custom)** | Self-built Progressive Web App | Maximum control. Camera API for QR scanning (via `navigator.mediaDevices`). Full offline support via Service Workers + IndexedDB. Requires actual frontend development. Best long-term option if no-code tools don't fit. Works on both Android and iOS via browser. |

**Evaluation criteria:**

Must-have (tool is disqualified if it fails any of these):

0. **No mobile app to publish** - avoid creating custom app that we have to sign and send for review by Apple / Google.
1. **Offline operation with guaranteed data persistence** — the app must work without internet. Entered data must be stored locally on the device and synced to the backend when connectivity is restored. Data loss due to network unavailability is unacceptable — this is the primary data entry point.
2. **Cross-platform: Android & iOS** — must work on both platforms (native app, PWA, or responsive web).
3. **API connectivity** — must be able to POST structured data to a custom REST endpoint.
4. **Free for expected load** — sustainable at zero cost for a single user with 10-20 entries/day. No "free trial" that expires.
5. **Longevity / sustainability** — the tool must have a credible future. For open-source: sufficient community (contributors, stars, release cadence). For commercial: a clear business model and track record suggesting the free tier won't be killed. Tools that have recently been acquired, pivoted, or deprecated their free tier are high-risk.
6. **QR scanning** — can the tool access the camera and scan a QR code to extract the URL? Required from Phase 0 (total-only extraction) through Phase 3b (full line-item flow).

Important:
7. **Speed of entry** — how many taps/screens for a manual expense? (critical for daily use adoption)
8. **Dev effort for MVP** — how fast can a working prototype be built?

Nice-to-have:
9. **Self-hostable / data ownership** — does data pass through third-party servers?
10. **Extensibility** — can it grow into the review/classification UI later?

---

## Classification Layer

### Three-tier Classification

**Tier 1: Fuzzy ML based classification like in other personal expense tracking apps.

**Tier 2: AI batch classification (deferred, economical).** Unclassified items (`classification_status = 'pending'`) accumulate on dinary-server throughout the day.
When the user runs dinary (manually or via scheduler), it fetches pending items from the server API and classifies them using `claude -p`:

```bash
# dinary fetches pending items from dinary-server
dinary classify

# Under the hood:
# 1. GET https://server/api/tasks/pending-classifications
# 2. Feeds items to claude -p with category list and classification prompt
# 3. POST https://server/api/tasks/classifications with results
```

This runs on the user's laptop under the existing Claude subscription via `claude -p` (Claude Code CLI, non-interactive mode). No API costs.
Typical batch: 20-50 items, easily fits in a single prompt. dinary-server applies the results to DuckDB.

**Tier 3: Manual confirmation.** AI suggestions are stored as `ai_category_suggestion` and `classification_status = 'ai_suggested'`. The user reviews and confirms (or corrects) via the dashboard or a CLI script. Confirmed classifications can optionally generate new rules in `category_rules` (with `created_by = 'ai'`), so similar items are auto-classified in the future.

### Rule Learning Loop

```
New item → Rule match? → YES → auto-classify, done
                       → NO  → mark 'pending'
                              → AI batch suggests category + rule
                              → User confirms/corrects
                              → New rule added to category_rules
                              → Next time this item appears → auto-classified
```

Over time, the rule table grows and the AI batch shrinks. After a few months, most items are auto-classified; AI handles only genuinely new products.

---

## Analytics Layer

### Operational Dashboard

**Purpose:** "How am I doing this month?" — quick glance on the phone.

**Content:**
- Total spent this month vs. total income.
- Savings rate (income − expenses) / income.
- Spending by category group with budget progress bars (if budgets are set).
- Comparison with same month last year and previous month.
- List of recent unclassified items (items needing attention).

**Implementation:** A static HTML page generated from DuckDB by a Python script. Served locally or via a lightweight HTTP server on a VPS. Regenerated after each new receipt or on a schedule (e.g., hourly). No JavaScript framework needed — HTML + CSS + inline SVG for progress bars, or minimal Chart.js.

### Analytical Dashboard

**Purpose:** "What happened over the past 6 months, and why?"

**Content:**
- Selectable time range (month, quarter, year, custom).
- Breakdown by category, group, store, beneficiary, event, tag — switchable views.
- Trend charts: monthly spending per category/group over time.
- Year-over-year comparison: selected period vs. same period previous year.
- Top-N items by total spend (item-level drill-down from parsed receipts).
- Seasonality detection (are there recurring monthly spikes?).

**Implementation:** An interactive single-page app (React/vanilla JS + Chart.js/Recharts).
Data is pre-aggregated by a Python script into a JSON file that the SPA loads. For ad-hoc queries, the user can also run SQL directly against DuckDB.
The dashboard is a view layer, not a data entry point.

### AI Analysis

**Purpose:** "What should I pay attention to? What can I optimize?"

**Trigger:** On demand, when the user runs dinary. Not automated — the user decides when to run it.

**Flow:**
1. dinary fetches aggregated data from dinary-server:
   ```bash
   dinary analyze --period 2026-Q1
   ```
2. Under the hood: fetches data from server API, feeds to `claude -p`, pushes the report back to dinary-server.
3. The report is stored on the server and optionally displayed in the dashboard.

**Cost:** Zero beyond the existing Claude subscription. A quarterly analysis is ~2-3K tokens of input data + prompt — trivial.

---

## Export Layer: Google Sheets Sync (Phase 1: persistent queue + async worker)

From Phase 1 onward Google Sheets are **export-only** — the historical sheets stay as a familiar read-only view, while DuckDB is the single source of truth for all new manual writes. There is no DB-to-sheet reconciliation: we never read sheet state and rewrite it from DuckDB.

### Queue model

`sheet_logging_jobs` (in `budget_YYYY.duckdb`) is the durable queue: one row per `expense_id` that still needs to be appended to Sheets. The in-process async task spawned by the API handler is just an opportunistic fast path over that queue; the DB queue is the source of truth.

Producer: `POST /api/expenses` inserts the queue row in the *same* DuckDB transaction as the `expenses` row. Consumers both run the same single-row drain path centered on `_drain_one_job` (which in turn uses `_append_row_to_sheet`):

1. **Async worker** — `asyncio.create_task` spawned by the API handler right before returning the response, only on the fresh-insert path that also created the queue row. Never spawned on idempotent replay.
2. **In-process periodic sweep** — started by FastAPI `lifespan`, controlled by `DINARY_SHEET_LOGGING_DRAIN_INTERVAL_SEC` (default 300s, `0` disables). Drain runs immediately on entry, then every N seconds. Recovers from process restarts mid-flight, transient Sheets failures, and the no-event-loop branch of `schedule_logging`. There is no external CLI.

A row is deleted as soon as its single-row append succeeds. On failure (after a successful claim) the row's claim is released back to `pending` so the next sweep retries it.

**Rate-limiting and TTL.** Each `drain_pending` invocation is bounded by `DINARY_SHEET_LOGGING_DRAIN_MAX_ATTEMPTS_PER_ITERATION` (default 15, counts every `_drain_one_job` call across years) and paced by `DINARY_SHEET_LOGGING_DRAIN_INTER_ROW_DELAY_SEC` (default 1.0s, `time.sleep` inside the `asyncio.to_thread` worker — does NOT block the event loop). A single `_drain_one_job` call makes 1-3 Sheets API calls (read the marker cell, optional append, optional dedupe-cleanup), so sustained Sheets API usage is **≤9 calls/min** in the worst case and **~3 calls/min** in the common single-append case, well inside the 60/min per-user Sheets quota. The inter-row sleep further caps the instantaneous rate at ≤1 attempt/sec (≤3 API calls/sec). A TTL in days (`DINARY_SHEET_LOGGING_DRAIN_MAX_AGE_DAYS`, default 90) restricts each sweep to `budget_YYYY.duckdb` files covering the last 90 days (typically 1-2 yearly files) and, inside those files, further filters by `expense.date`. Expired rows remain in `sheet_logging_jobs` untouched. Orphan rows (queue rows whose expense was deleted) bypass the TTL filter via `LEFT JOIN ... OR e.id IS NULL` and still reach `_drain_one_job` for `NOOP_ORPHAN` cleanup. A backlog of N rows clears in `ceil(N / 15)` ticks (60 rows → ~20 min, 1000 rows → ~5.5 h) — slower than the removed `inv drain-logging` tight-loop behaviour, a deliberate trade-off for quota safety. Inside one sweep the newest year drains first (cap-aware), so an older year can be starved for one sweep if the newer year has more than `max_attempts` pending rows; it will be served on the next tick.

### Atomic claim and stale-claim recovery

Workers must atomically claim a row before appending. Claim transitions the row from `pending` to `in_progress` with a unique `claim_token` and a fresh `claimed_at`. If the claim fails (row absent, already claimed and not stale), the worker no-ops — it must not append again. A claim older than the configured timeout is treated as stale and may be reclaimed by a later worker; this is the crash-recovery path for workers that die after claim but before release/delete.

There is no in-process state that needs to survive a process restart, so no task-registry or GC mitigation is required.

### Sheet logging configuration

Sheet logging is **optional**. It is enabled by setting the `DINARY_SHEET_LOGGING_SPREADSHEET` environment variable to a Google Sheets spreadsheet ID or a full browser URL. When unset or empty, `schedule_logging` is a no-op and no Google Sheets calls are made.

The target spreadsheet is **independent of `import_sources`** — import sources configure the historical bootstrap import pipeline, while `DINARY_SHEET_LOGGING_SPREADSHEET` configures the optional runtime append-only logging. The logging worker always writes to the **first visible worksheet** of the configured spreadsheet.

### Logging projection rules

The async worker maps `(expense.category_id, expense.event_id, expense tag set)` to a target `(sheet_category, sheet_group)` using the dedicated `logging_mapping` table (year-agnostic):

- **Lookup order**:
  1. exact match on same `category_id`, same `event_id` (NULL matches NULL), same tag-id set;
  2. category-only fallback: first `logging_mapping` row with the same `category_id` (tag set and `event_id` ignored).
- Ties inside the same preference bucket are resolved deterministically by `logging_mapping.id ASC`.
- **Guaranteed fallback**: if no `logging_mapping` row exists for a category at all, the category name itself is used as `sheet_category` with an empty `sheet_group`. Every expense can be logged.
- For Phase-1 manual rows `event_id` is always NULL, so the effective path is "exact match on `(category_id, tag set)`" then category-only fallback. The `event_id`-aware branch is kept for the future receipt-processing pipeline.
- This is **best-effort placement, not a round-trip guarantee**. Tags whose combination does not match an exact mapping row use the best available fallback.

### Sheet layout contract

The sheet logging worker writes to a flat-table layout (one tab holds **every year** of expenses):

| Column | Content |
| --- | --- |
| A | First day of the expense's month, written as `YYYY-MM-DD` with `USER_ENTERED` so Google stores a date serial. Google **displays** it as `"Apr-1"` etc. (year is dropped from the formatted view but kept in the underlying value). |
| B | Sum-formula in RSD — extended in place by `append_expense_atomic` (`=460+373+...`). |
| C | EUR conversion formula `=IF(H{r}="","",B{r}/H{r})`. |
| D, E | `sheet_category`, `sheet_group` from `logging_mapping`. |
| F | Free-text comment, semicolon-separated when multiple expenses share a row. |
| G | Month number 1..12 (literal, no formula). Used for fast month-block scans. |
| H | Manual EUR↔RSD rate cell. The worker only writes here when it's empty (set-if-missing). |
| J | Opaque idempotency-marker trail of `[exp:<expense_id>]` strings, one per appended expense. Read before each append to detect timeout-after-success retries. |

### Year-aware matching

Column G holds the month number only, so a naive month-only scan would collapse e.g. January 2026 and January 2027 into the same block — a 2027 expense would land on a 2026 row. The worker mitigates this with a separate `batch_get` of column A using `ValueRenderOption.UNFORMATTED_VALUE`: that returns the underlying date serial (or the original string for text-typed cells), which is decoded into a per-row year list (`years_by_row`). All matching helpers (`find_category_row`, `find_month_range`, `get_month_rate`, `_find_insertion_row`) accept this list together with `target_year` and constrain candidate rows by year.

When `ensure_category_row` inserts a new row, the worker splices the new year into `years_by_row` at the insert index so the post-insert helpers stay aligned with the refreshed grid. Without this splice, the rate-write step can either silently skip or land on another year's rate cell.

Cost: one extra `batch_get` of column A per drained expense on top of `get_all_values`. Sheets' default 60 reads/min quota stays comfortable since the in-process periodic drain is rate-limited and the inline `schedule_logging` path runs once per `POST /api/expenses`.

### Idempotency marker (column J)

The append path is **at-least-once**: a Sheets API call may succeed on the server even if we never see the response (network timeout). On retry the queue row is still `pending`, so the next worker would otherwise add the same amount a second time. To close that hole, `append_expense_atomic` reads column J first and skips the entire write if `[exp:<expense_id>]` is already present. The formula extension, the comment append, and the marker write all go in a single `batch_update` so the only two observable post-states are "all three updated" and "none updated" — which the next attempt handles correctly. The drain reports a successful skip as `DrainResult.ALREADY_LOGGED` so the operational `appended` counter only reflects real new sheet writes.

### POST /api/expenses (consolidated contract)

Request shape:

- `expense_id` — required, client-generated UUID. Used as the year-scoped idempotency key and as the row PK inside the target `budget_YYYY.duckdb`.
- `category_id` — required, validated against `config.duckdb.categories`.
- `tag_ids[]` — optional, validated against `config.duckdb.tags`.
- `datetime`, `amount`, `amount_original`, `currency_original`, `comment` — as before.
- `event_id` — **rejected with 4xx** if the client sends it (Phase-1 invariant: events stay out of the PWA contract).

The server routes the write to `budget_YYYY.duckdb` by `datetime.year`, enforces cross-year ownership via `expense_id_registry`, inserts with `event_id=NULL`, `sheet_category=NULL`, `sheet_group=NULL`, and enqueues `sheet_logging_jobs(expense_id)` in the same transaction **only when sheet logging is enabled**. When `DINARY_SHEET_LOGGING_SPREADSHEET` is unset, the expense is still stored in DuckDB but no queue row is created.

Idempotency: same `expense_id` routed to the same `datetime.year` → `200` with the stored row, no rewrite, no new enqueue, no new async scheduling. A WARNING is logged if the replay payload differs from the stored row. Same `expense_id` with a different `datetime.year` → 4xx.

The handler never calls Google API. After building the response, `schedule_logging(expense_id, year)` spawns an `asyncio.create_task` only on the fresh-insert path. `year` is required so the background drain opens the correct `budget_<year>.duckdb` without re-deriving it from the expense row. The response includes `catalog_version` (echo only, no bump).

### Historical bootstrap import

Bootstrap historical import is a separate, destructive code path used only by `inv import-budget`. It does **not** populate `sheet_logging_jobs`: historical rows already live in Sheets and are not projected back. Imported rows populate `sheet_category` / `sheet_group` together as audit provenance (with `sheet_group=''` for no-envelope rows).

---

## Deployment: Split Architecture (Backend + Local Agent)

### Design Principle

The system is split into two parts: an always-on **backend** (VPS) that handles data ingestion and serves dashboards, and a **local agent**
(user's laptop) that runs expensive AI tasks using the existing Claude subscription via `claude -p`.

**Note on source of truth:** In Phase 0, Google Sheets is the single source of truth (the backend writes directly to it).
Starting from Phase 1, DuckDB on the backend becomes the single source of truth, and Google Sheets becomes a read-only view layer synced from DuckDB.

The local agent is stateless — it fetches tasks, processes them, and pushes results back.

```
┌──────────────┐         ┌─────────────────────────────────────┐
│  dinary-app  │────────▶│  dinary-server (VPS)                │
│  (mobile)    │         │                                     │
│              │◀────────│  FastAPI + DuckDB                   │
└──────────────┘         │  - receives expenses from mobile    │
                         │  - rule-based classification (Tier 1)│
                         │  - serves operational dashboard     │
                         │  - serves analytical dashboard      │
                         │  - exposes task queue API            │
                         │  - Google Sheets sync               │
                         └──────────────┬──────────────────────┘
                                        │
                              task queue API (REST)
                                        │
                         ┌──────────────▼──────────────────────┐
                         │  dinary (user's laptop)             │
                         │                                     │
                         │  ┌─ daemon (background) ──────────┐ │
                         │  │  Rust + claude -p               │ │
                         │  │  - batch classification         │ │
                         │  │  - spending analysis            │ │
                         │  │  - push results to server API   │ │
                         │  └────────────────────────────────┘ │
                         │  ┌─ GUI (interactive) ────────────┐ │
                         │  │  Rust + GUI framework           │ │
                         │  │  - analysis params & results    │ │
                         │  │  - quick manual entry           │ │
                         │  │  - paste text/PDF → AI API      │ │
                         │  │    → extract & store expense    │ │
                         │  │  - review AI suggestions        │ │
                         │  └────────────────────────────────┘ │
                         └─────────────────────────────────────┘
```

### dinary-server (VPS)

**What it does:**
- Accepts expenses from dinary-app (REST API).
- Stores everything in DuckDB (config.duckdb + yearly budget files).
- Applies Tier 1 classification (rule-based pattern matching) immediately on ingestion.
- Serves operational and analytical dashboards (static HTML or SPA).
- Syncs aggregated data to Google Sheets on schedule or on demand.
- Exposes a **task queue API** for the local agent:
  - `GET /api/tasks/pending-classifications` — returns unclassified items as JSON.
  - `POST /api/tasks/classifications` — accepts classification results, updates DuckDB.
  - `GET /api/tasks/analysis-export?period=2026-Q1` — returns aggregated data for AI analysis.
  - `POST /api/tasks/analysis-report` — stores the AI-generated report.

**What it does NOT do:**
- Any AI/LLM calls. All AI work is delegated to dinary.

**Hosting (free, always-on options):**

- **Oracle Cloud Free Tier** — AMD Micro VM (1 OCPU, 1 GB RAM, always available) is recommended for reliability. ARM A1 Flex (up to 4 OCPU, 24 GB RAM) is more powerful but often unavailable due to shared capacity pool. Run directly with uvicorn as a systemd service (no Docker — saves RAM on 1 GB instances). Docker available for local development.
- **Self-hosted (Mac/PC)** — run locally, expose via Tailscale Serve (tailnet-only) or Cloudflare Tunnel (custom domain + Cloudflare Access). Aligns with Phase 4 architecture (dinary desktop app on the same machine).

**Important:** sleeping/serverless hosting (Render free tier, AWS Lambda, etc.) is **not suitable** — the PWA on iOS cannot run background sync, so the server must respond within 1-2 seconds while the user still has the app open.

**Accessibility:** API served via Cloudflare Tunnel or Tailscale Serve. For the current MVP, Tailscale Serve is the preferred default because it avoids public internet exposure.

#### 1 GB Server Rules

Because the reference production target is the Oracle AMD Micro instance, the server-side implementation must follow these rules:

- Run a single app process by default. Do not scale by adding multiple uvicorn workers on the 1 GB host.
  - This is also a **correctness** constraint, not just a memory one: `POST /api/expenses` opens `config.duckdb` in **write mode** for the NBS rate cache (`convert_to_eur` can populate `exchange_rates` on a cache miss). DuckDB allows at most one writer per file across processes, so a second uvicorn worker would fail the second concurrent POST with `IO Error: ... is already opened in another process`. Single-worker keeps writes serialized through the event loop. If a future phase needs multiple workers, split rate lookup into a read-only fast path that only takes a write connection on cache miss, and arbitrate the writer through a dedicated background task.
  - Within that single process, the API is already concurrent: FastAPI runs many `POST /api/expenses` handlers on the asyncio event loop, blocking DuckDB/gspread work goes to the default thread pool via `asyncio.to_thread`, and `schedule_logging` plus the lifespan's periodic `drain_pending` add background drain tasks on the same loop. Safety relies on (a) the singleton DuckDB engine in `duckdb_repo` so each file is opened once per process, (b) the optimistic `claim_token` in `claim_logging_job`, and (c) DuckDB's OCC turning lost claim races into a clean `TransactionException` → `None`. The single-process rule exists precisely so this in-process coordination is the only coordination we need; a second OS process (extra uvicorn worker, ad-hoc `python -c` scripts that open `budget_*.duckdb` while the server runs, etc.) bypasses all three layers and trips DuckDB's per-file write-lock. There is intentionally no external CLI for draining the queue — recovery is the lifespan task's job.
- Avoid colocating extra infrastructure on the VPS: no separate Postgres, Redis, Celery, message broker, or background analytics service in Phase 1.
- Treat Google Sheets sync as lightweight projection work, not as a second analytics engine.
- Prefer on-demand or dirty-month scoped recomputation over broad periodic rebuilds.
- Any future feature that materially increases steady-state RAM use must be designed to run off-box (for example on the laptop-side agent) or be explicitly deferred until a larger host is available.

### dinary (User's Laptop)

A desktop application with two components: a **daemon** for background AI processing and a **GUI** for interactive use.

**Daemon (background service):**
- Runs continuously (or on schedule) when the user is at the computer.
- Fetches pending tasks from the dinary-server API.
- Processes them using `claude -p` (Claude Code CLI, non-interactive mode) under the user's existing subscription — no API token costs.
- Pushes results back to the dinary-server API.
- Handles all heavy/batch AI work that can be deferred.

**GUI (interactive desktop app):**
- Set analysis parameters (time range, grouping, filters) and view interactive analysis results.
- Quick manual entry: hot-key to enter an expense, import from email/messages like bank notifications of internet payments.
- Paste text or PDF with a receipt — the app responsively extracts payment data and stores it (uses AI API directly for fast turnaround; see "AI processing modes" below).
- Review and confirm AI classification suggestions.

**AI processing modes:**

The desktop app uses two distinct AI channels depending on latency requirements:

1. **`claude -p` (daemon, batch)** — for tasks where latency is not critical: batch classification, spending analysis, report generation. Runs under the existing Claude subscription at zero API cost. This is the primary AI channel.

2. **AI API (GUI, interactive)** — for tasks that must feel responsive to the user: when the user pastes text or a PDF with a receipt, the app calls an AI API directly to extract payment data (amount, date, store, items) in real time. The user should not wait seconds for `claude -p` to spin up. This is a lightweight, targeted use — simple extraction prompts with small payloads, minimal API cost.

**Task types (daemon):**

1. **Batch classification** (daily or on demand):
   ```bash
   # Fetch unclassified items from dinary-server
   dinary classify

   # Under the hood:
   # 1. GET https://server/api/tasks/pending-classifications → pending.json
   # 2. claude -p "classify these items..." → results.json
   # 3. POST https://server/api/tasks/classifications ← results.json
   ```

2. **Spending analysis** (weekly/monthly/on demand):
   ```bash
   dinary analyze --period 2026-Q1

   # Under the hood:
   # 1. GET https://server/api/tasks/analysis-export?period=2026-Q1 → data.json
   # 2. claude -p "analyze this spending data..." → report.md
   # 3. POST https://server/api/tasks/analysis-report ← report
   ```

3. **Future AI tasks** — any new AI-intensive operation follows the same pattern: dinary-server exposes a task endpoint, dinary fetches, processes with `claude -p`, pushes results back.

**Built in Rust** — targeting macOS and Windows. Packaging model (single binary vs. app bundle, installer type, tray integration,
daemon lifecycle management) depends on the GUI framework choice and will be determined during the Phase 4 GUI framework POC.

### Backup Strategy

- DuckDB files on the VPS (dinary-server) are the primary copy.
- Periodic backup to user's laptop: `rsync` or `scp` of DuckDB files.
- Periodic Parquet export for maximum portability: `COPY expenses TO 'expenses_2026.parquet' (FORMAT parquet);`
- Git for the codebase (scripts, config). Data files excluded from git, backed up separately.

### Security

- dinary-server API protected by Cloudflare Access (if using Cloudflare Tunnel) or by tailnet membership (if using Tailscale Serve). Single user, no need for an in-app auth system.
- Cloudflare Tunnel or Tailscale Serve provides HTTPS without exposing the application port directly to the internet.
- DuckDB files are not accessible from the internet — only through the dinary-server API.

---

## Build Plan (Incremental Phases)

### Phase 0: MVP — Manual Entry + QR Total → Google Sheets (completed)

The fastest path to replacing manual spreadsheet editing, with early validation of QR scanning.
No new database, no line-item parsing — just a mobile frontend that writes directly to the existing Google Sheets structure.

**Scope:**

- A mobile frontend (implemented as a PWA) with a simple form: amount (RSD) + group dropdown + category dropdown + optional comment. This matches the existing spreadsheet model better than a single huge selector.
- **QR scanning with parallel processing:** the user scans a Serbian fiscal receipt QR code on the phone. The QR code is decoded on the device (fully offline — client-side image processing) using `zbar-wasm`. The client extracts amount/date from the receipt URL immediately and shows the form without waiting for the backend. Backend QR parsing remains as a fallback/API capability. No line-item parsing, no store extraction in Phase 0.
- A FastAPI backend that receives the entry and writes it to the existing Google Sheets spreadsheet via the Sheets API. FastAPI (not serverless) because it carries forward into Phase 1 (DuckDB) and Phase 4 (AI agent API) without rewriting.
- **Auto-month creation:** if the backend detects that rows for the current month don't exist yet in the sheet, it automatically creates the full block of category rows for the new month by copying the previous block, preserving spreadsheet formulas, zeroing RSD values, and inserting the new month at the top of the yearly sheet.
- **Currency conversion:** the EUR/RSD exchange rate is stored in the sheet itself, on the first row of each month block. When the backend creates a new month or writes the first expense of the month, it checks that month header row and writes the rate only there if missing.
- **Offline queue:** entries are stored in IndexedDB on the device before any network call. When connectivity is restored, the queue is flushed automatically on app open, on `online`, and after successful user actions when pending items exist. The user must never lose an entry due to network or server failure.
- **Always-on server required:** PWA on iOS cannot run background sync — sync only happens while the app is open. The server must respond within 1-2 seconds. Sleeping/serverless hosting (Render free tier, Lambda) is not suitable. Use Oracle Cloud Free Tier (AMD Micro, always on) or self-hosted Mac/PC with Tailscale Serve / Cloudflare Tunnel.
- No line-item parsing, no store extraction, no DuckDB, no AI. The user picks the category manually, just as they do now — but from a phone instead of editing a spreadsheet. QR scanning only extracts the receipt total amount and date, not individual items or store.

**What this validates:**

- The chosen mobile frontend tool works for daily data entry (offline persistence, speed, UX).
- **QR scanning works reliably** with the chosen frontend tool (camera access, code extraction, end-to-end flow).
- The Google Sheets API integration is reliable.
- The user actually adopts phone-based entry over direct spreadsheet editing.

**Deliverables**

- PWA frontend (in `static/`), backend, manuals, deployment scripts — all in the dinary-server repo
- The `dinary` repo is not used in Phase 0 (reserved for the Rust desktop app, Phase 4+)

**Operational conventions introduced by the completed MVP:**

- Local/CI regression entry point is `inv test`, which runs both pytest and Vitest and writes a shared `allure-results/` directory.
- New tests must preserve the existing Allure taxonomy unless there is an explicit architecture-level reason to extend it.
- Phase 0 approved Allure epics are: `Data Safety`, `Google Sheets`, `API`, `Services`, `Build`.
- Phase 0 approved features are:
- `Data Safety`: `Formula Preservation`, `Comment Preservation`, `Column Protection`, `Offline Queue`, `No Data Loss`
- `Google Sheets`: `Read Categories`, `Write Expense`, `Exchange Rate`, `Month Creation`, `Helpers`
- `API`: `Health`, `Categories`, `Expenses`, `QR Parse`
- `Services`: `Category Store`, `Exchange Rate`, `QR Parser`
- `Build`: `Version`

**Exit criteria for Phase 0:**
- The user has used the system daily for 2+ weeks and no longer opens the spreadsheet to enter data manually.
- QR scanning has been used successfully on real receipts (camera → URL extraction → total + date pre-fill) and is confirmed to work reliably with the chosen frontend tool.

### Phase 1: 3D ledger, idempotent ingestion, export-only Sheets (dinary-server) ✓ IMPLEMENTED (after 2026-04 reset)

Detailed plan: [phase1.md](phase1.md)

- DuckDB with the **3D classification schema** (`category`, `event`, `tags[]`) from day one.
- `import_mapping` + `import_mapping_tags` decompose legacy Google Sheets `(sheet_category, sheet_group)` pairs into 3D assignments for one-time bootstrap import. Runtime sheet logging uses the separate `logging_mapping` table for 3D → 2D projection.
- **PWA contract**: sends `expense_id` (UUID) + `category_id` + optional `tag_ids[]`. Events stay out of the PWA contract; `event_id` from the client is rejected.
- DuckDB-backed expense ingestion with idempotent dedup via `expenses.id PRIMARY KEY` plus year-scoped `expense_id_registry` for cross-year ownership.
- Historical data import (2012–present) imported via destructive `inv import-budget` / `inv import-budget-all` and verified via `inv verify-bootstrap-import` / `inv verify-bootstrap-import-all`. Bootstrap-imported rows populate `sheet_category` / `sheet_group` as audit provenance.
- Google Sheets is **export-only**: a persistent `sheet_logging_jobs` queue plus an `asyncio` worker performs single-row appends. The lifespan-managed periodic `drain_pending` task is the durable retry path. No full-month rebuild, no DB-to-sheet reconciliation.
- Monotonic `catalog_version` (singleton in `app_metadata`) bumped only by `inv import-catalog`; echoed by `GET /api/categories` and `POST /api/expenses`.
- Destructive operator commands (`import-catalog`, `import-budget`, `import-budget-all`, `import-income`, `import-income-all`) print loud warnings and require `--yes`. The coordinated reset flow is: stop server → deploy code/assets → `import-catalog --yes` → `import-budget-all --yes` → `import-income-all --yes` → `verify-bootstrap-import-all` → `verify-income-equivalence-all` → start server. The legacy standalone `inv import-sheet` operator workflow is retired (Phase 1 has no partial re-import semantics).

### Phase 2: Receipt Parser
- Integrate or adapt sr-invoice-parser for fetching and parsing Serbian fiscal receipts from SUF PURS URLs.
- Add a separate **receipt-ingestion queue** (out of the `expenses` ledger) where parsing, rule/AI classification, and user disambiguation happen *before* a final expense row lands in `expenses`.
- Implement AI auto-classification that produces 3D classification directly (`category`, `event`, `tag_ids[]`).
- Switch the PWA receipt flow so a scan submits the receipt-ingestion job immediately; later user interaction is review/correction, not the initial submission.
- Reintroduce non-destructive `catalog_version` bumps for tag admin and the receipt pipeline.

### Phase 3: Mobile Input — Full Version (dinary-app)
**Done as part of MVP**

- **3a: Frontend tool evaluation.

  - ** Research the candidate tools from the evaluation table (see "Frontend Tool Evaluation" section) **and any other tools discovered during research**.
  - Build a minimal MVP (scan QR → send URL → see parsed items with line-item detail) with 1-2 top candidates.
  - Compare: QR scanning reliability, offline data persistence, speed of manual entry, API connectivity, cross-platform behavior (Android + iOS), overall UX on phone.
  - Decide on the tool. Note: QR scanning and basic UX are already validated in Phase 0. This step focuses on whether the Phase 0 tool also handles the full line-item review flow, or whether a different tool is needed for Phase 3b.

- **3b: Build the full mobile input layer** with the chosen tool.

  - QR scan → send URL → parse → store.
  - Manual entry for non-QR expenses.
  - Event auto-suggestion and selection.
  - Beneficiary selector.
  - Offline queue with sync-on-reconnect.

### Phase 4: AI Classification & Desktop App (dinary)

- **4a: GUI framework POC.**
  - Evaluate Rust GUI frameworks for the desktop app (e.g., Tauri, egui/eframe, Slint, Dioxus, Iced).
  - Build a minimal POC: a window with a form (analysis parameters), a results view, and a paste-to-extract flow (paste text → call AI API → display extracted data).
  - Evaluate: cross-platform support (macOS + Windows), native look and feel, ease of iteration, maturity/community, integration with async Rust for API calls.
  - Decide on the framework.

- **4b: Build dinary daemon + GUI.**
  - Daemon: background service that fetches pending tasks from dinary-server, processes with `claude -p`, pushes results back.
  - Implement the task queue API on dinary-server (`/api/tasks/*`).
  - Build the batch classification flow: fetch pending → `claude -p` → push results.
  - GUI: interactive AI API calls for responsive receipt extraction (paste text/PDF → AI API → extract amount, date, items → store via server API).
  - After AI classification of receipt line items is available, change the PWA receipt flow so scanning a receipt submits it immediately without waiting for a manual `Save` press. The scan should create the receipt/import job right away; later user interaction is only for review/correction, not for the initial submission.
  - Implement the review/confirm flow (via GUI or CLI).
  - Wire up rule learning (confirmed classifications → new rules in `category_rules`).

### Phase 5: Dashboards (dinary-server)
- Operational dashboard (static HTML, current month snapshot).
- Analytical dashboard (interactive SPA with time range selector and breakdowns).

### Phase 6: AI Analysis & Google Sheets Sync (dinary + dinary-server)
- Add analysis export endpoint to dinary-server API.
- Build the dinary analysis flow: fetch aggregates → `claude -p` → push report.
- Build the Google Sheets sync script on dinary-server (if not already done in Phase 1).
- Set up scheduled runs on the VPS (sync, dashboard regeneration).

Each phase is independently useful.

- Phase 0 alone eliminates manual spreadsheet editing, validates QR scanning, and validates the mobile input tool.
- Phase 1 establishes the 3D ledger with idempotent ingestion, cross-year `expense_id` ownership, persistent sheet-export queue, and one-shot historical bootstrap import.
- Phase 2 solves the supermarket opacity problem and adds the receipt-ingestion pipeline that originates `event_id` and richer tag sets.
- Phase 3 adds full line-item QR flow and complete mobile input.
- Phase 4 builds the desktop app (daemon + GUI) with AI classification and responsive receipt extraction.
- Phases 5-6 add dashboards, AI analysis, and Google Sheets sync.

## Open questions

- **Cross-year events**: events (e.g. a trip) can span a year boundary (start in December, end in January). Since `expenses` are partitioned into yearly `budget_YYYY.duckdb` files but `events` live in the shared `config.duckdb`, this works at the data level -- expenses in both years reference the same `event_id`. However, reporting and sync need to handle the case where a single event's expenses are split across two yearly DB files. Decide whether to query both years when summarizing an event, or accept per-year totals as sufficient.

# Architecture

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
- Keep background work serialized and bounded: no fan-out workers, no parallel sync pipelines, no large in-memory queues.
- Google Sheets sync should operate on **dirty months / targeted aggregates**, not full-sheet or full-history recomputation on every request.
- Caches must stay small and optional. Correctness must not depend on large resident in-memory datasets.

### Partitioning Strategy

One DuckDB file per year:

```
data/
├── budget_2025.duckdb
├── budget_2026.duckdb
├── config.duckdb          # categories, groups, stores, family, events, tags, rules — shared across years
└── archive/
    └── budget_2024.duckdb
```

**Yearly files** contain transactional data (expenses, receipts, income).

**config.duckdb** contains classification metadata (categories, groups, stores, family members, events, tags, rules)
that is shared across all years and evolves independently of the transactional data.

Archiving a year = moving the file to `archive/`. Cross-year queries use ATTACH:

```sql
ATTACH 'data/budget_2025.duckdb' AS y2025;
ATTACH 'data/budget_2026.duckdb' AS y2026;

SELECT * FROM y2025.main.expenses
UNION ALL
SELECT * FROM y2026.main.expenses;
```

### Schema

#### config.duckdb — Classification & Reference Data

```sql
-- Category groups: high-level budget buckets for aggregated views.
-- Examples: здоровье, транспорт, жильё, питание, развлечения.
CREATE TABLE category_groups (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    monthly_budget_eur DECIMAL(10,2)  -- optional planned budget per month
);

-- Specific expense categories: what was bought.
-- Examples: фрукты, топливо, медицина, кафе, аренда.
-- Each category belongs to exactly one group (or none).
CREATE TABLE categories (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    group_id    INTEGER REFERENCES category_groups(id)  -- nullable
);

-- Stores: normalized store names.
-- Useful for analytics: "how much do I spend at Maxi vs Lidl".
CREATE TABLE stores (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    store_type  TEXT                   -- 'supermarket' | 'pharmacy' | 'gas_station' | 'online' | etc.
);

-- Family members: who the expense is for (beneficiary).
-- Short fixed list. Default = 'семья' (whole family / unspecified).
CREATE TABLE family_members (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE    -- 'семья', 'Андрей', 'Лариса', 'Аня', 'собака'
);

-- Events: temporary situations with their own spending that should be trackable separately.
-- A trip, a renovation project, hosting guests, a camp for the child, etc.
-- Events have date ranges (informational, used for auto-suggestion at entry time)
-- and participants (which family members are involved).
-- Multiple events can overlap in time (e.g., parents' trip + child's camp).
CREATE TABLE events (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,          -- "Босния Сараево+Мостар", "Дивчибаре", "Аня соревнования Гамбург"
    date_from   DATE NOT NULL,
    date_to     DATE NOT NULL,
    is_active   BOOLEAN DEFAULT true,   -- false = archived, hidden from entry UI
    comment     TEXT
);

-- Event participants: which family members are part of each event.
CREATE TABLE event_members (
    event_id    INTEGER NOT NULL REFERENCES events(id),
    member_id   INTEGER NOT NULL REFERENCES family_members(id),
    PRIMARY KEY (event_id, member_id)
);

-- Tags: small fixed set of flags for cross-cutting concerns.
-- NOT for things that are better modeled as category, group, beneficiary, or event.
-- Examples: 'релокация' (extra cost of living abroad), 'профессиональное' (work-related).
-- Expected count: 2-5, practically never grows.
CREATE TABLE tags (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE
);

-- Pattern-based auto-classification of receipt line items.
-- Patterns are matched against item names from parsed receipts.
-- Priority: lower number = higher priority (checked first).
-- Note: for Serbian receipt items (e.g., "AJDARED 1KG", "JAB ZELENA"), rule-based matching
-- has limited effectiveness. Primary classification mechanism is AI (see Classification Layer).
-- Rules capture the easy wins and grow over time from confirmed AI suggestions.
CREATE TABLE category_rules (
    id          INTEGER PRIMARY KEY,
    pattern     TEXT NOT NULL,         -- substring or regex matched against item name
    category_id INTEGER NOT NULL REFERENCES categories(id),
    priority    INTEGER DEFAULT 100,
    created_by  TEXT DEFAULT 'manual'  -- 'manual' | 'ai' — tracks rule origin
);
```

#### budget_YYYY.duckdb — Yearly Transactional Data

```sql
-- The single source of truth for all spending.
-- Every expense — whether parsed from a QR receipt, or manually entered (café, taxi, haircut) —
-- is a row in this table.
-- A manual expense is simply a row with no receipt_id and a hand-picked category.
--
-- Five orthogonal dimensions on each expense, each answering a different question:
--   category_id   → what was bought        (→ category_groups via categories.group_id)
--   beneficiary_id → for whom              (family_members; default = 'семья')
--   event_id      → within which event     (events; nullable)
--   tags          → cross-cutting flags    (via expense_tags; e.g., релокация, профессиональное)
--   store_id      → where purchased        (stores; nullable)
CREATE TABLE expenses (
    id              TEXT PRIMARY KEY,      -- UUID or ULID
    datetime        TIMESTAMP NOT NULL,    -- when the purchase happened
    name            TEXT NOT NULL,         -- item name (from receipt) or description (manual entry)
    quantity        DECIMAL(10,3),         -- nullable for manual entries
    unit_price      DECIMAL(10,2),         -- nullable for manual entries
    amount          DECIMAL(10,2) NOT NULL,-- total for this line: quantity × unit_price, or manual amount
    currency        TEXT DEFAULT 'RSD',    -- ISO 4217: RSD, EUR, BAM, etc.
    category_id     INTEGER,              -- FK to config.categories (nullable until classified)
    beneficiary_id  INTEGER,              -- FK to config.family_members (nullable → defaults to 'семья')
    event_id        INTEGER,              -- FK to config.events (nullable)
    store_id        INTEGER,              -- FK to config.stores (nullable for non-store expenses)
    receipt_id      TEXT,                  -- FK to receipts.id (nullable; manual entries have none)
    comment         TEXT,
    ai_category_suggestion TEXT,          -- raw AI suggestion, stored for review
    classification_status TEXT DEFAULT 'pending'  -- 'pending' | 'auto' | 'ai_suggested' | 'confirmed'
);

-- Many-to-many: an expense can have multiple tags (e.g., both 'релокация' and 'профессиональное').
CREATE TABLE expense_tags (
    expense_id  TEXT NOT NULL REFERENCES expenses(id),
    tag_id      INTEGER NOT NULL REFERENCES config.tags(id),  -- cross-db FK (enforced in app logic)
    PRIMARY KEY (expense_id, tag_id)
);

-- Raw receipt archive. NOT used in analytical queries — no JOINs needed.
-- Exists purely to preserve the original data for reproducibility and debugging.
CREATE TABLE receipts (
    id          TEXT PRIMARY KEY,
    datetime    TIMESTAMP NOT NULL,
    store_id    INTEGER,
    total       DECIMAL(12,2),
    currency    TEXT DEFAULT 'RSD',
    raw_url     TEXT,                  -- SUF PURS URL from QR code
    raw_html    TEXT,                  -- cached HTML page from tax authority
    created_at  TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE income (
    id          TEXT PRIMARY KEY,
    date        DATE NOT NULL,
    amount      DECIMAL(12,2) NOT NULL,
    currency    TEXT DEFAULT 'RSD',
    source      TEXT DEFAULT 'salary',  -- 'salary' | 'bonus' | 'freelance' | 'espp' | etc.
    comment     TEXT
);

CREATE TABLE exchange_rates (
    currency    TEXT NOT NULL,          -- source currency (e.g., 'RSD')
    target      TEXT DEFAULT 'EUR',
    rate        DECIMAL(12,6) NOT NULL, -- 1 unit of currency = rate units of target
    valid_from  DATE NOT NULL,
    valid_to    DATE,
    PRIMARY KEY (currency, target, valid_from)
);
```

### Five Orthogonal Dimensions

The current spreadsheet mixes several unrelated concepts in the "envelope" field: hierarchical grouping (здоровье = медицина + БАД + лекарства),
beneficiary (ребенок, лариса), temporary context (путешествия), expense purpose (профессиональное), and relocation overhead (релокация).
This leads to duplicated category rows and makes cross-cutting analysis impossible.

The new model separates five independent dimensions:

```
expense row
  │
  ├── category_id ──→ categories ──→ category_groups     WHAT (фрукты → питание)
  │
  ├── beneficiary_id ──→ family_members                  FOR WHOM (Аня, Лариса, собака, семья)
  │
  ├── event_id ──→ events ←── event_members              WITHIN WHAT (поездка в Боснию, лагерь Ани)
  │
  ├── expense_tags ──→ tags                              WHY SPECIAL (релокация, профессиональное)
  │
  └── store_id ──→ stores                                WHERE (Maxi, Lidl, онлайн)
```

**"How much on fruit?"** → category = фрукты.
**"How much on health?"** → group = здоровье (медицина + БАД + лекарства + спорт).
**"How much for the child?"** → beneficiary = Аня.
**"How much on the Bosnia trip, and on what?"** → event = Босния, GROUP BY category.
**"How much on fruit during the Bosnia trip?"** → category = фрукты AND event = Босния.
**"All trips this year?"** → SELECT * FROM events WHERE year = 2026.
**"All trips the child went on?"** → events JOIN event_members WHERE member = Аня.
**"How much does relocation cost me?"** → tag = релокация.
**"How much on professional subscriptions?"** → tag = профессиональное AND group = подписки.

No duplicated rows. Each dimension is independent and composable with any other.

### Event Auto-assignment at Entry Time

When a new expense is entered, the system checks if its date falls within any active event's date range:

- **Zero matching events** → event_id = NULL (no suggestion).
- **One matching event** → auto-assigned to that event. User sees it and can remove.
- **Multiple matching events** (overlapping dates) → dropdown list of matching events. User picks one, or none.

Manual override in both directions: assign an expense to an event outside its date range (fueling up before a trip),
or remove auto-assignment (a regular grocery run during a trip that shouldn't count).

### Key Design Decisions

**Raw data is immutable; classification is a layer on top.** Expenses store the original name and amount.
Category, beneficiary, event, and tags can be changed at any time without touching the expense's core data.

**Category group is derived, not stored on expenses.** An expense's group is resolved via `category_id → category.group_id`.
Changing a category's group assignment instantly affects all historical data.

**Beneficiary has a default.** If `beneficiary_id` is NULL, it means "семья" (whole family / general).
This is the common case — only expenses specifically for one person need explicit assignment.

**Events are archivable.** Once a trip/event is over, `is_active = false` hides it from the entry UI dropdown but preserves all data.
Past events are accessible in analytics and can be reactivated if needed.

**Tags are a tiny fixed set.** Unlike events (which grow by ~5-10/year) or categories (which grow with QR parsing),
tags are 2-5 conceptual flags that practically never change.
They mark structural circumstances (relocation, professional use), not temporal events or beneficiaries.

**One table for all expenses.** A café bill for 500 RSD and a line item from a supermarket receipt are both rows in `expenses`.
The difference: the café entry has no `receipt_id`, no `quantity`, and was manually categorized at entry time.

**Receipts table is an archive, not a parent.** `receipts` stores raw HTML and URL for reproducibility.
It is never JOINed in analytical queries. All fields needed for analytics (datetime, store_id, etc.) are denormalized onto each expense row.

**Stores are normalized.** `store_id` on expenses, with a lookup table in config.duckdb.
The receipt parser maps variant spellings ("MAXI", "Maxi DOO", "MAXI SOMBOR") to a single store_id.

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

## Export Layer: Google Sheets Sync

The existing Google Sheets spreadsheet continues to work as a familiar view.
A Python script (using `gspread` or Google Sheets API directly) runs on demand or on a schedule:

1. Queries DuckDB for monthly aggregates by category and group.
2. Writes the data into the existing sheet format (months as columns, categories as rows).
3. Updates the income and savings rows.

This is a **write-only, one-directional sync**: DuckDB → Google Sheets.
The spreadsheet becomes a read-only view; all data entry happens through the new system.
The sync script is idempotent — running it twice produces the same result.

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

### Phase 1: Data Foundation & Idempotent Ingestion (dinary-server)

Detailed plan: [phase1.md](phase1.md)

- Set up DuckDB with the **full 5-dimensional classification schema** (category, beneficiary, event, tags, store) from day one -- not a simplified subset.
- Build a **sheet-to-5D mapping table** (`sheet_category_mapping`) that decomposes the current Google Sheet's flat `(Расходы, Конверт)` pairs into proper 5D assignments. The sheet's envelope column mixes category groups (здоровье), beneficiaries (собака, ребенок), tags (релокация), and event contexts (путешествия) -- the mapping table untangles these.
- **PWA is unchanged** -- it still sends `(category, group)` as in Phase 0. The server resolves these to 5D via the mapping table on ingestion.
- Deploy dinary-server (FastAPI) with DuckDB-backed expense ingestion and idempotent deduplication via `expenses.id PRIMARY KEY`.
- Google Sheets becomes a derived read-only view: an idempotent sync layer projects 5D DuckDB data back into the sheet's flat `(category, group)` format using the mapping table in reverse.
- Client generates `expense_id` (UUID) at enqueue time; server returns `200 created`, `200 duplicate`, or `409 Conflict`.

**Historical data migration is NOT part of Phase 1.** After Phase 1 cutover, DuckDB holds only new expenses; historical data remains in Google Sheets until Phase 1.5.

### Phase 1.5: Historical Data Migration

The existing Google Sheets contain ~10 years of data. Nearly every year used a slightly different category system (different category names, different envelope groupings) and even different column layouts. This makes bulk import impractical -- each year requires individual analysis and its own mapping.

- Analyze each yearly Google Sheets tab individually: identify that year's category/envelope structure, column layout, and how it differs from other years.
- Build per-year mapping from that year's flat `(category, envelope)` pairs to the 5D classification model, handling cases where the same category name meant different things in different years.
- For "путешествия" envelopes: create a per-year synthetic event "отпуск-YYYY" (`date_from = YYYY-01-01`, `date_to = YYYY-12-31`) and map all travel rows to it (same approach as Phase 1 uses for the current year). Once the PWA switches to native 5D input (Phase 2+), set `date_to` of the last synthetic travel event to the release date of the 5D PWA. From that date forward, the user creates specific named trips instead of a per-year umbrella, and the auto-attach rule for `sheet_group = "путешествия"` is retired.
- Build per-year import scripts that create synthetic expense rows in `budget_YYYY.duckdb` with `source = 'legacy_import'`.
- Reconcile imported totals against original sheet totals.
- After successful import, run DuckDB -> Google Sheets sync to verify the rebuilt sheet matches legacy data.

### Phase 2: Receipt Parser
- Integrate or adapt sr-invoice-parser for fetching and parsing Serbian fiscal receipts from SUF PURS URLs.
- Build the ingestion pipeline: URL → fetch HTML → parse line items → insert into `expenses` table in DuckDB.
- Implement fuzzy ML / AI auto-classification that produces 5D classification directly (category, beneficiary, event, tags, store).
- Change the PWA so it no longer works in Google Sheets terms for new receipt/manual flows. From Phase 2 onward, the PWA should use the native 5D classification model directly instead of asking the user for `(Расходы, Конверт)` from the spreadsheet.
- Google Sheets sync uses the `sheet_category_mapping` table in reverse: from the 5D classification produced by AI/rules, determine the target `(Расходы, Конверт)` row in the sheet.

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
- Phase 1 establishes the proper data foundation with idempotent ingestion and deduplication.
- Phase 1.5 migrates historical Google Sheets data into DuckDB (complex, per-year analysis required).
- Phase 2 solves the supermarket opacity problem.
- Phase 3 adds full line-item QR flow and complete mobile input.
- Phase 4 builds the desktop app (daemon + GUI) with AI classification and responsive receipt extraction.
- Phases 5-6 add dashboards, AI analysis, and Google Sheets sync.

## Open questions

- **Cross-year events**: events (e.g. a trip) can span a year boundary (start in December, end in January). Since `expenses` are partitioned into yearly `budget_YYYY.duckdb` files but `events` live in the shared `config.duckdb`, this works at the data level -- expenses in both years reference the same `event_id`. However, reporting and sync need to handle the case where a single event's expenses are split across two yearly DB files. Decide whether to query both years when summarizing an event, or accept per-year totals as sufficient.

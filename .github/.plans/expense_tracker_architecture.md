# dinary — your dinar diary

## Architecture

### Overview

A personal expense tracking system for a single user living in Serbia. 
Receipts are entered via mobile (QR scan or manual), stored in a local database with item-level granularity, automatically categorized, 
and analyzed through dashboards and AI-powered insights.

The system is designed to be built incrementally as a vibe-coding project by the user (an experienced developer), 
prioritizing clean data model and scriptability over UI polish.

### Repositories

| Repository | Language | Role                                                                                                                              |
|---|---|-----------------------------------------------------------------------------------------------------------------------------------|
| **dinary** | Python (FastAPI + DuckDB) | Backend — REST API, data storage, rule-based classification, dashboards, Google Sheets sync. Manuals & configs to setup frontend. |
| **dinary-analyst** | Rust | Local desktop tool — AI classification and spending analysis via `claude -p`, communicates with dinary-server API                 |

---

## Data Layer: DuckDB, Partitioned by Year

### Why DuckDB

- Single-file embedded database, zero configuration, runs everywhere (laptop, VPS, Raspberry Pi).
- First-class analytical SQL: window functions, PIVOT/UNPIVOT, native Parquet/CSV/JSON import and export.
- ATTACH allows querying multiple year-files simultaneously for cross-year comparisons.
- Python-native: `import duckdb` — no server, no driver, no ORM needed.
- At the expected scale (~30K item rows/year), every query completes in milliseconds.

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
Key functional requirements regardless of the chosen tool:

- Camera access for QR scanning.
- Fast manual entry: amount + category selector + optional comment, one tap to submit.
- Event selector: if the expense date falls within an active event's date range, auto-suggest it. If multiple active events overlap, show a dropdown. Allow manual assignment/removal.
- Beneficiary selector: defaults to "семья", quick switch to a specific family member.
- Confirmation screen after QR scan: shows parsed items, allows quick category corrections before saving.

#### Frontend Tool Evaluation (Phase 3 prerequisite)

Before building the mobile input layer, evaluate the candidate tools listed below **and research whether other tools exist** that may fit better. 
The list is a starting point, not exhaustive — the no-code/low-code landscape changes rapidly and there may be newer or niche tools 
that satisfy the requirements better than any of these.

Build a minimal MVP with the most promising 1-2 candidates to compare real-world UX before committing.

**Initial candidate list:**

| Tool | Type | Evaluate for |
|------|------|-------------|
| **Telegram Bot** | Chat-based UI | Lowest dev effort. Native camera for QR photo/URL sharing. Inline keyboards for category selection. No app install needed. Limitation: no true "form" UX — interaction is sequential, not a single screen. **Offline: does not work offline — requires internet for every interaction.** |
| **Glide Apps** | No-code app builder (Google Sheets/SQL backend) | Can it connect to a custom REST API or DuckDB directly? Does it support camera/QR scanning? Free tier limits? Good for rapid prototyping if it can talk to our backend. Check offline support. |
| **Retool** | Low-code internal tool builder | Strong on forms, tables, and API integration. Mobile-responsive. Free tier (5 users) is sufficient. Can it do QR scanning natively or via a component? Overkill for input-only, but could double as an admin/review UI for classifications. Check offline support — likely none. |
| **Appsmith** | Open-source Retool alternative | Self-hostable (important for data ownership). Same evaluation criteria as Retool. Check: mobile UX quality, QR scanning support, DuckDB/REST connectivity, offline mode. |
| **Appgyver (SAP Build Apps)** | No-code native app builder | Produces actual mobile apps. QR scanning is a built-in component. Free tier available. Evaluate: learning curve, API connectivity, ease of iteration. Has offline data storage capabilities. More effort than Telegram but better native UX. |
| **Tally / Typeform** | Form builders | Good for quick data capture. Tally is free and supports webhooks. Can a form-based flow work for receipt entry? Likely too rigid for the QR→review→confirm flow, but worth checking for manual entry only. No offline support. |
| **PWA (custom)** | Self-built Progressive Web App | Maximum control. Camera API for QR scanning (via `navigator.mediaDevices`). Full offline support via Service Workers + IndexedDB. Requires actual frontend development. Best long-term option if no-code tools don't fit. Works on both Android and iOS via browser. |

**Evaluation criteria:**

Must-have (tool is disqualified if it fails any of these):

0. **No mobile app to publish** - avoid creating custom app that we have to sign and send for review by Apple / Google.
1. **Offline operation with guaranteed data persistence** — the app must work without internet. Entered data must be stored locally on the device and synced to the backend when connectivity is restored. Data loss due to network unavailability is unacceptable — this is the primary data entry point.
2. **Cross-platform: Android & iOS** — must work on both platforms (native app, PWA, or responsive web).
3. **API connectivity** — must be able to POST structured data to a custom REST endpoint.
4. **Free for expected load** — sustainable at zero cost for a single user with 10-20 entries/day. No "free trial" that expires.
5. **Longevity / sustainability** — the tool must have a credible future. For open-source: sufficient community (contributors, stars, release cadence). For commercial: a clear business model and track record suggesting the free tier won't be killed. Tools that have recently been acquired, pivoted, or deprecated their free tier are high-risk.

Important:
6. **QR scanning** — can the tool scan a QR code and extract the URL? (must-have for Phase 3b, not required for MVP)
7. **Speed of entry** — how many taps/screens for a manual expense? (critical for daily use adoption)
8. **Dev effort for MVP** — how fast can a working prototype be built?

Nice-to-have:
9. **Self-hostable / data ownership** — does data pass through third-party servers?
10. **Extensibility** — can it grow into the review/classification UI later?

---

## Classification Layer

### Three-tier Classification

**Tier 1: Rule-based (instant, free).** `category_rules` table contains patterns (substrings or regexes) matched against item names. 
Example: pattern `MLEKO` matches category "Dairy", pattern `SREDSTVO ZA` matches "Household chemicals". 
Rules are applied immediately when items are ingested. This handles the majority of repeat purchases after an initial learning period.

**Tier 2: AI batch classification (deferred, economical).** Unclassified items (`classification_status = 'pending'`) accumulate on dinary-server throughout the day. 
When the user runs dinary-analyst (manually or via scheduler), it fetches pending items from the server API and classifies them using `claude -p`:

```bash
# dinary-analyst fetches pending items from dinary-server
dinary-analyst classify

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

**Trigger:** On demand, when the user runs dinary-analyst. Not automated — the user decides when to run it.

**Flow:**
1. dinary-analyst fetches aggregated data from dinary-server:
   ```bash
   dinary-analyst analyze --period 2026-Q1
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
The backend owns the single source of truth (DuckDB). 
The local agent is stateless — it fetches tasks, processes them, and pushes results back.

```
┌──────────────┐         ┌─────────────────────────────────────┐
│  dinary-app  │────────▶│  dinary (VPS)                │
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
                         │  dinary-analyst (user's laptop)     │
                         │                                     │
                         │  Rust binary + claude -p             │
                         │  - fetch pending classification tasks│
                         │  - AI batch classify (claude -p)     │
                         │  - AI spending analysis (claude -p)  │
                         │  - push results back to server API   │
                         │  - any future AI-heavy tasks         │
                         └─────────────────────────────────────┘
```

### dinary (VPS)

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
- Any AI/LLM calls. All AI work is delegated to dinary-analyst.

**Hosting:** Oracle Cloud Free Tier (free ARM VM, 4 cores, 24 GB RAM — permanent free tier). Alternative: any cheap VPS, or even a Raspberry Pi at home with Cloudflare Tunnel for external access.

**Accessibility:** Dashboard and API served via Cloudflare Tunnel (free, no public IP needed) or directly from the VPS.

### dinary-analyst (User's Laptop)

**What it does:**
- Runs on demand (manually or via scheduler) when the user is at the computer.
- Fetches pending tasks from the dinary-server API.
- Processes them using `claude -p` (Claude Code CLI, non-interactive mode) under the user's existing subscription — no API token costs.
- Pushes results back to the dinary-server API.

**Task types:**

1. **Batch classification** (daily or on demand):
   ```bash
   # Fetch unclassified items from dinary-server
   dinary-analyst classify

   # Under the hood:
   # 1. GET https://server/api/tasks/pending-classifications → pending.json
   # 2. claude -p "classify these items..." → results.json
   # 3. POST https://server/api/tasks/classifications ← results.json
   ```

2. **Spending analysis** (weekly/monthly/on demand):
   ```bash
   dinary-analyst analyze --period 2026-Q1

   # Under the hood:
   # 1. GET https://server/api/tasks/analysis-export?period=2026-Q1 → data.json
   # 2. claude -p "analyze this spending data..." → report.md
   # 3. POST https://server/api/tasks/analysis-report ← report
   ```

3. **Future AI tasks** — any new AI-intensive operation follows the same pattern: dinary-server exposes a task endpoint, dinary-analyst fetches, processes with `claude -p`, pushes results back.

**Built as a Rust binary** — single executable, no runtime dependencies, compact installer for macOS and Windows.

### Backup Strategy

- DuckDB files on the VPS (dinary-server) are the primary copy.
- Periodic backup to user's laptop: `rsync` or `scp` of DuckDB files.
- Periodic Parquet export for maximum portability: `COPY expenses TO 'expenses_2026.parquet' (FORMAT parquet);`
- Git for the codebase (scripts, config). Data files excluded from git, backed up separately.

### Security

- dinary-server API protected by API key or mutual TLS (single user, no need for full auth system).
- Cloudflare Tunnel provides HTTPS without exposing the VPS directly.
- DuckDB files are not accessible from the internet — only through the dinary-server API.

---

## Build Plan (Incremental Phases)

### Phase 0: MVP — Manual Entry → Google Sheets (no DuckDB, no QR, no AI)

The fastest path to replacing manual spreadsheet editing. No new database, no receipt parsing — just a mobile frontend that writes directly to the existing Google Sheets structure.

**Scope:**
- A mobile frontend (chosen from the evaluation table, or a quick Telegram bot / PWA prototype) with a simple form: amount (RSD) + category (dropdown from the existing ~33 categories) + category group (auto-filled from category) + optional comment.
- A lightweight backend (Python script or serverless function) that receives the entry and writes it to the existing Google Sheets spreadsheet via the Sheets API.
- **Auto-month creation:** if the backend detects that rows for the current month don't exist yet in the sheet, it automatically creates the full block of category rows for the new month (copying the category/group structure from the previous month). This eliminates the most tedious manual step.
- Currency conversion: RSD → EUR using the same fixed rate currently used in the sheet.
- No item-level parsing, no DuckDB, no AI. The user picks the category manually, just as they do now — but from a phone instead of editing a spreadsheet.

**What this validates:**
- The chosen mobile frontend tool works for daily data entry (offline persistence, speed, UX).
- The Google Sheets API integration is reliable.
- The user actually adopts phone-based entry over direct spreadsheet editing.

**Exit criteria for Phase 0:** the user has used the system daily for 2+ weeks and no longer opens the spreadsheet to enter data manually.

### Phase 1: Data Foundation & Backend Deployment (dinary-server)
- Set up DuckDB schema (config.duckdb + budget_2026.duckdb) on VPS (Oracle Cloud Free Tier).
- Deploy dinary-server (FastAPI) with basic REST API for expense ingestion.
- Migrate existing Google Sheets data into DuckDB.
- Write basic SQL queries for monthly aggregates.
- Backend now writes to both DuckDB (primary) and Google Sheets (view layer).
- Set up Cloudflare Tunnel or direct HTTPS access to the backend.

### Phase 2: Receipt Parser
- Integrate or adapt sr-invoice-parser for fetching and parsing Serbian fiscal receipts from SUF PURS URLs.
- Build the ingestion pipeline: URL → fetch HTML → parse line items → insert into `expenses` table in DuckDB.
- Implement rule-based auto-classification.

### Phase 3: Mobile Input — Full Version (dinary-app)
- **3a: Frontend tool evaluation.** Research the candidate tools from the evaluation table (see "Frontend Tool Evaluation" section) **and any other tools discovered during research**. Build a minimal MVP (scan QR → send URL → see parsed items) with 1-2 top candidates. Compare: QR scanning reliability, offline data persistence, speed of manual entry, API connectivity, cross-platform behavior (Android + iOS), overall UX on phone. Decide on the tool. Note: if the Phase 0 tool already satisfies all must-have criteria, this step may be a confirmation rather than a new evaluation.
- **3b: Build the full mobile input layer** with the chosen tool.
  - QR scan → send URL → parse → store.
  - Manual entry for non-QR expenses.
  - Event auto-suggestion and selection.
  - Beneficiary selector.
  - Offline queue with sync-on-reconnect.

### Phase 4: AI Classification (dinary-analyst)
- Build dinary-analyst as a Rust CLI binary.
- Implement the task queue API on dinary-server (`/api/tasks/*`).
- Build the batch classification flow: fetch pending → `claude -p` → push results.
- Implement the review/confirm flow (via dashboard or CLI).
- Wire up rule learning (confirmed classifications → new rules in `category_rules`).

### Phase 5: Dashboards (dinary-server)
- Operational dashboard (static HTML, current month snapshot).
- Analytical dashboard (interactive SPA with time range selector and breakdowns).

### Phase 6: AI Analysis & Google Sheets Sync (dinary-analyst + dinary-server)
- Add analysis export endpoint to dinary-server API.
- Build the dinary-analyst analysis flow: fetch aggregates → `claude -p` → push report.
- Build the Google Sheets sync script on dinary-server (if not already done in Phase 1).
- Set up scheduled runs on the VPS (sync, dashboard regeneration).

Each phase is independently useful. 

- Phase 0 alone eliminates manual spreadsheet editing and validates the mobile input tool. 
- Phase 1 establishes the proper data foundation. 
- Phase 2 solves the supermarket opacity problem. 
- Phase 3 adds QR scanning and full offline support. 
- Phases 4-6 add intelligence and convenience.

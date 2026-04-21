# Architecture

> **Status note (2026-04, single-file reset):** Phase 1 shipped, was reset to a **3-dimensional model** (`category`, `event`, `tags[]`), and then restructured again to run against a **single `data/dinary.duckdb` file**. The earlier multi-file layout (`config.duckdb` + `budget_YYYY.duckdb`) and the per-year ATTACH/routing code paths are gone. The 5D (with `beneficiary`, `sphere_of_life`, `store`) and intermediate 4D variants are also gone: `beneficiary` and `sphere_of_life` collapsed into the flat tag dictionary and `store` was dropped. Idempotency is now expressed as a PWA-generated `client_expense_id` (UUID) with a DB-level `UNIQUE` constraint and no separate cross-year registry. The PWA works in the configured **app currency** (`settings.app_currency`, default `RSD`): it sends the amount without a currency and the server stores `amount` in the app currency, keeping `amount_original`/`currency_original` for audit. The `POST /api/expenses` response no longer echoes the server-side expense id back to the client. Catalog management is FK-safe: `inv import-catalog` toggles `is_active` instead of deleting catalog rows that ledger tables still reference. Google Sheets are **never the source of truth at runtime**: DuckDB is. Historical sheet import is bootstrap-only and runs through the destructive `inv import-budget` path. Optional **sheet logging** (off by default; enabled via `DINARY_SHEET_LOGGING_SPREADSHEET`) appends each new expense to a separate spreadsheet so the operator can build pivot tables in Google Sheets alongside Dinary's analytics. The authoritative source for concrete category/group/tag/event values is [src/dinary/services/seed_config.py](../src/dinary/services/seed_config.py); when this document disagrees with the seed code, the code wins.

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

## Data Layer: DuckDB, single-file

### Why DuckDB

- Single-file embedded database, zero configuration, runs everywhere (laptop, VPS, Raspberry Pi).
- First-class analytical SQL: window functions, PIVOT/UNPIVOT, native Parquet/CSV/JSON import and export.
- Python-native: `import duckdb` — no server, no driver, no ORM needed.
- At the expected scale (~30K item rows/year × a few tens of years), every query completes in milliseconds, and the whole dataset still fits comfortably in the 1 GB VPS.

### Server Memory Constraint

The production design must fit on an always-on VPS with **1 OCPU / 1 GB RAM**.
This is a hard architectural constraint, not just a deployment preference.

Implications:

- Prefer embedded/local components over additional server daemons. DuckDB is acceptable precisely because it runs in-process and avoids a separate database service.
- The backend must remain a **small FastAPI + DuckDB process**, not a multi-service stack.
- Do not require Docker in production on the 1 GB instance.
- Do not run AI/LLM workloads, heavy batch classification, or other memory-hungry jobs on the server. Those stay on the laptop-side `dinary` agent.
- Keep background work bounded: no fan-out worker pools beyond the asyncio event loop and its default thread pool, no parallel sync pipelines, no large in-memory queues. A small, fixed set of long-lived background tasks (e.g. the sheet-logging periodic drain) is fine; spawning a worker per pending row is not.
- Optional sheet logging is **single-row append per `client_expense_id`** via the `sheet_logging_jobs` queue; never a full-sheet or full-month recomputation. Disabled (no queue rows are even created) when `DINARY_SHEET_LOGGING_SPREADSHEET` is unset.
- Caches must stay small and optional. Correctness must not depend on large resident in-memory datasets.

### Storage layout

A single DuckDB file holds both catalog and ledger tables:

```
data/
└── dinary.duckdb    # catalog (categories, groups, events, tags, *_mapping,
                     # exchange_rates, app_metadata) + ledger (expenses, expense_tags,
                     # sheet_logging_jobs, income)

# Import sources are NOT a DuckDB table. They live as an operator-local
# (gitignored) file at the repo root:
#   .deploy/import_sources.json  # optional, consumed by ``inv import-*``
```

The file path is derived from `settings.data_path` (env var `DINARY_DATA_PATH`), so tests and ad-hoc smoke runs can point at a throwaway file without touching production data. Migrations and `inv` operator tasks run against the same file; there is no longer a separate `config.duckdb` or per-year `budget_YYYY.duckdb`.

Rationale for the single-file model: the projected dataset (tens of years × tens of thousands of item-rows after receipt-line support lands) is tiny by DuckDB standards and fits in tens of MB. Per-year files, `ATTACH`-based cross-year queries, and a separate classification DB added write-lock juggling, registry tables for cross-year id ownership, and a matrix of migration streams — all of which the single-file layout collapses away. Cross-year analytics is just `SELECT ... WHERE YEAR(datetime) BETWEEN ... AND ...` on one table. Archiving (when it becomes useful) is a plain `COPY expenses TO 'archive/2020.parquet' (FORMAT parquet)` plus a ranged `DELETE`.

### Schema (3D)

The authoritative SQL lives in [src/dinary/migrations/0001_initial_schema.sql](../src/dinary/migrations/0001_initial_schema.sql); the block below is a current snapshot. Migrations are applied by a thin DuckDB backend for `yoyo-migrations` ([src/dinary/services/db_migrations.py](../src/dinary/services/db_migrations.py)) because DuckDB has no savepoints and needs a custom transaction wrapper.

```sql
-- -------------------------------------------------------------------------
-- Catalog tables. Natural-key tables (category_groups / categories / events /
-- tags) carry `is_active` so `inv import-catalog` can retire vocabulary
-- entries without deleting rows that ledger tables still reference.
-- -------------------------------------------------------------------------

CREATE TABLE category_groups (
    id         INTEGER PRIMARY KEY,
    name       TEXT UNIQUE NOT NULL,
    sort_order INTEGER NOT NULL,
    is_active  BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE categories (
    id          INTEGER PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    group_id    INTEGER REFERENCES category_groups(id),
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    -- reserved columns for a future receipt-ingestion pipeline that projects
    -- raw-receipt classifications directly into the logging sheet; unused in
    -- Phase 1.
    sheet_name  TEXT,
    sheet_group TEXT
);

-- Events stay first-class for trips/camps/relocation.
-- `auto_attach_enabled` opts the event into the PWA's "auto-select
-- the active event for this expense's date" affordance (the PWA
-- picks the shortest matching active event and the operator can
-- override). `auto_tags` is a denormalised JSON array of tag names
-- the runtime and the bootstrap importer union into the expense's
-- effective tag set whenever the expense is attached to the event;
-- tags referenced from `auto_tags` are prevented from being
-- hard-deleted even if no expense row references them directly
-- (the delete-time reference count in `catalog_writer` walks every
-- `events.auto_tags` payload), and a PATCH rename of a tag cascades
-- into every `events.auto_tags` entry that names it so renumbering
-- a tag does not orphan the auto-attach pipeline.
CREATE TABLE events (
    id                  INTEGER PRIMARY KEY,
    name                TEXT UNIQUE NOT NULL,
    date_from           DATE NOT NULL,
    date_to             DATE NOT NULL,
    auto_attach_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    -- ``auto_tags`` is a JSON array of tag names stored in a plain
    -- TEXT column; DuckDB's JSON extension is not required, we just
    -- round-trip the array through ``json.dumps`` / ``json.loads``.
    -- ``NOT NULL DEFAULT '[]'`` keeps every row in a readable state
    -- without null-handling at the call sites.
    auto_tags           TEXT NOT NULL DEFAULT '[]'
);

-- Phase-1 fixed flat tag dictionary; PWA hardcodes the same list. Tags
-- absorb everything the old `beneficiary` and `sphere_of_life` axes used
-- to carry.
CREATE TABLE tags (
    id        INTEGER PRIMARY KEY,
    name      TEXT UNIQUE NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE exchange_rates (
    currency TEXT NOT NULL,
    date     DATE NOT NULL,
    rate     DECIMAL(18,6) NOT NULL,
    PRIMARY KEY (currency, date)
);

-- NOTE: ``import_sources`` is no longer a DuckDB table. The registry
-- of per-year source sheets lives as an operator-local file at
-- ``.deploy/import_sources.json`` (gitignored). The file is OPTIONAL
-- and only consumed by ``inv import-*`` tasks; runtime (POST /api/expenses,
-- sheet logging, map reload) never reads it. See
-- ``dinary.config.read_import_sources`` and the repo-root ``imports/``
-- directory for the schema + workflows.

-- Maps legacy Google Sheets `(sheet_category, sheet_group)` rows to the 3D
-- model. Used exclusively for bootstrap historical import (year=Y or
-- year=0 fallback). Runtime sheet logging uses the separate
-- `sheet_mapping` table sourced from a hand-curated `map` worksheet
-- tab — see "Catalog sync" below.
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

-- Runtime 3D -> 2D projection for expense logging. Evaluated in
-- `row_order ASC` with **first non-`*` wins per column independently**:
-- each row contributes a `(sheet_category, sheet_group)` pair where
-- either cell may be the literal `'*'` meaning "inherit from a later
-- row". `category_id` / `event_id` `NULL` mean wildcard (match any).
-- Tags filter is a subset check: the row's `sheet_mapping_tags` set
-- must be a subset of the expense's tag-id set. Rebuilt from the
-- hand-curated `map` worksheet tab — see
-- `src/dinary/services/sheet_mapping.py`.
CREATE TABLE sheet_mapping (
    row_order      INTEGER PRIMARY KEY,
    category_id    INTEGER REFERENCES categories(id),
    event_id       INTEGER REFERENCES events(id),
    sheet_category TEXT NOT NULL,
    sheet_group    TEXT NOT NULL
);

-- Note: ``mapping_row_order`` deliberately has **no** FK to
-- ``sheet_mapping(row_order)``. The reload path wipes both tables in
-- a single transaction (``sheet_mapping._atomic_swap``), and DuckDB
-- 1.5 mis-validates such a FK when child rows are deleted before
-- their parents inside the same txn — the validator reflects only
-- committed state, so the freshly-deleted child rows still registered
-- as live references to ``sheet_mapping.row_order`` and the parent
-- delete raised. The FK is therefore intentionally absent from
-- ``0001_initial_schema.sql``; the full context lives in the NOTE
-- comment there. Orphan rows are safe because
-- ``logging_projection.sql`` reads tag filters via a COALESCE'd
-- ``LIST`` subquery that returns an empty list for any orphan.
CREATE TABLE sheet_mapping_tags (
    mapping_row_order INTEGER NOT NULL,
    tag_id            INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (mapping_row_order, tag_id)
);

-- Generic KV metadata. Currently only stores `catalog_version`
-- (monotonic integer, echoed by `GET /api/catalog` and every
-- `POST /api/expenses` response). Two write paths bump it — see
-- "Catalog versioning" below.
CREATE TABLE app_metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT INTO app_metadata (key, value) VALUES ('catalog_version', '1');

-- -------------------------------------------------------------------------
-- Ledger tables.
-- -------------------------------------------------------------------------

CREATE SEQUENCE expenses_id_seq;

-- `id` is a server-owned integer PK (sequence). `client_expense_id` is the
-- PWA-generated UUID; NULL for bootstrap-imported historical rows. The
-- `UNIQUE` constraint combined with DuckDB's multi-NULL UNIQUE semantics
-- gives us: exactly one live row per client UUID, while imported rows
-- coexist freely (all NULL).
-- `amount` is in `settings.app_currency` (default RSD). `amount_original` +
-- `currency_original` preserve the audit value; identity FX keeps both
-- fields equal.
-- `sheet_category` / `sheet_group` are populated together for bootstrap-
-- imported rows as audit provenance, and stay NULL for runtime rows.
CREATE TABLE expenses (
    id                 INTEGER PRIMARY KEY DEFAULT nextval('expenses_id_seq'),
    client_expense_id  TEXT UNIQUE,
    datetime           TIMESTAMP NOT NULL,
    amount             DECIMAL(12,2) NOT NULL,
    amount_original    DECIMAL(12,2) NOT NULL,
    currency_original  TEXT NOT NULL,
    category_id        INTEGER NOT NULL REFERENCES categories(id),
    event_id           INTEGER REFERENCES events(id),
    comment            TEXT,
    sheet_category     TEXT,
    sheet_group        TEXT
);

CREATE TABLE expense_tags (
    expense_id INTEGER NOT NULL REFERENCES expenses(id),
    tag_id     INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (expense_id, tag_id)
);

-- Durable queue for "this expense still needs to be appended to Google
-- Sheets". Producer: POST /api/expenses inserts the queue row in the same
-- transaction as the expenses row (only when sheet logging is enabled).
-- Consumer: the lifespan-managed periodic `drain_pending` task. There is
-- no opportunistic fast-path worker — the periodic sweep is the single
-- writer. A row is deleted on success; a permanent error marks it
-- `poisoned` with the error captured in `last_error` so the sweep skips
-- it afterwards. Transient failures release the claim back to `pending`.
CREATE TABLE sheet_logging_jobs (
    expense_id  INTEGER PRIMARY KEY REFERENCES expenses(id),
    status      TEXT NOT NULL DEFAULT 'pending',
    claim_token TEXT,
    claimed_at  TIMESTAMP,
    last_error  TEXT,
    CHECK (status IN ('pending', 'in_progress', 'poisoned'))
);

-- Income keeps `year` explicit so cross-year analytics stays uniform.
-- Stored in `settings.app_currency`.
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
- Phase 2: the PWA surfaces a date-filtered event dropdown (±30 days of the expense date) and `POST /api/expenses` accepts an optional `event_id`. The historical sheet import still populates `event_id` from `import_mapping`. The sheet-logging drain worker does not originate `event_id`; it reads `expense.event_id` directly when projecting an expense through `sheet_mapping`.
- Events carry an `auto_tags` JSON array: tags attached to the event automatically flow into the expense's `tag_ids` (both at `POST /api/expenses` time and during historical import). Vacation events seed `auto_tags = ["отпуск", "путешествия"]` so either tag alone still routes the expense to the "путешествия" envelope via the default `map` tab's tag-driven rows (the module-private `_TAG_RULES` list in `src/dinary/services/sheet_mapping.py`).
- Future receipt-processing pipeline (out of scope for Phase 1) will use `auto_attach_enabled` and overlap rules:
  - exactly one auto-attach-enabled event covers the date → suggest/attach;
  - more than one covers the date → user or rule must pick;
  - zero cover the date → no event unless explicit override;
  - manual override allowed in either direction.

### Tag Semantics

- Tags are flat labels, many-to-many with expenses, and replace both the former `beneficiary` and `sphere_of_life` axes.
- The initial tag set is the hardcoded `PHASE1_TAGS` list in `seed_config.py`; `import_mapping_tags` rows reference those same tags by id (the seed ordering upserts `tags` before building the mapping rebuild), so they never contribute extra names. After the first `inv import-catalog`, the set grows/shrinks via the Admin API (`POST /api/admin/catalog/tags`, `PATCH /api/admin/catalog/tags/{id}`). The PWA discovers tags via `GET /api/catalog` and supports a multi-select on the expense form.
- `POST /api/expenses` validates `tag_ids[]` against the tag table; unknown ids are rejected with 422, and inactive ids survive only on an idempotent replay whose stored tag set matches exactly.
- No `tag_type`, no hierarchy, no namespaces — tags remain a flat, user-extensible label dictionary.

### Key Design Decisions

**Raw data is immutable; classification is a layer on top.** `expenses.amount`, `amount_original`, `currency_original`, and `datetime` are never rewritten by the server.

**Currency model.** `expenses.amount` is stored in `settings.app_currency` (default `"RSD"`). `amount_original` + `currency_original` preserve the value the user originally entered for audit. The PWA runs in the app currency and sends `amount` without a currency; the server defaults `currency_original` to `settings.app_currency` and writes `amount = amount_original` (identity FX). If a future client sends a different `currency`, the server converts to the app currency through NBS at the expense date and still stores both the converted `amount` and the original `(amount_original, currency_original)` pair. Historical bootstrap import converts the sheet's per-year source currency to the app currency using the NBS middle rate on the 1st of the expense month (via an RSD-anchored two-step conversion), so every row of `expenses.amount` is in the same currency regardless of which decade it was imported from. An additional EUR projection is preserved for the legacy housing heuristic and the 2D→3D diagnostic report, but is never stored in `expenses`.

**Category group is derived, not stored on expenses.** An expense's group is resolved via `category_id → categories.group_id`. Changing a category's group assignment instantly affects all historical data.

**`expenses` is a committed ledger.** Once a row is written, its `(category_id, event_id, tag set)` is the final decision. There is no silent re-attach/re-detach. Any future receipt queue, raw receipt storage, AI suggestions, or user-resolution tasks will live in *separate* pipeline tables — not as intermediate states overloaded onto `expenses`.

**Catalog vocabulary is user-editable from the PWA (Phase 2).** Groups, categories, events, and tags are all mutable via the Admin API (`POST`/`PATCH`/`DELETE /api/admin/catalog/*`) through `catalog_writer`. Retired rows are soft-deleted (`is_active = FALSE`) so `expenses.*_id` FKs stay walkable; `GET /api/catalog` returns both active and inactive rows (with an `is_active` flag) and the PWA filters them out of the dropdowns by default, offering a per-picker "Показать неактивные" toggle that also exposes "Активировать" / "Удалить" affordances. `inv import-catalog` remains the bulk seeding path from the legacy sheet.

**`sheet_category` / `sheet_group` are import provenance, not runtime metadata.** Imported rows populate the pair together (with `sheet_group=''` when the legacy row had no envelope); runtime rows leave both NULL. The async append worker does not read these columns.

### Catalog versioning

`catalog_version` lives in the KV `app_metadata` table. Two write paths bump it, both routing their final write through `duckdb_repo.set_catalog_version`:

1. **`inv import-catalog`** — reads the previous value (defaulting to `0` on a fresh DB) and drives `imports.seed.rebuild_config_from_sheets`, which runs in two phases on the singleton DuckDB engine: phase 1 reads the operator-local `.deploy/import_sources.json` via `dinary.config.read_import_sources` (no DB lock, no network) and pulls category rows from each configured sheet (also no DB lock); phase 2 opens a single `BEGIN`, runs `seed_classification_catalog`, rebuilds `import_mapping` / `import_mapping_tags` from scratch, then invokes a `finalize` hook that validates coverage against the hardcoded taxonomy and — hash-gated against a pre-phase-2 `hash_catalog_state` snapshot — writes `previous + 1` before `COMMIT`. The phase-2 transaction is the atomic boundary: a validation failure in the hook rolls back the catalog rebuild too, instead of leaving the DB half-committed. A genuine no-op reseed (same hardcoded groups, same remote mappings) leaves the hash unchanged and skips the bump, so PWA ETag-validated `GET /api/catalog` keeps serving 304s. The old `import_sources` DuckDB table and its snapshot/restore dance are gone — the file-backed SoT removed both the in-transaction preserve step and the "configuration stored inside derived DB state" layering violation.
2. **Admin API (`catalog_writer`)** — every `POST /api/admin/catalog/<kind>` and `PATCH /api/admin/catalog/<kind>/<id>` mutation runs through `src/dinary/services/catalog_writer.py`, which computes a sha256 of the canonical catalog state before and after the mutation and bumps `catalog_version` only if the hash changed. A no-op rewrite (re-adding an already-active name, renaming a row to its current name) does **not** bump.

**Scope — what is and isn't part of `catalog_version`:** the hash canonicalises the shape of `category_groups`, `categories`, `events` (including `auto_tags`), and `tags`. It deliberately does **not** cover `sheet_mapping`/`sheet_mapping_tags` or `import_mapping`/`import_mapping_tags`. Runtime 3D→2D routing (the `map` tab, reloaded by `services/sheet_mapping.py`) and the derived per-year `import_mapping` rebuild (owned by `seed_classification_catalog`) are internal server state that the PWA never reads directly, so there is nothing for a client to invalidate when they change. Conversely, a pure `/api/admin/reload-map` or a no-op `inv import-config` that only refreshes mappings leaves `catalog_version` untouched, and the PWA's ETag-validated `GET /api/catalog` keeps serving `304 Not Modified`. If the mapping tables ever surface on an API the PWA reads, the hash definition in `catalog_writer.hash_catalog_state` is the single place to extend.

`GET /api/catalog` sets a weak ETag `W/"catalog-v<N>"` on the HTTP `ETag` response header (the body does **not** duplicate it — the value is a pure function of `catalog_version` and is derived client-side). The PWA stores the response in `localStorage["catalog"]` keyed by version and revalidates with `If-None-Match`. `POST /api/expenses` also echoes the current version so the PWA can detect staleness after each expense without a dedicated catalog round-trip.

**Admin API authentication:** Phase-2 intentionally does **not** gate
`/api/admin/*` or `/api/admin/catalog/*` on any credential. The
earlier `DINARY_ADMIN_API_TOKEN` header-check was removed along with
the single-file DB refactor because a shared static token was both
annoying (PWA users had to type it on every add-tag modal) and
weaker than the network layer we already rely on (`dinary-server is
only reachable through Cloudflare Access or a Tailscale tailnet
— see "Deployment notes"). A proper app-level auth layer (session
cookies or OIDC) is listed under the open items and will replace
the current trust-the-network stance before the service is exposed
publicly. Until then, **any** request that reaches `/api/admin/*`
is accepted; this is safe in the current single-user deployment
and deliberately unsafe anywhere else.

**Admin API write semantics:**

* `add_*` on a name already in use: if the existing row is inactive, it is reactivated with its other columns (sort_order / date_from / date_to / auto_attach_enabled / sheet_name / sheet_group / auto_tags) preserved — to change those, the caller uses `edit_*`. On reactivation, `add_category` does apply the caller's `group_id` because it is part of the "where am I putting this" intent of the add. Response includes `status ∈ {"created", "reactivated", "noop"}`. The reactivate branch still validates the caller's body (`date_from <= date_to` for events, `auto_tags` names resolve to active tags) even though the values are discarded: an invalid body surfaces as 422, not as a silently-ignored 200. This keeps `add_event` symmetric with `edit_event` and prevents "PWA sent garbage, server dropped it under a successful reactivate" failure modes.
* `edit_*` is atomic within a single transaction: validations (not-found, name conflict, inactive-group, in-use guard, post-patch `date_from <= date_to`) all run **before** any UPDATE, so a failed validation never leaves the row half-edited. `edit_tag(name=...)` additionally cascades the rename into every `events.auto_tags` JSON array that references the old name (inside the same transaction), so the auto-attach pipeline keeps working without the operator having to touch each event by hand; without the cascade, `resolve_event_auto_tag_ids` would log "unknown/inactive tag name" WARNs and silently drop the renamed tag from every new expense created under that event.
* `DELETE /api/admin/catalog/<kind>/<id>` applies hard-vs-soft based on **all** FK-bearing tables, not just the ledger: a category / event / tag is hard-deleted only when no `expenses`/`expense_tags` row **and** no `sheet_mapping`/`sheet_mapping_tags`/`import_mapping`/`import_mapping_tags` row references it; otherwise it is soft-deleted (`is_active = FALSE`) to keep the FK graph consistent. `usage_count` in the response still reflects the ledger count only (that is what the PWA phrases as "used by N expenses"); mapping references are an implementation detail of the hard-delete safety check.
* Groups are special: `DELETE /api/admin/catalog/groups/<id>` refuses (409) while any category — active or inactive — still points at it, because leaving an active category under an inactive group would be a semantic lie. Operators soft- or hard-delete the child categories first.
* Retire guards via `edit_*(is_active=False)` still apply: flipping a category / event / tag inactive while it is in use by an expense succeeds (soft), while flipping a group inactive while it still has *active* child categories is refused (observability, not FK safety — children must be retired first).

The seed path and the admin path are not unified yet — `seed_from_sheet` runs its step-3 bulk SQL in one outer transaction (see "Catalog versioning" path 1) that `catalog_writer`'s per-mutation `BEGIN`/`COMMIT` cannot nest inside. Both paths funnel their final version write through `duckdb_repo.set_catalog_version`, so any future audit hook can intercept them uniformly.

### Catalog sync (FK-safe in-place)

`inv import-catalog` runs **non-destructively** against the ledger. It does **not** delete or recreate catalog rows that historical `expenses` might reference; instead, it:

1. Loads every row already in `categories` / `category_groups` / `events` / `tags` into an in-memory map keyed by natural key (`name`) and stable `id`.
2. For each entity present in the sheet-driven vocabulary, `UPDATE` the existing row in place (preserving the `id`) with `is_active = TRUE` plus the latest `group_id` / `date_from` / `date_to` / `sort_order`; `INSERT` any genuinely new natural keys.
3. For each entity **not** present in the new vocabulary, set `is_active = FALSE`. The row stays in the table so `expenses.category_id` (etc.) remains walkable.
4. Rebuild the `import_mapping` / `import_mapping_tags` tables from scratch. These have no ledger FKs pointing at them, so a plain wipe-and-reseed is safe; `_purge_mapping_tables` runs in auto-commit *outside* the main write transaction because DuckDB 1.5 does not see intra-transaction `DELETE`s in the FK index — a `DELETE FROM import_mapping_tags` followed by `DELETE FROM import_mapping` inside a single `BEGIN`/`COMMIT` raises FK violation on the second statement (the first delete's rows still look FK-referenced from the second delete's perspective). Running each `DELETE` in its own implicit transaction sidesteps the limitation; losing transactional atomicity on the purge is acceptable because the mapping tables are pure derived state (a crashed rebuild leaves them empty, which is the same state a fresh migration would produce and the very next seed run repopulates).
5. Bump `catalog_version`.

Runtime 3D -> 2D projection lives in the `sheet_mapping` / `sheet_mapping_tags` tables. These are not derived from historical sheet data but from a hand-curated `map` worksheet tab in the logging spreadsheet, reloaded lazily via `sheet_mapping.ensure_fresh()` (Drive API `modifiedTime` check) and eagerly via `POST /api/admin/reload-map`.

`GET /api/catalog` returns **every** catalog row (active and inactive) and stamps each with an `is_active` flag so the PWA can filter client-side and expose a per-picker "Показать неактивные" toggle plus an "Активировать" action (`PATCH /api/admin/catalog/<kind>/<id>` with `{"is_active": true}`). This keeps retired vocabulary reachable on demand without polluting the default UI.

### Cross-year references

- `expenses.category_id`, `expenses.event_id`, and `expense_tags.tag_id` all reference catalog tables in the same DB file; DuckDB enforces the FKs declaratively.
- `client_expense_id` is a UUID generated by the PWA on enqueue; the server does not partition by year, so there is no per-year reservation table and no cross-year ownership rule. Replays are disambiguated by the `UNIQUE` constraint plus a payload comparison (see "POST /api/expenses").
- Bootstrap-imported historical rows carry `client_expense_id = NULL` (they never went through a runtime idempotency path). DuckDB allows multiple NULLs in a `UNIQUE` column, so historical rows coexist with runtime UUIDs without collision.
- Destructive re-import of historical data (`inv import-budget --year=YYYY --yes`) reassigns `expenses.id` values for imported rows. That is acceptable because the sequence-driven PKs are server-internal; the PWA never sees them, and runtime `client_expense_id` rows are never touched by the bootstrap importer. (The per-year importer wipes only rows whose `YEAR(datetime)` matches the target year.)

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

`sheet_logging_jobs` is the durable queue: one row per `expenses.id` (integer PK) that still needs to be appended to Sheets. Living in the same DB file as `expenses`, it joins cheaply and the queue row is inserted inside the same write transaction as the ledger row.

- **Producer**: `POST /api/expenses` inserts the queue row in the same transaction as the `expenses` row — but **only when** `DINARY_SHEET_LOGGING_SPREADSHEET` is set. When logging is disabled, the ledger row is still written; the queue just stays empty.
- **Consumer**: the lifespan-managed periodic `drain_pending` sweep (see below). There is no API-handler fast-path worker, no inline `schedule_logging` call, and no external CLI — the periodic sweep is the single writer. This intentionally simplifies the concurrency story for the single-worker server: the drain is the only piece of code that writes to Google Sheets.

A row is deleted as soon as its single-row append succeeds. On transient failure the claim is released back to `pending` so the next sweep retries it; on a permanent failure (`ValueError` from projection resolution, an unmappable category, a 4xx from Sheets, etc.) the sweep flips `status='poisoned'` and records the error message in `last_error`, so the sweep skips it until an operator investigates.

### Periodic drain

Started by FastAPI `lifespan` and controlled by `DINARY_SHEET_LOGGING_DRAIN_INTERVAL_SEC` (default 300s, `0` disables). The sweep runs immediately on entry, then waits on an `asyncio.Event` with a timeout of N seconds — whichever fires first triggers the next sweep. This keeps the append latency low for user-driven traffic (a `POST /api/expenses` that creates a fresh ledger row calls `sheet_logging.notify_new_work()`, which wakes the sweep instantly) while the timer remains the canonical fallback for process restarts and for crash-recovery of claims left by a previous worker. A `time.sleep`-paced `asyncio.to_thread` worker keeps the event loop responsive.

Notify semantics: only fresh creates notify. Idempotent replays (`status=duplicate`) did not enqueue a new queue row, so they do not wake the loop; the original insert's notify already did. Notifies are coalesced (multiple POSTs while a sweep is running collapse into a single wake), so burst traffic does not cause sweep thrash.

Per-sweep limits:

- `DINARY_SHEET_LOGGING_DRAIN_MAX_ATTEMPTS_PER_ITERATION` (default 15) caps how many jobs are attempted in one sweep.
- `DINARY_SHEET_LOGGING_DRAIN_INTER_ROW_DELAY_SEC` (default 1.0s) paces successive attempts inside the sweep.

A single attempt makes 1–3 Sheets API calls (marker read, optional batch write, optional dedupe cleanup), so sustained Sheets usage stays well inside the 60/min per-user quota. The single-file DB made the previous per-year / TTL-in-days filtering unnecessary — the sweep just picks up every non-poisoned row and respects the attempts cap.

### Circuit breaker for transient Sheets errors

Instead of retrying every queued row on every transient failure, `drain_pending` wraps each attempt in a global circuit breaker:

- On a transient error (network timeout, 5xx, `ConnectionError`, non-4xx `gspread.APIError`) the breaker arms with an exponential backoff starting at 60s and capped at 30min (`_BACKOFF_INITIAL_SEC` / `_BACKOFF_MAX_SEC` in [src/dinary/services/sheet_logging.py](../src/dinary/services/sheet_logging.py)).
- Subsequent sweeps short-circuit until `_backoff_until` elapses.
- A successful append resets the breaker to zero.

Permanent errors (4xx, value/FK errors, missing expense row) do not arm the breaker — they flip that one queue row to `poisoned` and the sweep continues.

### Atomic claim and stale-claim recovery

Workers must atomically claim a row before appending. Claim transitions the row from `pending` to `in_progress` with a unique `claim_token` and a fresh `claimed_at`. If the claim fails (row absent, already claimed and not stale, or already poisoned), the worker no-ops — it must not append again. A claim older than the configured timeout is treated as stale and may be reclaimed by a later worker; this is the crash-recovery path for workers that die after claim but before release/delete.

### Sheet logging configuration

Sheet logging is **optional**. It is enabled by setting the `DINARY_SHEET_LOGGING_SPREADSHEET` environment variable to a Google Sheets spreadsheet ID or a full browser URL. When unset or empty, `POST /api/expenses` does not enqueue a queue row and the periodic drain runs over an empty table; no Google Sheets calls are made.

The target spreadsheet is **independent of `.deploy/import_sources.json`** — import sources configure the historical bootstrap import pipeline, while `DINARY_SHEET_LOGGING_SPREADSHEET` configures the optional runtime append-only logging. The logging worker always writes to the **first visible worksheet** of the configured spreadsheet.

### Logging projection rules

The drain worker maps `(expense.category_id, expense.event_id, expense tag set)` to a target `(sheet_category, sheet_group)` by walking `sheet_mapping` in `row_order ASC`:

- **Match filter**: a row is a candidate when all three hold: `category_id` matches the expense or is `NULL` (wildcard), `event_id` matches or is `NULL`, and the row's `sheet_mapping_tags` set is a subset of the expense's tag-id set.
- **Column resolver**: `sheet_category` and `sheet_group` are resolved independently — the first non-`'*'` value encountered in `row_order ASC` among the matching candidates wins for each column. A row that contributes a literal `'*'` in a column defers to later rows for that column only.
- **Guaranteed per-column fallback**: `duckdb_repo.logging_projection` applies the fallback itself, independently per column: if the resolver didn't pick a `sheet_category` it uses `categories.name`; if it didn't pick a `sheet_group` it uses the empty string. A partial match (e.g. a tag rule rewrote only `sheet_group`) keeps the resolved column and only substitutes the missing one. The function returns `None` exclusively when the expense's `category_id` itself is not in the catalog — the one state where no sensible target can be synthesized. The drain loop translates that `None` into a poison-queue signal (there is no landing cell to use).
- The lookup is deterministic (row_order mirrors the row order in the `map` worksheet), so operators can reason about output by reading the tab top-down.

### `sheet_mapping` refresh model

The `sheet_mapping` / `sheet_mapping_tags` tables are derived state; the source of truth is the `map` worksheet tab in the logging spreadsheet. `src/dinary/services/sheet_mapping.py` owns two refresh entry points:

- `ensure_fresh()` — the drain loop calls this once per sweep. Issues a Drive `files.get(fields=modifiedTime)` call (sub-second, no row data) and only re-parses the tab when the returned timestamp differs from the process-local cache. Network errors downgrade to a warning so an outage on the Drive side cannot stall draining whatever is already in the table.
- `reload_now()` — the admin endpoint (`POST /api/admin/reload-map`) calls this unconditionally. Captures `modifiedTime` before **and** after the sheet read; if the timestamp shifts during the read, the new rows are still swapped into the DB (they're at least as fresh as what was there) but the cache is left untouched so the next `ensure_fresh()` tick picks up the post-edit version instead of treating a possibly-stale snapshot as steady state.

Both swap `sheet_mapping` and `sheet_mapping_tags` inside a single DuckDB transaction so the drain worker never sees a half-populated map. To make that single transaction possible, `0001_initial_schema.sql` deliberately declares `sheet_mapping_tags.mapping_row_order` **without** a FK to `sheet_mapping.row_order` (see the NOTE comment in the migration): DuckDB 1.5's FK validator reflects only committed state, so `DELETE FROM sheet_mapping_tags; DELETE FROM sheet_mapping` inside one txn used to raise a spurious "child rows still reference parent" ConstraintException, which `ensure_fresh()` swallowed with an ERROR log and kept serving the stale in-DB mapping — silently ignoring every edit to the `map` tab until the service restarted. `sheet_mapping.ensure_default_map_tab()` (called from `imports.seed.rebuild_config_from_sheets` after the catalog-side transaction commits) lays down a default `map` tab on first boot: the module-private `_TAG_RULES` list (`services/sheet_mapping.py`) provides the generic tag → envelope rules, and `_CATEGORY_ENVELOPES` provides per-category envelope overrides for the handful of categories whose Конверт is not the resolver's default empty string (currently `"гигиена"` and `"ЗОЖ"`). Pure identity rows (`Расходы = category.name`, `Конверт = "*"`) are deliberately **not** emitted — the resolver's no-rule-matched fallback in `resolve_projection` / `logging_projection` already substitutes `category.name` for a missing `sheet_category`, so an identity row would only duplicate that fallback and bloat the tab. Both lists are filtered against the active catalog at generation time (any `_TAG_RULES` entry whose tag is not an active `tags.name`, or any `_CATEGORY_ENVELOPES` entry whose category is not an active `categories.name`, is skipped with a WARN rather than emitted), otherwise a post-rename taxonomy would produce a template that trips `MapTabError` on first read. Both `ensure_fresh()` and `reload_now()` resolve `settings.sheet_logging_spreadsheet` through `config.spreadsheet_id_from_setting()` before hitting Sheets/Drive, so an operator env value that is a full browser URL (rather than a bare spreadsheet ID) is accepted transparently.

### Sheet layout contract

The sheet logging worker writes to a flat-table layout (one tab holds **every year** of expenses):

| Column | Content |
| --- | --- |
| A | First day of the expense's month, written as `YYYY-MM-DD` with `USER_ENTERED` so Google stores a date serial. Google **displays** it as `"Apr-1"` etc. (year is dropped from the formatted view but kept in the underlying value). |
| B | Sum-formula in RSD — extended in place by `append_expense_atomic` (`=460+373+...`). |
| C | EUR conversion formula `=IF(H{r}="","",B{r}/H{r})`. |
| D, E | `sheet_category`, `sheet_group` resolved by the drain loop via `sheet_mapping` (fallback: `categories.name`, `''`). |
| F | Free-text comment, semicolon-separated when multiple expenses share a row. |
| G | Month number 1..12 (literal, no formula). Used for fast month-block scans. |
| H | Manual EUR↔RSD rate cell. The worker only writes here when it's empty (set-if-missing). |
| J | Last-key-only idempotency marker: the `client_expense_id` UUID of the most recent expense appended to this row, overwritten on every subsequent append. Read before each append to detect timeout-after-success retries. Bootstrap-imported historical rows carry `client_expense_id = NULL` but are never enqueued for logging (`enqueue_logging=False`), so the drain can treat "runtime row with NULL UUID" as a producer bug: it poisons the queue row rather than invent a synthetic non-UUID marker that would corrupt future duplicate detection. |

### Year-aware matching

Column G holds the month number only, so a naive month-only scan would collapse e.g. January 2026 and January 2027 into the same block — a 2027 expense would land on a 2026 row. The worker mitigates this with a separate `batch_get` of column A using `ValueRenderOption.UNFORMATTED_VALUE`: that returns the underlying date serial (or the original string for text-typed cells), which is decoded into a per-row year list (`years_by_row`). All matching helpers (`find_category_row`, `find_month_range`, `get_month_rate`, `_find_insertion_row`) accept this list together with `target_year` and constrain candidate rows by year.

When `ensure_category_row` inserts a new row, the worker splices the new year into `years_by_row` at the insert index so the post-insert helpers stay aligned with the refreshed grid. Without this splice, the rate-write step can either silently skip or land on another year's rate cell.

Cost: one extra `batch_get` of column A per drained expense on top of `get_all_values`. The periodic drain's attempts cap and inter-row sleep keep this well inside Sheets' default 60 reads/min quota.

### Idempotency marker (column J)

The append path is **at-least-once**: a Sheets API call may succeed on the server even if we never see the response (network timeout). On retry the queue row is still `pending`, so the next worker would otherwise add the same amount a second time. To close that hole, `append_expense_atomic` reads the current J value first and skips the entire write if it already equals the incoming `client_expense_id`. The formula extension, the comment append, the J overwrite, and the optional rate write all go in a single `batch_update`, so the only two observable post-states are "all updated" and "none updated" — which the next attempt handles correctly.

J is **last-key-only**: each successful append overwrites the previous marker with the new UUID. This bounds the cell size to one UUID regardless of how many expenses the row aggregates, at the cost of not being able to recover the full list of contributors from the sheet alone — which is fine because DuckDB, not Sheets, is the source of truth.

Queue rows whose underlying expense has `client_expense_id = NULL` are marked `poisoned` rather than falling back to a synthetic marker (e.g. the server PK). Writing a non-UUID marker into J would silently corrupt the duplicate-detection contract for every subsequent append to the same row — a later retry for a legitimate UUID expense would read J, not match, and append again, producing a double-write. Bootstrap-imported historical rows carry `client_expense_id = NULL` but are never enqueued (`enqueue_logging=False`), so reaching the drain with a NULL UUID only happens when a runtime path inserts a queue row without a UUID — a producer bug, not a normal state. The poisoned row stays on disk for audit; `list_logging_jobs` filters it out of subsequent drain iterations.

The drain reports a successful skip as `DrainResult.ALREADY_LOGGED` so the operational `appended` counter only reflects real new sheet writes.

### POST /api/expenses (consolidated contract)

Request shape (Phase 2 — 3D, id-based):

- `client_expense_id` — required, client-generated UUID (the PWA uses `crypto.randomUUID()`). Idempotency key. There is no server-side `expense_id`-to-client plumbing; the server's integer PK is internal.
- `category_id` — required integer FK into `categories`. Unknown id → `422 Unknown category_id`. Known-but-inactive → `422` for a truly-new POST, but accepted for an idempotent replay where the stored row has the same `category_id`. This keeps an offline PWA retry against a category that was deactivated after the original POST from being silently dropped.
- `event_id` — optional integer FK into `events`. Same unknown/inactive semantics as `category_id`; inactive events survive only on a replay whose stored `event_id` matches. The PWA only sends non-NULL when the operator explicitly picked an event from the dropdown (±30-day active window) or when the auto-attach affordance filled it in from an `auto_attach_enabled` event whose date range contains the expense date — untouched expenses go in with `event_id=NULL`.
- `tag_ids[]` — optional list of integer FKs into `tags`. Unknown ids → `422`; inactive ids survive only on a replay whose stored tag set is *exactly* the same set. Tag names are never sent over the wire.

When `event_id` is set, the server unions the event's `auto_tags` (JSON array of tag names resolved to active tag ids) into `tag_ids[]` before storage and before the idempotency comparison. The `POST /api/expenses` handler deduplicates the client-supplied ids (preserving order, first-seen wins) and appends the event's auto-tag ids in the order they were authored on `events.auto_tags` (`sheet_mapping.resolve_event_auto_tag_ids` returns the names in `dict.fromkeys(...)` insertion order, dropping unknown/inactive names with a WARN rather than silently). The canonical sort into a single `(id ASC)` tuple happens one layer down, in `duckdb_repo.insert_expense`, which is also where the idempotency comparison lives. End-to-end this means replay comparisons are order-independent: "client sent `[T1]`, auto-attached `[T2]`" and "client sent `[T2, T1]`, event auto-tags empty" both normalise to the same `(T1, T2)` tuple by the time they hit the `ON CONFLICT` compare. The bootstrap importer applies the same normalisation for historical rows via `_union_event_auto_tags` (`imports/expense_import.py`).
- `date`, `amount`, `comment` — as usual. `amount` is in `settings.app_currency`.
- `currency` — optional. When omitted (the PWA never sends it), the server defaults to `settings.app_currency` and writes `amount = amount_original` with identity FX. When set to something other than the app currency, the server converts via NBS at the expense date.

The server validates `(category_id, event_id, tag_ids[])` up front against the catalog tables (both presence and active-state) so an idempotent replay for an expense whose vocabulary has since been retired is distinguishable from a fresh attempt to use retired vocabulary. After validation, the handler delegates the whole dup/conflict decision to a single `INSERT ... ON CONFLICT (client_expense_id) DO NOTHING RETURNING id` in `duckdb_repo.insert_expense`. There is no happy-path pre-lookup — the ON CONFLICT path is itself the duplicate check, so exactly one piece of code decides "same UUID + same body = duplicate, same UUID + different body = conflict" (we intentionally avoid a second compare on the handler side to prevent it drifting from the storage-layer compare):

- **Fresh insert**: the `RETURNING id` comes back non-NULL. The same transaction also inserts `expense_tags` rows for each `tag_id` and, when `DINARY_SHEET_LOGGING_SPREADSHEET` is set, one `sheet_logging_jobs` row. `sheet_category` / `sheet_group` are always `NULL` for runtime rows — they're populated lazily by the drain loop from `sheet_mapping`.
- **Idempotent replay**: the `UNIQUE` conflict path re-reads the committed row and compares `(amount, amount_original, currency_original, category_id, event_id, comment, datetime, sheet_category, sheet_group, tag_ids)` against the request. Match → `200 duplicate`. Mismatch → `409 conflict` — same UUID + different data is treated as a client/data-corruption bug. The 409 `detail` string appends a compact per-column diff produced by `duckdb_repo.describe_expense_conflict` (e.g. `tag_ids: stored=[1,2] incoming=[1,2,3]`), so the common "narrow race" case — an operator edited `events.auto_tags` between the original POST and the PWA's offline-queue replay, which surfaces as a `tag_ids`-only diff — is distinguishable on the client from a real body-drift conflict without the PWA having to guess.

The response echoes only `(status, month, category_id, amount_original, currency_original, catalog_version)`; there is no server-assigned expense id in the body. The PWA consumes `catalog_version` to invalidate its catalog cache and skip unnecessary `GET /api/catalog` calls.

The handler runs the blocking DB + NBS work inside `asyncio.to_thread` so the single-worker event loop stays responsive under concurrent POSTs. The response body does **not** include a server-assigned expense id; it carries only the normalised echo of the request plus the integer `catalog_version` so the PWA can invalidate its cached category list. The handler never calls the Google API — the sheet-logging queue row is the only side effect beyond the ledger insert.

Concurrent POSTs sharing the same `client_expense_id` are handled inside `duckdb_repo.insert_expense`, not at the edge. DuckDB's `ON CONFLICT DO NOTHING` only absorbs conflicts with rows that are already **committed** at statement time; two cursors on the singleton connection racing through `asyncio.to_thread` can each see "no row yet" and both proceed. The loser then surfaces at the UNIQUE constraint — either while executing the INSERT (the winner's write is in-flight and their uncommitted snapshot holds the UNIQUE key) or while executing the COMMIT (the winner committed between our INSERT and our COMMIT). DuckDB is free to raise either `ConstraintException` or `TransactionException` at either of those points (the class depends on when in the transaction lifecycle it notices the conflict), so both points catch the pair and classify via `_is_unique_violation_of_client_expense_id`. Both recovery branches reduce to the same thing: ROLLBACK to clear the aborted cursor state, then drop through to the compare-outside-tx path against the winner's now-committed row — `200 duplicate` if the payload matches, `409 conflict` otherwise. There is no API-level lock; the UNIQUE constraint itself is the serialisation point, and the compare path is idempotent.

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
- Stores everything in DuckDB (single `data/dinary.duckdb` file).
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
  - This is also a **correctness** constraint, not just a memory one: DuckDB allows at most one writer per file across processes, and every write path — `POST /api/expenses`, NBS rate-cache population in `exchange_rates`, the periodic sheet-logging drain, `inv` operator tasks — targets the same `data/dinary.duckdb`. A second OS process (extra uvicorn worker, ad-hoc `python -c` script, or an `inv` task started against the running server) trips DuckDB's per-file write lock with `IO Error: ... is already opened in another process`. Single-worker keeps writes serialized through the event loop. `inv` tasks therefore run against a stopped server.
  - Within that single process the API is concurrent: FastAPI runs many `POST /api/expenses` handlers on the asyncio event loop and blocking DuckDB/gspread work goes to the default thread pool via `asyncio.to_thread`. The lifespan's periodic `drain_pending` task runs on the same loop (dispatched via `asyncio.to_thread` for the blocking portions). Safety relies on (a) the singleton DuckDB engine in `duckdb_repo` that opens the file once per process and hands out cursors via `get_connection()`, and (b) the optimistic `claim_token` in `claim_logging_job` — DuckDB's OCC turns a lost claim race into a clean `TransactionException`, which the drain treats as "another worker got this row". There is intentionally no external CLI for draining the queue — recovery is the lifespan task's job.
  - Cursors returned by `get_connection()` share the engine with every other cursor in the process but carry their own transaction state, so `BEGIN`/`COMMIT` on one cursor does not affect another. Tests reset the singleton explicitly (see `tests/conftest.py::_reset_duckdb_connection`) to avoid cross-test bleed.
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

### Phase 1: 3D ledger, idempotent ingestion, export-only Sheets (dinary-server) ✓ IMPLEMENTED (single-file reset, 2026-04)

Detailed plan: [phase1.md](phase1.md) (historical; frozen before the single-file reset — treat as context, not current documentation).

- Single `data/dinary.duckdb` file with the **3D classification schema** (`category`, `event`, `tags[]`) from day one. No per-year partitioning, no separate config DB. One migration stream at `src/dinary/migrations/0001_initial_schema.sql`.
- Catalog tables carry `is_active`; `inv import-catalog` mutates them in place (FK-safe), never wipes them, so historical ledger FKs stay valid.
- `import_mapping` + `import_mapping_tags` decompose legacy Google Sheets `(sheet_category, sheet_group)` pairs into 3D assignments for one-time bootstrap import. Runtime sheet logging uses the separate `sheet_mapping` / `sheet_mapping_tags` tables (sourced from the `map` worksheet tab) for 3D → 2D projection.
- **PWA contract (Phase 1, superseded by Phase 2)**: sent `client_expense_id` (UUID) + `category` (string) + `date` + `amount` + `comment`; `currency` optional (default `settings.app_currency`); `tag_ids[]`, `event_id`, and a server-side expense id in the response were explicitly **not** part of the Phase-1 surface. The current on-the-wire contract is the id-based Phase-2 shape documented in "POST /api/expenses (consolidated contract)" above — this bullet is retained only to record what Phase 1 actually shipped before the 3D + auto-tags migration.
- DuckDB-backed expense ingestion with idempotent dedup via `expenses.client_expense_id UNIQUE` plus a payload comparison on replay (resolved `category_id`, not raw label). Same UUID + matching payload → `200 duplicate`; same UUID + different payload → `409 conflict`.
- `expenses.amount` is stored in `settings.app_currency` (default `"RSD"`); `amount_original`/`currency_original` preserve the audit value. Historical imports convert to the app currency via RSD-anchored NBS rates.
- Historical data import (2012–present) imported via destructive `inv import-budget` / `inv import-budget-all` and verified via `inv verify-bootstrap-import` / `inv verify-bootstrap-import-all`. Bootstrap-imported rows populate `sheet_category` / `sheet_group` as audit provenance and carry `client_expense_id = NULL`.
- Google Sheets is **export-only**: the `sheet_logging_jobs` queue plus a single lifespan-managed periodic `drain_pending` sweep performs single-row appends with a last-key-only J marker. A circuit breaker handles transient Sheets failures; permanent errors are parked as `poisoned`. No inline API-handler worker, no full-month rebuild, no DB-to-sheet reconciliation.
- Monotonic `catalog_version` (KV row in `app_metadata`) bumped by `inv import-catalog` and by the Admin API via `catalog_writer` (hash-gated); echoed by `GET /api/catalog` and `POST /api/expenses`. The PWA uses it as the cache key for its in-memory 3D catalog snapshot.
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

- **Cross-year events**: events (e.g. a trip) can span a year boundary (start in December, end in January). With the single-file model this is trivially just `SELECT ... WHERE event_id = ?` — no ATTACH, no union. The open question is whether reporting should default to the event's full span or to a calendar-year slice for year-over-year comparisons; the data model imposes no constraint either way.
- **Archiving cold years**: the single-file model removed the ability to physically detach an old year, which was a stated motivation for the per-year layout. For the projected dataset size this is not a capacity issue, but if a future use case needs to freeze / audit a specific year, the expected answer is a `COPY ... TO '<year>.parquet' (FORMAT parquet)` dump plus a ranged `DELETE`, not re-introducing per-year DB files.
- **`categories.sheet_name` / `categories.sheet_group`**: these columns are declared in the initial schema as a hook for a future receipt-ingestion pipeline that projects raw-receipt classifications directly into the logging sheet without going through `sheet_mapping`. They are still unreferenced by the drain worker (which consults `sheet_mapping` first, falling back to `categories.name`); revisit in a later phase whether to project them into the `map` tab defaults.

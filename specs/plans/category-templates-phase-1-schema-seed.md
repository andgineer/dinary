# Phase 1 — Schema, template storage & seed

Foundation: DB schema for codes/visibility/templates, the YAML loader, and a
clean idempotent seed (fresh + reconcile + the one-off adopt-existing mode).
See `category-templates.md` for the decided model. **Do not** touch
`seed_classification_catalog`'s logic in `tasks/imports/seed_config.py` — it
stays exactly as it is, a manual recovery/import toolkit for the rare future
"forgot to import X from the Sheets" case. What *does* change is which function
gets invoked to populate a fresh catalog — see §5.

## 1. Migration `0006_category_templates.py`

yoyo migration under `src/dinary/db/migrations/` (next free number is 0006).
Run inside the migration's own transaction; `PRAGMA foreign_keys` is ON.

### 1a. Columns on existing catalog tables
- `categories`: add `code TEXT`, `is_hidden BOOLEAN NOT NULL DEFAULT 0`,
  `is_retired BOOLEAN NOT NULL DEFAULT 0`. Repurpose existing `is_active` to mean
  "in the active template's visible subset" (no DDL change, semantic only).
- `category_groups`: add `code TEXT`, `sort_order` already exists.
- `tags`: add `code TEXT`.
- `code` left nullable here; backfilled by seed (step 4) before the unique index
  is relied on. Create the unique indexes now, they tolerate NULLs in SQLite:
  `CREATE UNIQUE INDEX ux_categories_code ON categories(code);`
  `CREATE UNIQUE INDEX ux_category_groups_code ON category_groups(code);`
  `CREATE UNIQUE INDEX ux_tags_code ON tags(code);`

### 1b. Drop the `name` UNIQUE constraints
`name` becomes a per-template baked label and may legitimately repeat (e.g. a
custom category vs a hidden factory one). The inline `name TEXT UNIQUE` on
`categories` and `category_groups` must go. SQLite can't drop an inline
constraint in place → table rebuild **inside the migration**, preserving
`id` so all FKs (`expenses.category_id`, `import_mapping.category_id`,
`sheet_mapping.category_id`) stay valid:
1. `PRAGMA foreign_keys=OFF;` — write this migration as a Python yoyo function (not
   a raw SQL file) so all statements share one connection and the PRAGMA persists
   for the full rebuild. This is the project's first Python migration (0001-0005
   are raw `.sql`/`.rollback.sql` pairs) — a deliberate one-off: it is applied
   once, manually, to the single personal dev DB (no other installation exists
   yet, and the migration stream will likely be squashed into one before any
   public release), so it doesn't need to fit the unattended-startup mould the
   SQL convention serves elsewhere.
   Verify early that the PRAGMA toggle actually takes effect: SQLite treats
   `PRAGMA foreign_keys` as a no-op inside an open transaction, and the project's
   custom yoyo backend (`SQLiteBackend.begin()` in `db/db_migrations.py`) opens
   one with `BEGIN IMMEDIATE` before running migration steps. If the toggle turns
   out to be a no-op here, mark the step `transactional = False` and wrap the
   rebuild in an explicit `BEGIN`/`COMMIT` placed around the PRAGMA instead.
2. `CREATE TABLE categories_new (... same cols incl. new ones, name TEXT NOT NULL
   without UNIQUE ...);`
3. `INSERT INTO categories_new SELECT ... FROM categories;`
4. `DROP TABLE categories; ALTER TABLE categories_new RENAME TO categories;`
5. recreate indexes (`ux_categories_code`, the partial visibility helpers below).
6. repeat for `category_groups`.
7. `PRAGMA foreign_key_check;` then `PRAGMA foreign_keys=ON;`
- No rollback migration: once `apply_template` has run, `categories.name` may
  contain duplicates (different templates can bake identical labels for different
  codes), so restoring the `name UNIQUE` constraint would fail. Roll back by
  restoring from a DB backup taken before the migration. There is exactly one
  installation — the personal dev DB — so `tasks/deploy.py:_downgrade_if_needed`'s
  automated `yoyo rollback` path (which every prior migration feeds with a
  `.rollback.sql`) is never exercised for 0006; its absence here is intentional,
  not an oversight.

### 1c. Index for the `used` predicate
`CREATE INDEX ix_expenses_category_id ON expenses(category_id);` — makes the
`LEFT JOIN (SELECT DISTINCT category_id FROM expenses)` in Phase 2 cheap.

### 1d. Template-definition storage
- `CREATE TABLE category_sets (
     id INTEGER PRIMARY KEY,
     code TEXT NOT NULL UNIQUE,
     origin TEXT NOT NULL CHECK (origin IN ('factory','custom')),
     sort_order INTEGER NOT NULL DEFAULT 0,
     definition_json TEXT NOT NULL
   );`
  `definition_json` holds the parsed YAML for that set serialized via
  `json.dumps(sort_keys=True)`: `names`, `taglines`
  (per-lang short onboarding blurb), `groups` (code→names), optional `renames`,
  `visible`, `hidden`. Rationale: definitions
  are read only at apply time, never at render time (Phase 2 renders from baked
  live rows), so a JSON blob per set is faithful and far simpler than decomposing
  into 5 relational tables. The custom "My setup" set is just another row with
  `origin='custom'`.
- `CREATE TABLE category_translations (
     code TEXT NOT NULL, lang TEXT NOT NULL, name TEXT NOT NULL,
     PRIMARY KEY (code, lang)
   );`
  Default per-language category names from `categories.yml`. Used to bake
  `categories.name` at apply / language change without re-reading files.
  No FK to `categories.code` — intentional: orphaned rows for retired codes are
  never read and harmless, avoiding cascade complexity.

### 1e. Onboarding state
- `active_template` lives in `app_metadata` (key/value). The migration does **not**
  insert it — its absence means "no template selected" and is what triggers the
  PWA chooser. Seed (fresh) leaves it absent; apply (Phase 2) sets it.

## 2. YAML loader — `src/dinary/category_templates/loader.py`
- Read package resources via `importlib.resources.files("dinary.category_templates")`
  (mirror `db/sql_loader.load_sql`). `pyyaml` is already a dependency.
- File extension convention: `categories.yml` (`.yml`) is the vocabulary;
  template files use `.yaml`. The difference is intentional — `load_templates`
  globs `*.yaml` and the vocabulary is never matched.
- `load_vocabulary() -> dict[str, dict[str, str]]` — parse `categories.yml`
  (`code → {lang: name}`).
- `load_templates() -> list[Template]` — parse every `*.yaml` sorted
  alphabetically by filename (all template files; `categories.yml` uses `.yml`
  so it is never matched); return frozen dataclasses (`code` — read from the
  file's `id:` field, already present in all four shipped templates and matching
  their filenames — `names`, `taglines`, `groups`, `renames`, `visible`, `hidden`).
  Prerequisite: none of the four shipped files has a `taglines:` key yet — add a
  per-language tagline (same language set as `names`) to each before this loader
  can parse them as planned.
- `validate(vocabulary, templates)` — port the coverage check already run by hand:
  every template's `visible`+`hidden` equals the vocabulary key set exactly (no
  dupes/missing/unknown), every referenced group is declared; all templates share
  the same set of language keys in `names` (Phase 4 derives the available language
  list from the first template's key set — any mismatch would silently break the
  onboarding language selector). Raise on failure.

## 3. Code namespaces
- Factory codes = the YAML slugs (e.g. `groceries`).
- Custom/user codes = prefixed `u_` (e.g. `u_my_thing`) so seed can tell them
  apart and never reconcile/retire them. Document the prefix as the namespace
  boundary.

## 4. Seed — `src/dinary/db/category_seed.py` (clean, new)
All functions take an open `sqlite3.Connection`, run under `storage.transaction` —
except `migrate_personal_catalog`, which wraps only its own backfill SQL to avoid
nesting when it calls the other two (see its transaction-boundary note below).

- `seed_category_templates(con)` — fresh / reconcile (idempotent when files
  unchanged):
  1. Upsert `category_translations` from `load_vocabulary()` (by `(code,lang)`).
  2. Ensure a `categories` row per factory code: insert if missing with
     `is_active=0, is_hidden=0, is_retired=0`, `name` = default-language name,
     `group_id` = NULL (assigned on apply). Update `code`/restore `is_retired=0`
     for re-appearing codes. Never touch rows whose `code` starts `u_`.
  3. Ensure a `category_groups` row per group code used across templates (union);
     `name` = name from the first template in `load_templates()` order that declares
     this group (groups have no canonical name outside template files; `apply`
     re-bakes the correct per-template name anyway); `code` set.
  4. Upsert `category_sets` (one row per factory template) with
     `origin='factory'`, `definition_json` = the parsed template.
  5. **Retire vanished factory codes:** any `categories` row with a non-`u_`
     `code` absent from the current vocabulary → `is_active=0, is_retired=1`
     (kept for history; see Phase 2 visibility). SQL filter must be explicit:
     `WHERE code IS NOT NULL AND code NOT LIKE 'u_%' AND code NOT IN (...)` —
     rows with `code IS NULL` must be excluded (SQLite `NOT IN` with NULL returns
     NULL rather than FALSE, but the explicit guard makes the intent clear).
     Same idea for factory
     `category_sets` rows whose file disappeared → delete the set row (no FK to
     expenses); if the deleted set's code equals `app_metadata.active_template`,
     also clear `active_template` so the PWA falls back to the onboarding chooser.
  6. Do **not** set `app_metadata.active_template`.
  - "default language" = a module constant (start with `ru`, matching the
    existing personal data); apply re-bakes visible names for the chosen
    language anyway.

- `migrate_personal_catalog(con)` — in `db/category_seed.py` alongside
  `seed_category_templates`. One-off personal function; hardcoded maps specific
  to the current live DB. **Called automatically by bootstrap** (see section 5) —
  no manual invocation needed. Guard: if any `categories.code IS NOT NULL` exists,
  return immediately — already done.
  Transaction boundary: `storage.transaction` is a bare `BEGIN IMMEDIATE` /
  `COMMIT` with no savepoint nesting (`storage.py:322-331`), so this function must
  not wrap its whole body — that would nest inside the transactions opened by
  steps 3 and 4 below and raise "cannot start a transaction within a transaction".
  Wrap only steps 1-2 (the name backfill, where the `ValueError` must fire before
  any write — that is the real no-partial-state boundary) in one
  `storage.transaction`; steps 3 and 4 each open and commit their own via
  `seed_category_templates` / `apply_template` and run sequentially afterward.
  Steps:
  1. Backfill `categories.code` by name. Raise `ValueError` listing every
     unrecognised name before touching the DB (no partial state):
     ```python
     CATEGORY_MAP = {
         "алкоголь": "alcohol",      "деликатесы": "delicacies",
         "еда": "groceries",          "фрукты": "fruit",          "кафе": "cafe",
         "интернет": "internet",      "коммунальные": "utilities",
         "мобильник": "mobile",       "сервисы": "subscriptions",
         "аренда": "rent",            "бытовая техника": "appliances",
         "мебель": "furniture",       "ремонт": "repairs",        "хозтовары": "household_goods",
         "обучение": "education",     "продуктивность": "productivity",
         "ЗОЖ": "wellness",           "гигиена": "hygiene",
         "лекарства": "pharmacy",     "медицина": "doctor",
         "карманные": "pocket_money", "одежда": "clothing",       "подарки": "gifts",
         "велосипед": "cycling",      "лыжи": "skiing",           "спорт": "sport",
         "машина": "car",             "топливо": "fuel",          "транспорт": "transit",
         "гаджеты": "gadgets",        "инструменты": "tools",
         "развлечения": "entertainment", "электроника": "electronics",
         "налог": "tax",              "штрафы": "fines",
     }
     ```
  2. Backfill `category_groups.code` by name. Raise `ValueError` on any
     unrecognised group name:
     ```python
     GROUP_MAP = {
         "Государство": "government", "Еда": "food",
         "ЖКХ и сервисы": "utilities", "Жильё": "housing",
         "Знания и продуктивность": "growth", "Красота и ЗОЖ": "beauty",
         "Медицина": "health",        "Семья и личное": "personal",
         "Спорт": "sport",            "Транспорт": "transport",
         "Хобби и отдых": "hobbies",
     }
     ```
  3. Call `seed_category_templates(con)` — loads full factory vocabulary + all four
     templates; existing rows (now with factory codes) are reused, not duplicated.
  4. Call `apply_template(con, "active", "ru")` — bakes Russian names from
     `active.yaml` onto categories and groups; sets `active_template = "active"`.
     Group names in the result: Еда, Жильё, ЖКХ и сервисы, Медицина,
     Красота и ЗОЖ, Спорт, Хобби и отдых, Транспорт, Знания и продуктивность,
     Семья и личное, Государство, Питомцы (новая).

## 5. Startup wiring & invoke tasks
- **`bootstrap_categories(con)` runs on every app boot** — call it from
  `main.py:_lifespan`, right after `storage.init_db()`. Nothing seeds the catalog
  automatically at startup today (the only catalog-population paths are the
  manual `inv bootstrap-catalog` and `inv dev --reset`), so without this wiring a
  genuinely fresh production DB would boot with empty `categories` /
  `category_sets` tables and the Phase 4 onboarding chooser would have nothing to
  show. Running it on every boot also covers the reconcile branch automatically
  whenever a `categories.yml` / template file changes — mirroring how migrations
  already run on every startup (`db/migrations/README.md`).
- Add to `tasks/` (mirror existing `inv` tasks): `inv seed-categories` →
  `init_db()` then `bootstrap_categories` — a manual entry point for ad-hoc
  reseed/reconcile without restarting the service (mirrors `inv migrate`).
- **Replace the `bootstrap_catalog()` call in `inv dev --reset`**
  (`tasks/devtools/dev.py:140-144`, currently
  `from tasks.imports.seed_config import bootstrap_catalog`) with
  `bootstrap_categories` — a freshly reset local DB now gets only the category
  catalog (vocabulary + factory template definitions), matching what a fresh
  production boot gets.
- **Tags and events are no longer auto-seeded — intentional.**
  `seed_classification_catalog` / `bootstrap_catalog`
  (`tasks/imports/seed_config.py:316-475`) populates `category_groups` +
  `categories` + `tags` + `events` together as one hardcoded taxonomy — that
  bundling exists because the Google-Sheets import needed the whole runtime
  vocabulary in place before `_rebuild_import_mapping` could resolve names to
  ids. That import job is done; `seed_classification_catalog` stays untouched as
  a manual recovery tool for the rare future "forgot to import X" case
  (`inv bootstrap-catalog --yes`, `inv import-catalog --yes`), but it drops out of
  the standard fresh-DB path. A fresh install now gets its category catalog from
  the chosen template (onboarding + `apply_template`) and starts with **empty**
  `tags` / `events` tables — the user grows those organically.
- `bootstrap_categories(con)` selects the right branch automatically:
  ```python
  def bootstrap_categories(con):
      has_rows = con.execute("SELECT 1 FROM categories LIMIT 1").fetchone()
      has_null_code = con.execute("SELECT 1 FROM categories WHERE code IS NULL LIMIT 1").fetchone()
      if has_rows and has_null_code:
          migrate_personal_catalog(con)   # non-empty DB with at least one NULL code → personal migration
      else:
          seed_category_templates(con)    # empty DB → fresh seed; or all codes present → reconcile
  ```
  After `migrate_personal_catalog` the DB has codes and `active_template = "active"`,
  so re-runs hit the guard in `migrate_personal_catalog` and exit immediately.

## 6. Tests (`tests/...`, same session)
- `tests/category_templates/test_loader.py` — parse + the coverage validator
  (the 4 shipped templates must validate; a broken fixture must raise).
- `tests/category_templates/test_seed.py` — fresh seed inserts vocabulary +
  4 sets, no active template; re-run is a no-op; removing a code from a fixture
  vocabulary retires (not deletes) its row and keeps an expense FK valid; `u_`
  rows survive a reconcile.
- `tests/category_templates/test_migrate_personal_catalog.py` — uses a test DB
  seeded with exactly the current personal categories and groups (without codes,
  mirroring the real DB state before the migration). Note: these tests require
  `apply_template` from `db/category_apply.py` (Phase 2), because
  `migrate_personal_catalog` calls it in step 4. Both modules are implemented in
  the same change.
  - All 35 categories in `CATEGORY_MAP` get the correct factory code after migration.
  - All 11 groups in `GROUP_MAP` get the correct factory code after migration.
  - `app_metadata.active_template = "active"` after migration.
  - Group names are Russian (spot-check: "Еда", "ЖКХ и сервисы", "Спорт").
  - Guard: second call to `migrate_personal_catalog` returns without DB changes.
  - `bootstrap_categories` on the same pre-migration fixture calls
    `migrate_personal_catalog` (not `seed_category_templates`); on a fresh empty
    DB it calls `seed_category_templates` instead.
  - Validation: a DB with an unknown category name raises `ValueError` before any
    writes (no partial state left).
- Migration test alongside `tests/ledger/test_migrations.py`: 0006 applies cleanly;
  FKs intact (`PRAGMA foreign_key_check`). No rollback step is implemented — to
  revert, restore from a pre-migration DB backup (see section 1b).

## Done gate
`uv run inv pre` (0 pyrefly errors) + `uv run pytest` green before moving on.

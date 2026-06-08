# Phase 2 — Backend domain: apply, visibility reads, category ops

Depends on Phase 1 (schema, seed, definitions). All query SQL goes in
`src/dinary/db/sql/` with `AS` aliases and is mapped via
`db/sql_loader.fetchall_as`/`fetchone_as`; row dataclasses in `db/storage.py`.
Write logic in clean modules, run mutations under `storage.transaction`.

## 1. Apply a template — `src/dinary/db/category_apply.py`
`apply_template(con, template_code: str, lang: str) -> None`:
1. Load the template: `SELECT definition_json FROM category_templates WHERE code = ?`; parse.
2. Resolve each group code in the template's `groups` to a `category_groups.id`
   (created in Phase 1 seed); bake `category_groups.name` = template's group
   name[lang] and set `sort_order` from the template's group order.
3. For **every** category in `visible` ∪ `hidden`:
   - `group_id` = the resolved group it sits in for this template;
   - `is_active` = 1 if in `visible` else 0;
   - `name` = `renames[code][lang]` if present else `category_translations[code][lang]`
     (fallback to the `ru` default, then the code itself);
   - **leave `is_hidden` and `is_retired` untouched.**
   Single `UPDATE ... WHERE code = ?` per category (FK-safe; `id` untouched).
   User-created categories (`code` starting `u_`) are absent from every template's
   `visible ∪ hidden` and are therefore skipped by apply — their `is_active`,
   `group_id`, and `name` remain unchanged.
4. `set_catalog_version(con, get_catalog_version(con) + 1)` (reuse
   `db/catalog.py`) and `UPDATE/INSERT app_metadata.active_template = template_code`.
- Whole thing in one `storage.transaction`. Bumping `catalog_version` invalidates
  the PWA cache (existing ETag mechanism in `api/catalog.py`).
- Note: apply rewrites `is_active`/`group_id` wholesale, so switching templates
  re-themes membership; survivors stay visible via the `used` term (step 2 below)
  without special-casing.

## 2. Visibility reads — `db/catalog.py` + new SQL
Predicate (decided): **shown = `(is_active OR used) AND NOT is_hidden AND NOT is_retired`**,
`used = EXISTS expense`.

- `sql/list_visible_categories.sql` — render the picker, grouped:
  ```sql
  SELECT c.id AS id, c.code AS code, c.name AS name,
         c.group_id AS group_id, g.name AS group_name,
         g.sort_order AS group_sort_order
  FROM categories c
  JOIN category_groups g ON g.id = c.group_id
  LEFT JOIN (SELECT DISTINCT category_id FROM expenses) u
         ON u.category_id = c.id
  WHERE NOT c.is_retired AND NOT c.is_hidden
        AND (c.is_active OR u.category_id IS NOT NULL)
  ORDER BY g.sort_order, c.name
  ```
  Intra-group ordering is alphabetical by `c.name` — intentional; `categories`
  has no per-category `sort_order` column.
  The `JOIN category_groups` is intentionally INNER: a category with
  `group_id=NULL` (activated without an active template, per the edge case in
  `activate_category`) satisfies `is_active=1` but is excluded from this query
  and appears only in search results — handled by Phase 4. The same applies
  before any `apply_template` is called: on a fresh seed all categories have
  `group_id=NULL` and `is_active=0`, so this query returns an empty list; the
  Phase 4 onboarding guard ensures the chooser runs before any category-dependent
  view is reachable.
  Add a new `VisibleCategoryRow` dataclass in `db/storage.py` (do not modify
  `CategoryListRow` — updating it would require changing all existing consumers).
- `sql/search_categories.sql` — for activation, search across ALL non-retired
  (incl. hidden / not-in-set):
  ```sql
  SELECT c.id AS id, c.code AS code, c.name AS name, c.is_active AS is_active,
         c.is_hidden AS is_hidden
  FROM categories c
  WHERE NOT c.is_retired AND c.name LIKE '%' || ? || '%'
  ORDER BY c.is_active DESC, c.name
  ```
  (LIKE is enough at this scale; FTS5 is a later optimisation if needed.)
- Functions in `db/catalog.py`:
  - `list_visible_categories(con) -> list[...]`
  - `search_categories(con, query) -> list[...]`
  - `get_active_template(con) -> str | None` (reads `app_metadata.active_template`)
  - `activate_category(con, code)` — `is_active=1, is_hidden=0`. **Deliberately
    clears `is_hidden`, in tension with — but not violating — the "sticky,
    user-owned, apply never touches it" framing in `category-templates.md`:**
    that framing constrains *automatic* writers (`apply_template`, seed); a
    direct, explicit user action ("find this and turn it on") is the one case
    where overriding a stale hide is the expected outcome — mirroring how
    Phase 4 §3 routes "select a hidden result from search" through this exact
    function (one tap = un-hide + activate + select). `unhide_category` stays
    the no-side-effect primitive for "just bring it back without selecting it".
    If `group_id`
    is NULL, place it using the active template's definition (read
    `app_metadata.active_template`, load `category_templates.definition_json`
    for that code, resolve to a `category_groups.id`);
    if there is no active template or the code is absent from the definition, leave
    `group_id=NULL` — the category stays invisible in grouped views until apply or
    a manual move; bump `catalog_version`.
  - `hide_category(con, code)` / `unhide_category(con, code)` — toggle `is_hidden`;
    bump `catalog_version`. `unhide` does not set `is_active`; if the category is
    also inactive and has no expenses it remains invisible in
    `list_visible_categories` — the user must activate it explicitly.
  - `move_category(con, code, group_code)` — set `group_id` (manual override);
    bump `catalog_version`. Raise `ValueError` if `group_code` not found in
    `category_groups`.
  - `create_category(con, name, group_code) -> str` — the missing primitive for
    `category-templates.md`'s "add (**new** user-code or reuse existing by
    search → activation)" decision; `activate_category` only covers the "reuse
    existing" half. Generates a fresh `u_`-prefixed code by slugifying `name`
    (lowercase, non-alphanumeric → `_`, collapse repeats) and appending a
    numeric suffix on collision (`u_my_thing`, `u_my_thing_2`, …) so the
    `ux_categories_code` unique index never raises; resolves `group_code` to
    `group_id` (raise `ValueError` if not found — same contract as
    `move_category`); inserts the row with `is_active=1, is_hidden=0,
    is_retired=0`; bumps `catalog_version`; returns the generated `code`. This
    is the direct sibling of `move_category` for the "brand new category" path
    that Phase 4 §4's "migrate add UI to code-based ops" otherwise has nothing
    to migrate *to* — `add_category` (`catalog_writer_categories.py`) is
    id/name-keyed and slated for removal with no described replacement.
  - `rename_category(con, code, name)` — set `name` only (`code` stable); bump
    `catalog_version`. The code-based, label-only sibling that lets Phase 4
    retire `edit_category`'s id-based `PATCH /api/catalog/categories/{id}`
    entirely — that endpoint also takes `group_id`/`is_active`, which now belong
    to `move_category`/`activate_category`/`apply_template` and must not be
    reachable through a second, validation-free path into the same columns.

## 3. Wire visibility into existing consumers
The LLM classifier and POST validation must use the **visible** set (decided).

**Correction to the discovery method:** `db.catalog.list_categories`
(`db/catalog.py:22`) has exactly **one** production caller today —
`tasks/receipt.py:41` (the `inv classify-receipt` operator tool). Neither the
classifier nor `GET /api/catalog` goes through it; both run their *own* inline
`… WHERE c.is_active = 1` SQL. So a grep for `list_categories\b` will **not**
surface the two places that most need the predicate swap, and **will** surface
a real caller (`tasks/receipt.py`) that's easy to forget.

**This cuts both ways — don't trust any single grep pattern to be exhaustive.**
Run `rg "is_active" | grep -i categor` (or equivalent) across `src/` *and*
`tasks/` (not just `src/`) before starting; it surfaces several more inline
`categories.is_active` / `category_groups.is_active` reads that belong on this
list and are easy to miss because none of them go through `list_categories` or
`load_categories` either:
- `background/classification/task.py:384,409` —
  `_load_top_fallback_categories` (`SELECT COUNT(*) … WHERE is_active = 1` for
  the `InsufficientCategoriesError` threshold, and `SELECT id FROM categories
  WHERE is_active = 1 …` to pad the LLM's fallback category list when rule hits
  run short). This **is** the LLM classifier's category source for the fallback
  path — squarely inside "the LLM classifier … must use the visible set" —
  yet it's a separate inline query from `receipt_classifier.load_categories`
  and must get the same predicate swap.
- `sheets/sheet_mapping.py:585` (`ensure_default_map_tab`) —
  `WHERE c.is_active AND g.is_active` when generating the default Google-Sheets
  mapping-tab template (the list of category names offered for the user to map
  sheet rows onto). Swap to the visibility predicate so the generated tab
  reflects the active template's set, not a stale "globally active" notion.
  While there, revisit `_load_catalog`'s docstring in the same file
  (`sheet_mapping.py:266-275`) — it explicitly documents the *old* meaning of
  `is_active` ("purely a 'hide from the ручной пикер' affordance … must not
  break the mapping reload"); that statement becomes wrong once `is_active`
  means "in the active template's visible subset" and should be corrected to
  describe the new predicate (the underlying behaviour — load every row by name
  regardless of flags — stays correct and still doesn't need to change).
- `api/controllers/expense_corrections.py:101` —
  `SELECT id FROM categories WHERE id = ? AND is_active = 1` validates
  `category_id` for expense corrections (422 "Unknown or inactive category_id"
  otherwise). This is the direct sibling of `_resolve_category_for_write`
  below and needs the same treatment — retired/hidden → 422, `used`-but-
  currently-inactive → activate-on-use — otherwise correcting an expense onto a
  category that fell out of the active template (but has history) is rejected,
  contradicting the decided "history stays editable" intent.
- `api/controllers/rules.py:154` —
  `SELECT id FROM categories WHERE id = ? AND is_active = 1` validates
  `category_id` when creating/editing a classification rule. Swap to the
  visibility predicate (`(is_active OR used) AND NOT is_hidden AND NOT
  is_retired`) so a rule can still reference a `used` category that the current
  template hides — otherwise switching templates would make existing rules
  un-editable (and new rules for legitimately-used-but-hidden categories
  impossible to create).

Update each of the following directly — do not rely on the grep alone:
- `background/classification/receipt_classifier.load_categories`
  (`receipt_classifier.py:52-59` — its own inline
  `SELECT c.id, cg.name, c.name FROM categories c LEFT JOIN category_groups cg …
  WHERE c.is_active = 1`, *not* a call to `db.catalog.list_categories`) — the
  classifier's allowed category list; replace the `WHERE` clause with the
  visibility predicate (or rewrite `load_categories` to call
  `list_visible_categories` and reshape its rows into the same
  `dict[int, str]` it returns today).
- `tasks/receipt.py:41` (`inv classify-receipt`, the operator debug tool and
  the *only* current production caller of `db.catalog.list_categories` — it
  builds the same `{id: "group: name"}` map fed to the LLM as the production
  classifier) — switch it to `list_visible_categories` so the manual debug path
  matches what production classification actually sees. Once this caller is
  migrated, `db.catalog.list_categories` (and `sql/list_categories.sql`) has no
  remaining production callers — remove both as dead code rather than leaving
  an unused wrapper around the superseded `is_active = 1` query, and remove
  their test coverage **precisely**, not file-by-file:
  - `tests/ledger/test_ledger_repo_catalog.py` bundles `TestListCategories`
    alongside `TestConnectionLifecycle`, `TestCatalogVersion`, `TestSheetMapping`
    (covers `resolve_mapping_for_year`) and `TestGetCategoryName` — all four of
    those test other still-live functions in `db.catalog`. Remove only the
    `TestListCategories` class and the now-unused `list_categories` import;
    leave the rest of the file intact.
  - `tests/ledger/test_ledger_repo_logging_projection.py` is **not** about
    `list_categories` at all — it is the dedicated suite for
    `db.catalog.logging_projection` (a separate, very much live function behind
    the sheet-logging pipeline); its one mention of `list_categories` is a
    cross-reference in a docstring pointing at its sibling module. Do **not**
    touch this file.
  - `tests/services/test_sql_loader.py` — remove only the `"list_categories.sql"`
    case, as originally stated.
- POST `/api/expenses` category validation — rewrite `_resolve_category_for_write`
  (`api/controllers/expenses.py:423-434`, **already** 422 for both its existing
  "Unknown" / "Inactive" branches — the change is the *conditions* that trigger
  which response, not the status code, and the result keeps matching the
  convention its siblings `_validate_event` / `_validate_tags` already use in
  the same file): if `is_retired` or `is_hidden`, raise 422 (`Retired category_id: …` /
  `Hidden category_id: …`) **unless `_is_replay`** — keep the existing replay
  exception so an idempotent resubmission of a previously-accepted expense isn't
  rejected just because its category was hidden/retired in the meantime; if
  inactive-but-not-hidden-and-not-retired, call `activate_category` —
  activation-on-use keeps a used category visible per the predicate (this also
  subsumes today's bare-inactive-category replay branch, since activation always
  succeeds for a non-hidden, non-retired code).
- `frequent_categories` / category-defaults flow — three `c.is_active = 1`
  /`g.is_active = 1` filters in `api/controllers/catalog.py` all need to move to
  the visibility predicate (`(is_active OR used) AND NOT is_hidden AND NOT is_retired`),
  not just one:
  - `_SQL_CAT_DEFAULTS` (`catalog.py:237`, `c.is_active = 1`) — feeds
    `most_used_category_per_group`, wired into POST `/api/expenses` defaults via
    `api/controllers/expenses.py:181`.
  - `_SQL_GROUP_DEFAULT` (`catalog.py:245`, `WHERE g.is_active = 1`) — drop this
    filter outright: `apply_template` rewrites every category's `group_id` to a
    group the active template declares, so a visible category can never resolve
    to a group outside it — `category_groups.is_active` becomes vestigial for
    this flow. Leave the column itself alone; it still backs the existing admin
    group CRUD in `catalog_writer_groups.py`, which is untouched and out of scope
    here. Before relying on "vestigial except for admin CRUD", grep
    `rg "category_groups\.is_active|cg\.is_active|g\.is_active"` to confirm —
    `build_catalog_snapshot` (`api/controllers/catalog.py:283-306`) also reads
    and returns `category_groups.is_active` for the admin snapshot; that read
    is fine to leave (same "leave `GET /api/catalog` as-is" reasoning below),
    but it should be accounted for explicitly rather than assumed away.
  - `frequent_categories_sync` (`catalog.py:254`, `c.is_active = 1`) — same
    predicate swap; also used by `stores/frequentCategories.js` on the PWA side.
- `analytics_auto_trends.sql` (`db/sql/analytics_auto_trends.sql:15,18`) — drop
  the `c.is_active = 1` / `cg.is_active = 1` filters entirely. They join
  `categories`/`category_groups` onto real `expenses` rows from the last six
  months to compute spending trends; under the repurposed `is_active`
  ("in the active template's visible subset"), a category that fell out of the
  active template but still has fresh expenses (`used = true`) would silently
  vanish from trend analytics the moment the user switches templates — directly
  contradicting the decided rule that "analytics / history read expenses
  directly … regardless of any flag" (`category-templates.md`). The join already
  scopes to categories that actually have matching expenses; no replacement
  filter is needed.
- `GET /api/catalog` (`api/controllers/catalog.py:build_catalog_snapshot`,
  lines 277-289) — runs its own inline
  `SELECT c.id, c.name, c.group_id, g.name, c.is_active FROM categories c
  JOIN category_groups g …` query for the admin snapshot (again, *not*
  `list_categories` — there is no `list_categories` call to except here).
  Leave it untouched **and do not plan to remove it**: `build_catalog_snapshot`
  is not a category-only endpoint — it assembles `category_groups`, `categories`,
  `events`, `tags` and `frequent_categories` into one admin payload, and
  `webapp/src/api/catalog.js` drives the existing `/api/catalog/groups`,
  `/api/catalog/events`, `/api/catalog/tags` admin CRUD off it. `GET /api/categories`
  (Phase 3) only ever returns the *visible* category list for the picker — it
  cannot stand in for this snapshot. Phase 4 migrates the **picker-facing**
  consumers (`CategorySheet.vue` / `CatalogSelectField.vue` / the catalog store's
  category data) from this endpoint to `GET /api/categories`; the snapshot
  endpoint itself, and the groups/events/tags admin surfaces it backs, stay.

## 4. Tests (same session)
- `tests/category_templates/test_apply.py` — applying a template bakes names
  (incl. a `renames` case), sets `is_active` per `visible`, sets group_id, leaves
  `is_hidden`, sets `active_template`, bumps `catalog_version`; switching to a
  second template re-themes; a `used` category not in the new template stays
  visible; a `is_hidden` category stays hidden across apply.
- `tests/category_templates/test_visibility.py` — the predicate truth table
  (active/used/hidden/retired combinations) via the SQL; fresh seed before any
  apply returns an empty list from `list_visible_categories`.
- `tests/category_templates/test_search_activate.py` — search finds a hidden
  category; activate makes it visible and places it in a group.
- `tests/category_templates/test_create.py` — `create_category` slugifies the
  name into a `u_`-prefixed code, places it in the resolved group, is
  immediately visible; a colliding name gets a numeric-suffixed code
  (`u_my_thing`, `u_my_thing_2`); unknown `group_code` raises `ValueError`;
  the row survives a subsequent `seed_category_templates` reconcile untouched
  (the `u_` namespace guard); `rename_category` changes `name` and leaves `code`
  untouched.
- Update classifier / expenses / frequent-categories tests for the visible-set
  restriction and activation-on-use.
- `tests/api/test_api_analytics.py` (`get_analytics_summary` /
  `analytics_auto_trends.sql`, loaded at `api/analytics.py:59`) — add a case: a
  category with recent expenses but `is_active = 0` after a template switch must
  still surface in trends; this is the regression the dropped filter guards
  against.

## Done gate
`uv run inv pre` + `uv run pytest` green.

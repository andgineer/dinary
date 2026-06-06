# Phase 2 — Backend domain: apply, visibility reads, category ops

Depends on Phase 1 (schema, seed, definitions). All query SQL goes in
`src/dinary/db/sql/` with `AS` aliases and is mapped via
`db/sql_loader.fetchall_as`/`fetchone_as`; row dataclasses in `db/storage.py`.
Write logic in clean modules, run mutations under `storage.transaction`.

## 1. Apply a template — `src/dinary/db/category_apply.py`
`apply_template(con, set_code: str, lang: str) -> None`:
1. Load the set: `SELECT definition_json FROM category_sets WHERE code = ?`; parse.
2. Resolve each group code in the set's `groups` to a `category_groups.id`
   (created in Phase 1 seed); bake `category_groups.name` = set group name[lang]
   and set `sort_order` from the set's group order.
3. For **every** category in `visible` ∪ `hidden`:
   - `group_id` = the resolved group it sits in for this set;
   - `is_active` = 1 if in `visible` else 0;
   - `name` = `renames[code][lang]` if present else `category_translations[code][lang]`
     (fallback to the `ru` default, then the code itself);
   - **leave `is_hidden` and `is_retired` untouched.**
   Single `UPDATE ... WHERE code = ?` per category (FK-safe; `id` untouched).
   User-created categories (`code` starting `u_`) are absent from every template's
   `visible ∪ hidden` and are therefore skipped by apply — their `is_active`,
   `group_id`, and `name` remain unchanged.
4. `set_catalog_version(con, get_catalog_version(con) + 1)` (reuse
   `db/catalog.py`) and `UPDATE/INSERT app_metadata.active_template = set_code`.
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
  - `activate_category(con, code)` — `is_active=1, is_hidden=0`; if `group_id`
    is NULL, place it using the active set's definition (read `app_metadata.active_template`,
    load `category_sets.definition_json` for that code, resolve to a `category_groups.id`);
    if there is no active template or the code is absent from the definition, leave
    `group_id=NULL` — the category stays invisible in grouped views until apply or
    a manual move; bump `catalog_version`.
  - `hide_category(con, code)` / `unhide_category(con, code)` — toggle `is_hidden`;
    bump `catalog_version`.
  - `move_category(con, code, group_code)` — set `group_id` (manual override);
    bump `catalog_version`.

## 3. Wire visibility into existing consumers
The LLM classifier and POST validation must use the **visible** set (decided).
Enumerate and update callers of today's `db/catalog.list_categories`:
- `src/dinary/background/classification/*` (the classifier's allowed category
  list) → use `list_visible_categories`.
- POST `/api/expenses` category validation (find in `api/controllers/expenses.py`)
  → if `is_retired`, return 400; if `is_hidden`, return 400 (hidden categories are
  not pickable — guard against stale client sessions); if inactive-but-not-hidden-
  and-not-retired, call `activate_category` — activation-on-use keeps a used
  category visible per the predicate.
- `frequent_categories` flow (`api/controllers/catalog.py`,
  `stores/frequentCategories.js`) → restrict to visible.
- Grep: `rg "list_categories\b"` to find every reference; update each + its test.

## 4. Tests (same session)
- `tests/category_templates/test_apply.py` — applying a set bakes names
  (incl. a `renames` case), sets `is_active` per `visible`, sets group_id, leaves
  `is_hidden`, sets `active_template`, bumps `catalog_version`; switching to a
  second set re-themes; a `used` category not in the new set stays visible; a
  `is_hidden` category stays hidden across apply.
- `tests/category_templates/test_visibility.py` — the predicate truth table
  (active/used/hidden/retired combinations) via the SQL.
- `tests/category_templates/test_search_activate.py` — search finds a hidden
  category; activate makes it visible and places it in a group.
- Update classifier / expenses / frequent-categories tests for the visible-set
  restriction and activation-on-use.

## Done gate
`uv run inv pre` + `uv run pytest` green.

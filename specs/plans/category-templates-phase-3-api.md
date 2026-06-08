# Phase 3 — API layer

Expose Phase 2 domain over HTTP for the PWA. Mirror the conventions in
`src/dinary/api/catalog.py` (APIRouter, `Depends(get_db)`, `catalog_version`
ETag) and `api/controllers/catalog.py` (Pydantic models + thin controller
functions). Router registered in `src/dinary/main.py` via `include_router`.

## 1. Router — `src/dinary/api/category_templates.py`
Controller logic + Pydantic models in
`src/dinary/api/controllers/category_templates.py`.

Endpoints:
- `GET /api/category-templates` → **200** `[{code, names: {lang: name}, taglines: {lang: tagline}, origin}]`
  from `category_templates` (parse `definition_json`; include `origin` so custom "My setup"
  shows too), ordered by `sort_order`.
- `GET /api/category-templates/active` → **200** `{active_template: str | null}` via
  `get_active_template`. `null` is the onboarding signal for the PWA.
- `POST /api/category-templates/apply` `{code, lang}` → `apply_template`;
  **200** `{active_template: str, catalog_version: int}`; the PWA compares the
  returned `catalog_version` against its cached value and re-fetches
  `GET /api/categories` (which will return a new ETag via the standard
  `If-None-Match` mechanism). **404** on unknown code.
- `GET /api/categories` → **200** visible grouped list from `list_visible_categories`;
  **304** when `If-None-Match` matches. New independent endpoint with its own
  `If-None-Match` / `catalog_version` ETag handling. `GET /api/catalog` is left
  unchanged — it is the broader admin snapshot (groups/categories/events/tags/
  frequent_categories), not a category-only endpoint, and stays as the backing
  for the existing groups/events/tags admin CRUD; only the *picker-facing*
  category consumers migrate to `GET /api/categories` in Phase 4 (see
  Phase 2 §3's note on `build_catalog_snapshot`).
- `GET /api/categories/search?q=` → **200** `search_categories` result (includes
  hidden / not-in-set; excludes retired).
- `POST /api/categories` `{name, group_code}` → `create_category`; **201**
  `{code: str, catalog_version: int}`; **404** on unknown `group_code`. The
  code-based replacement for the old id-based "add category" — covers the
  "brand new category" half of `category-templates.md`'s "add (new user-code or
  reuse existing by search → activation)" decision (the "reuse existing" half is
  `POST /api/categories/{code}/activate` below).
- `POST /api/categories/{code}/activate` → **200** `{catalog_version: int}`;
  **404** on unknown `code`.
- `POST /api/categories/{code}/hide` / `POST /api/categories/{code}/unhide` →
  **200** `{catalog_version: int}`; **404** on unknown `code`.
- `POST /api/categories/{code}/move` `{"group_code": "..."}` → **200**
  `{catalog_version: int}`; **404** on unknown `code` or unknown `group_code`.
- `POST /api/categories/{code}/rename` `{"name": "..."}` → `rename_category`;
  **200** `{catalog_version: int}`; **404** on unknown `code`. The label-only,
  code-based replacement for `edit_category`'s id-based rename — see Phase 2 §2.
- Keep the existing `/api/catalog/*` admin writers working during Phase 3;
  id-based add/delete endpoints in `catalog_writer_categories.py` are removed
  in Phase 4 step 4 once the PWA has migrated to code-based ops.

## 2. Wiring & cache
- `main.py`: `app.include_router(category_templates.router)`.
- Every mutation bumps `catalog_version` (done in Phase 2 functions), so the
  existing PWA ETag/`If-None-Match` cache invalidation keeps working unchanged.
- `GET /api/categories` and `GET /api/catalog` share the same `catalog_version`
  counter. Any mutation that bumps it invalidates both endpoints' ETags
  simultaneously — the PWA can detect the change via either endpoint.

## 3. Tests (`tests/api/...`, mirror `test_api_catalog.py`)
- `test_api_category_templates.py`:
  - list returns the 4 factory sets (+ custom when present), ordered.
  - active is `null` on a freshly-seeded DB; becomes the code after apply.
  - apply switches visibility + bumps `catalog_version` (ETag changes); unknown
    code → 4xx.
  - `GET /api/categories` returns only visible+grouped; `If-None-Match` → 304.
  - search finds a hidden category; activate then makes it appear in
    `GET /api/categories`.
  - hide removes a category from `GET /api/categories` even when it has expenses
    (used); unhide restores it.
  - move changes its group.
  - create returns a `u_`-prefixed code, **201**, and the category appears in
    `GET /api/categories` in the requested group; unknown `group_code` → 404.

## Done gate
`uv run inv pre` + `uv run pytest` green.

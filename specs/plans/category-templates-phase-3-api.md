# Phase 3 — API layer

Expose Phase 2 domain over HTTP for the PWA. Mirror the conventions in
`src/dinary/api/catalog.py` (APIRouter, `Depends(get_db)`, `catalog_version`
ETag) and `api/controllers/catalog.py` (Pydantic models + thin controller
functions). Router registered in `src/dinary/main.py` via `include_router`.

## 1. Router — `src/dinary/api/category_templates.py`
Controller logic + Pydantic models in
`src/dinary/api/controllers/category_templates.py`.

Endpoints:
- `GET /api/category-templates` → list available sets for the chooser:
  `[{code, names: {lang: name}, taglines: {lang: tagline}, origin}]` from
  `category_sets` (parse `definition_json`; include `origin` so custom "My setup"
  shows too), ordered by `sort_order`.
- `GET /api/category-templates/active` → `{active_template: str | null}` via
  `get_active_template`. `null` is the onboarding signal for the PWA.
- `POST /api/category-templates/apply` `{code, lang}` → `apply_template`;
  return `{active_template: str, catalog_version: int}` + set `ETag` from the
  bumped `catalog_version` (reuse `etag_for`); the PWA re-fetches
  `GET /api/categories` via the standard ETag mechanism. 404 on unknown code.
- `GET /api/categories` → visible grouped list from `list_visible_categories`
  — new independent endpoint with its own `If-None-Match` / `catalog_version` ETag
  handling. `GET /api/catalog` is left unchanged during Phase 3; the PWA migrates
  to `GET /api/categories` in Phase 4.
- `GET /api/categories/search?q=` → `search_categories` (includes hidden /
  not-in-set; excludes retired).
- `POST /api/categories/{code}/activate` → `activate_category`; bump version.
- `POST /api/categories/{code}/hide` / `POST /api/categories/{code}/unhide` →
  toggle `is_hidden`; bump version.
- `POST /api/categories/{code}/move` `{"group_code": "..."}` → `move_category`; bump version.
- Keep the existing `/api/catalog/*` admin writers working during Phase 3;
  id-based add/delete endpoints in `catalog_writer_categories.py` are removed
  in Phase 4 step 4 once the PWA has migrated to code-based ops.

## 2. Wiring & cache
- `main.py`: `app.include_router(category_templates.router)`.
- Every mutation bumps `catalog_version` (done in Phase 2 functions), so the
  existing PWA ETag/`If-None-Match` cache invalidation keeps working unchanged.

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

## Done gate
`uv run inv pre` + `uv run pytest` green.

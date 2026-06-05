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
  `[{code, names: {lang: name}, origin}]` from `category_sets` (parse
  `definition_json` names; include `origin` so custom "My setup" shows too),
  ordered by `sort_order`.
- `GET /api/category-templates/active` → `{active_template: str | null}` via
  `get_active_template`. `null` is the onboarding signal for the PWA.
- `POST /api/category-templates/apply` `{code, lang}` → `apply_template`;
  return the new visible category snapshot + set `ETag` from the bumped
  `catalog_version` (reuse `etag_for`). 409/400 on unknown code.
- `GET /api/categories` → visible grouped list from `list_visible_categories`
  (this can replace/augment the categories section of the existing
  `/api/catalog` snapshot; keep `catalog_version` ETag + `If-None-Match`
  handling like `get_catalog`).
- `GET /api/categories/search?q=` → `search_categories` (includes hidden /
  not-in-set; excludes retired).
- `POST /api/categories/{code}/activate` → `activate_category`; bump version.
- `POST /api/categories/{code}/hide` / `POST /api/categories/{code}/unhide` →
  toggle `is_hidden`; bump version.
- `PATCH /api/categories/{code}` `{group_code}` → `move_category`; bump version.
- Keep the existing `/api/catalog/*` admin writers working during transition;
  reconcile the two surfaces (the new code-based category ops vs the old
  id-based ones in `catalog_writer_categories.py`) — prefer the code-based ones,
  deprecate id-based add/delete once the PWA migrates (Phase 4).

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

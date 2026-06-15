# Catalog consolidation: drop `/api/categories` and `/api/categories/search`

## Goal

The PWA already caches the full `/api/catalog` snapshot client-side with
version/ETag-based conditional refetch (`dinary:catalog:v1`, `catalog_version`).
`GET /api/categories` (visible-categories list) and `GET /api/categories/search`
duplicate data that's already in that snapshot, just missing two columns
(`code`, `is_hidden`) and a retired-filter. Once the snapshot carries those,
both endpoints become redundant: `CategorySheet`'s picker list and its search
become synchronous local filters over the cached snapshot — no network call,
no debounce, no "offline" state to report.

Category-mutation endpoints (`activate`/`hide`/`unhide`/`move`/`rename`/create)
stay, but each returns only `{catalog_version}` plus whatever the server alone
determined (a new id/code, a resolved group placement, a
created-vs-reactivated status). The caller already knows the rest — it just
sent it in the request — and patches its own cached snapshot. The existing
`AdminCatalogResponse` pattern for the group/event/tag admin endpoints
(`POST/PATCH/DELETE /api/catalog/{groups,events,tags}/...` →
`applySnapshot()`) returns the *entire* catalog for the same kind of one-row
change. That is the same idiom — but on a **cold path** (admin mutations are
rare, the snapshot is small), so converting it is split out as an **optional
follow-up**; see the scope note at the head of §6b. The core of this plan is
the category consolidation (§5, §7–§11); §6b can land separately, or not at
all. `apply_template` (which genuinely
rewrites most of `category_groups`/`categories`) is the odd one out the other
way: today it returns only `{active_template, catalog_version}`, and the
frontend follows up with a separate conditional `GET /api/categories`
(`_refreshVisibleCategoriesIfChanged`). Since that endpoint and its
visible-categories cache are both going away, `apply_template` switches *to*
returning — and applying — the full snapshot inline, replacing
`ApplyTemplateResponse` with `CategoryMutationResponse` (§6). Either way, a
mutation ends up as a single POST round trip: no follow-up GET to refresh the
cache.

---

## Design principle: a mutation response carries only what the caller couldn't already know

A one-row mutation (`hide_category`, `move_category`, `adminPatchGroup`,
`adminDeactivateTag`, ...) is, by definition, a change the frontend itself
just requested with full knowledge of the new state. Echoing that whole state
back — let alone the *entire catalog* — is the "идиотизм" the
`AdminCatalogResponse`/full-snapshot pattern commits for every one of these
endpoints today, not just the 6 category ones. The fix applies uniformly:

- **Response = `{catalog_version}`**, plus only fields the server alone
  decided:
  - `create_category` / admin `add` — server-assigned `id` (and, for
    categories, the slugified `code`).
  - `activate_category` — `group_id` may be resolved from the active
    template if it was previously `NULL`; the resulting category is returned.
  - admin `add` — `status` (`created`/`reactivated`/`noop`, since adding a
    soft-deleted item by name reactivates it instead of duplicating).
  - admin `remove` — `delete_status` (`hard`/`soft`) and `usage_count`, since
    only the backend knows whether the row still has references.
  - Everything else (`hide`/`unhide`/`move`/`rename`/admin
    `patch`/`reactivate`/`deactivate`) — `{catalog_version}` only.
- **The frontend patches its own cached snapshot** with the field values it
  already sent in the request (plus any server-determined extras above), then
  sets `snapshot.value.catalog_version` from the response.
- **`frequent_categories` is not recomputed for these mutations.** It can
  drift slightly (e.g. a newly-hidden category lingers in the "frequent" quick
  picks until the next sync) — this self-heals via the *existing* mechanism in
  `webapp/src/composables/flushQueue.js:52-54`: every `POST /api/expenses`
  returns a fresh `frequent_categories`, and `applyFrequentCategories()`
  overwrites the cached list **unconditionally** on each post. (Note: the
  sibling `catalog.load()`-on-`catalog_version`-mismatch at
  `flushQueue.js:76-78` does *not* heal this case — a category mutation already
  set the cached `catalog_version` to the server's new value, so the versions
  match and that conditional GET just 304s.) No new consistency mechanism is
  introduced — this plan just stops manufacturing reasons to bypass it.
- **`apply_template` is the genuine exception, and a new response shape.** It
  rewrites most of `category_groups` and many `categories` rows in ways the
  frontend cannot reconstruct from the request alone, so its response changes
  from today's `{active_template, catalog_version}` to the full snapshot
  (`CategoryMutationResponse`, §6), which the frontend applies via
  `applySnapshot()` — this is "we tell the backend what to do and get the new
  catalog back," not the one-row case.

---

## Design: `is_active` already means "pickable", no `has_expenses` needed

`VISIBLE_CATEGORY_PREDICATE` is currently:

```
NOT c.is_retired AND NOT c.is_hidden AND (c.is_active OR EXISTS (SELECT 1 FROM expenses WHERE category_id = c.id))
```

The `OR has_expenses` clause exists so that switching category templates never
hides a category the user has historical expenses under. But every place that
sets `is_active` already maintains "used implies active", except one:

- `_resolve_category_for_write` (`src/dinary/api/controllers/expenses.py`) and
  `_validate_category_for_correction` (`src/dinary/api/controllers/expense_corrections.py`)
  already call `activate_category()` when an expense is created/corrected
  against an inactive category.
- `category_apply.apply_template()` (`src/dinary/db/category_apply.py`) is the
  only place that sets `is_active = 0` for an existing category **during
  normal API operation** — for every code placed in the new template's
  `hidden` bucket, unconditionally. (`category_seed.py`'s `_retire_vanished`,
  a seed/maintenance-time step, also sets `is_active = 0`, but always together
  with `is_retired = 1` — those rows are excluded from
  `VISIBLE_CATEGORY_PREDICATE` by the `is_retired` clause regardless of
  `is_active`, in both the old and the simplified predicate, so it needs no
  change here.)

Fix `apply_template` to never deactivate a category that already has expenses.
Then `is_active` always already equals `(is_active OR has_expenses)`, and the
predicate collapses to `NOT is_retired AND NOT is_hidden AND is_active`
everywhere — no `has_expenses` field, no `EXISTS` subquery, in the predicate's
definition or in any of its 6 usages across `background/classification/task.py`,
`sheets/sheet_mapping.py`, `api/controllers/catalog.py`, and
`api/controllers/rules.py`.

### 1. `src/dinary/db/category_apply.py` — `apply_template()`

Before the placement loop, compute the set of category codes with at least one
expense:

```python
used_codes = {
    str(code)
    for (code,) in con.execute(
        "SELECT DISTINCT c.code FROM categories c JOIN expenses e ON e.category_id = c.id",
    ).fetchall()
}
```

In the loop (currently `is_active = 1 if is_visible else 0`):

```python
is_active = 1 if (is_visible or code in used_codes) else 0
```

### 2. Simplify `VISIBLE_CATEGORY_PREDICATE` — `src/dinary/db/catalog.py`

```python
VISIBLE_CATEGORY_PREDICATE = "NOT c.is_retired AND NOT c.is_hidden AND c.is_active"
```

Update the docstring/comment (currently explains the `OR has_expenses`
rationale) to describe the simpler invariant: "`is_active` already reflects
template membership and historical use; `apply_template` is the only writer
that can deactivate a category during normal operation, and it never does so
for one with expenses. (`category_seed`'s retirement step also clears
`is_active`, but always alongside `is_retired`, which this predicate excludes
unconditionally.)"

### 3. Simplify `src/dinary/db/sql/list_visible_categories.sql`

Drop the `LEFT JOIN (SELECT DISTINCT category_id FROM expenses) u` and the
`OR u.category_id IS NOT NULL` branch — just:

```sql
SELECT c.id AS id, c.code AS code, c.name AS name,
       c.group_id AS group_id, g.name AS group_name,
       g.sort_order AS group_sort_order, g.code AS group_code
FROM categories c
JOIN category_groups g ON g.id = c.group_id
WHERE NOT c.is_retired AND NOT c.is_hidden AND c.is_active
ORDER BY g.sort_order, c.name
```

(`list_visible_categories()` / `VisibleCategoryRow` stay — used internally by
`receipt_classifier.load_categories()` for the LLM prompt.)

### 4. One-time local backfill

Existing dev DB may already have categories with `is_active=0` and expense
history (from a past template switch, before this fix). The category-templates
feature hasn't shipped to production yet (prod is on 1.4.1), so this is a
one-off `UPDATE` against the personal dev DB only — no production migration
needed. Exclude `is_retired` rows (handled separately by
`category_seed._retire_vanished`, and already excluded from
`VISIBLE_CATEGORY_PREDICATE` regardless of `is_active`):

```sql
UPDATE categories SET is_active = 1
WHERE is_active = 0
  AND NOT is_retired
  AND id IN (SELECT DISTINCT category_id FROM expenses);
```

### 4b. `src/dinary/db/catalog.py` — `activate_category()`: `group_id=NULL` is never valid for an active category

Today, if `group_id` is `NULL` and either there's no active template or the
code is absent from its `visible`/`hidden` buckets, `activate_category` still
sets `is_active=1` and silently leaves `group_id=NULL` — an "active but
ungrouped" row. `_category_item` (§6) needs `group_id`/`group` to always be
resolvable for an active category (its `categories JOIN category_groups` is
an `INNER JOIN`, matching `build_catalog_snapshot`'s); rather than make that
query handle a `NULL` group, close the inconsistent state at the source.

Per `_validate_template_coverage` (`category_templates/loader.py`), every
vocabulary code is placed in every template's `visible`/`hidden` buckets, so
`_resolve_group_code_in_template` only fails to resolve a group when there is
**no active template at all** — which `App.vue:111-115` already makes
unreachable for `/api/categories/{code}/activate` (no active template ⇒
`OnboardingTemplate` instead of the main app). A category with `group_id=NULL`
is also excluded from `build_catalog_snapshot`'s `categories` (same `INNER
JOIN`), so the frontend never has such a `code` to send to `/activate` either.
This change therefore turns an already-unreachable branch into an explicit
error, not a new failure mode for real users.

```python
def activate_category(con: sqlite3.Connection, code: str) -> None:
    """Make ``code`` pickable: ``is_active=1, is_hidden=0``.

    If ``group_id`` is ``NULL``, resolve it from the active template's
    definition. Raises ``ValueError`` if the code is unknown, or if
    ``group_id`` is ``NULL`` and no group can be resolved (no active
    template, or the template row is missing) — ``is_active=1`` with
    ``group_id=NULL`` is never a valid catalog state.
    """
    with storage.transaction(con):
        row = con.execute(
            "SELECT group_id FROM categories WHERE code = ?",
            [code],
        ).fetchone()
        if row is None:
            msg = f"Unknown category code: {code!r}"
            raise ValueError(msg)

        if row["group_id"] is None:
            template_code = get_active_template(con)
            template_row = (
                con.execute(
                    "SELECT definition_json FROM category_templates WHERE code = ?",
                    [template_code],
                ).fetchone()
                if template_code is not None
                else None
            )
            group_code = (
                _resolve_group_code_in_template(json.loads(template_row["definition_json"]), code)
                if template_row is not None
                else None
            )
            if group_code is None:
                msg = f"Cannot activate {code!r}: no group could be resolved"
                raise ValueError(msg)
            con.execute(
                "UPDATE categories SET group_id = "
                "(SELECT id FROM category_groups WHERE code = ?) WHERE code = ?",
                [group_code, code],
            )

        con.execute(
            "UPDATE categories SET is_active = 1, is_hidden = 0 WHERE code = ?",
            [code],
        )
        set_catalog_version(con, get_catalog_version(con) + 1)
```

`activate_category_sync` (§6) already wraps `ValueError` as
`HTTPException(404)`, so the new failure mode follows the same
404-on-unknown-code contract as the other category mutations — no change
needed there. The two other callers (`_resolve_category_for_write`,
`_validate_category_for_correction`) are unaffected: they only run once an
active template exists (expenses can't be created during onboarding), and
template coverage guarantees a group resolves for any vocabulary code.

---

## Backend: snapshot + endpoint changes

### 5. Augment `build_catalog_snapshot()` — `src/dinary/api/controllers/catalog.py`

**Models** (lines ~22-36): `CategoryGroupItem` gains `code: str`; `CategoryItem`
gains `code: str`, `is_hidden: bool`, and `is_retired: bool`.

**`category_groups` query** (currently line ~272) — add `g.code`:

```python
group_rows = con.execute(
    "SELECT id, code, name, sort_order, is_active FROM category_groups ORDER BY sort_order, id",
).fetchall()
```

Dict per group (currently lines ~291-298) gains `code`:

```python
{
    "id": int(r[0]),
    "code": str(r[1]),
    "name": str(r[2]),
    "sort_order": int(r[3]),
    "is_active": bool(r[4]),
    "removable": group_children.get(int(r[0]), 0) == 0,
}
```

**`categories` query** (currently lines ~274-278) — add `c.code`, `c.is_hidden`,
`c.is_retired`. Retired rows **stay** in the snapshot (no `WHERE` added):
`findCategoryById` (webapp) needs them to resolve the category of old expenses
whose category was later retired by `category_seed._retire_vanished`, which
retires by vocabulary membership regardless of expense history.
`is_active=0` (always true for retired rows — `_retire_vanished` sets both
flags together) already excludes them from `visibleCategories`, and the new
`is_retired` field lets `searchCategories()` exclude them from "addable"
results (§9), matching the old `search_categories.sql`'s `WHERE NOT c.is_retired`:

```python
category_rows = con.execute(
    "SELECT c.id, c.code, c.name, c.group_id, g.name, c.is_active, c.is_hidden, c.is_retired"
    " FROM categories c JOIN category_groups g ON g.id = c.group_id"
    " ORDER BY g.sort_order, c.name",
).fetchall()
```

Dict per category (currently lines ~301-309) gains `code`, `is_hidden`, `is_retired`:

```python
{
    "id": int(r[0]),
    "code": str(r[1]),
    "name": str(r[2]),
    "group_id": int(r[3]),
    "group": str(r[4]),
    "is_active": bool(r[5]),
    "is_hidden": bool(r[6]),
    "is_retired": bool(r[7]),
    "removable": cat_refs.get(int(r[0]), 0) == 0,
}
```

(`removable` for categories is kept as-is — unused by the frontend today, but
harmless to leave consistent with groups/events/tags. Not exposed in any UI.)

### 6. Category-mutation endpoints

`hide`/`unhide`/`move`/`rename` each touch exactly one `categories` row, and
the frontend already knows the new state (it sent `code` + the new
hidden/group/name value). `activate` and `create` are the two exceptions
where the server determines something the frontend couldn't: `activate` may
resolve `group_id` from the active template if it was `NULL`; `create`
assigns the new row's `id` and slugified `code`.

**Base response + shared ref-count helper** (`src/dinary/api/controllers/catalog.py`,
near `CategoryItem`):

```python
class CatalogVersionResponse(BaseModel):
    catalog_version: int


def _ref_count(con: sqlite3.Connection, row_id: int, tables_and_cols: tuple[tuple[str, str], ...]) -> int:
    total = 0
    for table, col in tables_and_cols:
        (n,) = con.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", [row_id]).fetchone()  # noqa: S608
        total += n
    return total


_CATEGORY_REF_TABLES = (
    ("expenses", "category_id"), ("sheet_mapping", "category_id"), ("import_mapping", "category_id"),
)
```

(`_ref_count`/`_CATEGORY_REF_TABLES` are the same helper §6b generalizes for
groups/events/tags — defined once, here, and reused there. This is a
single-row counterpart to the existing `_sum_counts_by_id`/`reference_counts()`
in this module, which batch-compute `removable` for the whole snapshot via
`GROUP BY`; for a single mutation response, `COUNT(*) WHERE col = ?` per table
is cheaper than running the batched query just to look up one id.)

**Result response, for the two endpoints that return a resolved category:**

```python
class CategoryResultResponse(CatalogVersionResponse):
    category: CategoryItem
```

Shared helper, also in `controllers/catalog.py` (used only by `activate` and
`create`, so the per-category reference-count query — needed for
`CategoryItem.removable` — runs at most once per request):

```python
def _category_item(con: sqlite3.Connection, code: str) -> CategoryItem:
    row = con.execute(
        "SELECT c.id, c.code, c.name, c.group_id, g.name AS group_name,"
        " c.is_active, c.is_hidden, c.is_retired"
        " FROM categories c JOIN category_groups g ON g.id = c.group_id"
        " WHERE c.code = ?",
        [code],
    ).fetchone()
    return CategoryItem(
        id=int(row[0]), code=str(row[1]), name=str(row[2]),
        group_id=int(row[3]), group=str(row[4]),
        is_active=bool(row[5]), is_hidden=bool(row[6]), is_retired=bool(row[7]),
        removable=_ref_count(con, int(row[0]), _CATEGORY_REF_TABLES) == 0,
    )
```

(`FROM categories c JOIN category_groups g ON g.id = c.group_id` is an
`INNER JOIN` — relies on §4b's invariant that an active category always has
`group_id` set. Both callers satisfy it: `create_category` always inserts
with a `group_id`, and `activate_category` now either resolves `group_id` or
raises `ValueError` before this helper runs.)

`src/dinary/api/controllers/category_templates.py` already defines its own
`CatalogVersionResponse` (currently lines ~106-108), with the same single
`catalog_version: int` field — drop that definition and import
`CatalogVersionResponse`, `CategoryResultResponse`, `CategoryItem`, and
`_category_item` from `controllers.catalog` instead:

```python
def activate_category_sync(con: sqlite3.Connection, code: str) -> CategoryResultResponse:
    try:
        activate_category(con, code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return CategoryResultResponse(catalog_version=get_catalog_version(con), category=_category_item(con, code))


def create_category_sync(con: sqlite3.Connection, body: CreateCategoryBody) -> CategoryResultResponse:
    try:
        code = create_category(con, body.name, body.group_code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return CategoryResultResponse(catalog_version=get_catalog_version(con), category=_category_item(con, code))


def hide_category_sync(con: sqlite3.Connection, code: str) -> CatalogVersionResponse:
    try:
        hide_category(con, code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return CatalogVersionResponse(catalog_version=get_catalog_version(con))
```

(same shape — including the `try/except ValueError → HTTPException(404)`
wrapper — for `unhide_category_sync`, `move_category_sync`,
`rename_category_sync`; this preserves the existing 404-on-unknown-code
contract that `TestHideUnhide`, `TestMoveAndRename`, etc. already test)

**Router signatures for `activate`/`create`** (`src/dinary/api/category_templates.py`):
`activate_category_endpoint` and `create_category_endpoint` currently declare
`response_model=CatalogVersionResponse`/`-> CatalogVersionResponse` and
`response_model=CreateCategoryResponse`/`-> CreateCategoryResponse`; change
both to `CategoryResultResponse`. `hide`/`unhide`/`move`/`rename` endpoints
keep `response_model=CatalogVersionResponse`. Update the router's import
block accordingly: `CatalogVersionResponse`, `CategoryResultResponse`, and
`CategoryItem` now come from `controllers.catalog` (where this section
defines/moves them), not from `controllers.category_templates`.

**`apply_template` switches to a full-snapshot response** (`category_groups`
and many `categories` rows genuinely change). This replaces today's
`ApplyTemplateResponse` (`{active_template, catalog_version}`); update the
route's `response_model` in `src/dinary/api/category_templates.py` from
`ApplyTemplateResponse` to `CategoryMutationResponse`, and remove
`ApplyTemplateResponse`:

```python
class CategoryMutationResponse(CatalogResponse):
    active_template: str
```

```python
def apply_template_sync(con, body: ApplyTemplateBody) -> CategoryMutationResponse:
    try:
        apply_template(con, body.code, body.lang)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return CategoryMutationResponse(**build_catalog_snapshot(con), active_template=body.code)
```

(keeps the existing `ValueError → 404` translation that
`TestApplyTemplate.test_unknown_code_returns_404` relies on)

Remove now-unused models: `ApplyTemplateResponse`, `VisibleCategoryItem`,
`CategoriesResponse`, `CategorySearchItem`, `CreateCategoryResponse`.

### 6b. Admin group/event/tag mutations — drop `AdminCatalogResponse` (optional follow-up)

> **Scope / trade-off.** Unlike §5–§11, this section is *not* on a hot path:
> admin group/event/tag mutations are rare and the full catalog snapshot is
> small, so the traffic saved here is marginal. The cost is real and permanent:
> the client gains `_upsertEntry` / `_ENTRY_COMPARATORS` (and `_upsertCategory`'s
> sort) that must **replicate `build_catalog_snapshot`'s server-side `ORDER BY`
> indefinitely**, and per-kind `removable` becomes an approximation (`_tag_item`
> ignores `events.auto_tags` refs — see the caveat below). For a rare operation,
> echoing the already-correct, already-sorted full snapshot is simpler and less
> error-prone. **Recommendation: ship §5/§7–§11 first; treat §6b as a separable
> follow-up, to do only if the admin response is measured to matter.** If §6b is
> deferred, the §9 `stripAdminEnvelope → _normalizeSnapshot` rename and the §8/§9
> admin-action rewrites are deferred with it — the admin actions keep calling
> `applySnapshot()`, and `stripAdminEnvelope` stays as-is.

The 9 admin endpoints (`POST/PATCH/DELETE /api/catalog/{groups,events,tags}`,
`src/dinary/api/catalog.py`) call `_etag_response()` → `snapshot_response()`,
which builds and returns the **entire** `build_catalog_snapshot()` for a
single-row add/edit/delete — the same pattern, the same idiocy, as the old
category mutations. For `patch`/`delete` the frontend already sent (or
already knows) the new state; only `delete` needs the server's hard-vs-soft
decision. For `add`, the server determines the new row's `id` and, for groups,
a possibly-defaulted `sort_order` (and, on the reactivate-in-place path, the
*preserved* `sort_order`/fields of the existing row) — so `add` returns the
one resolved row, not the whole catalog.

**Patch/delete response** (`controllers/catalog.py`), replacing the
`new_id`/`status` fields `AdminCatalogResponse` carried for all 9 endpoints:

```python
class AdminMutationResponse(CatalogVersionResponse):
    delete_status: DeleteStatusLiteral | None = None
    usage_count: int | None = None
```

**Add responses** — one per kind, each carrying the resolved row:

```python
class GroupAddResponse(CatalogVersionResponse):
    status: AddStatusLiteral
    group: CategoryGroupItem


class EventAddResponse(CatalogVersionResponse):
    status: AddStatusLiteral
    event: EventItem


class TagAddResponse(CatalogVersionResponse):
    status: AddStatusLiteral
    tag: TagItem
```

`_ref_count` is defined once in §6 (shared with `_category_item`'s
`_CATEGORY_REF_TABLES`). Add the remaining per-kind reference tables, plus one
item-builder per kind:

```python
_GROUP_REF_TABLES = (("categories", "group_id"),)
_EVENT_REF_TABLES = (
    ("expenses", "event_id"), ("sheet_mapping", "event_id"), ("import_mapping", "event_id"),
)
_TAG_REF_TABLES = (
    ("expense_tags", "tag_id"), ("sheet_mapping_tags", "tag_id"), ("import_mapping_tags", "tag_id"),
)


def _group_item(con: sqlite3.Connection, group_id: int) -> CategoryGroupItem:
    row = con.execute(
        "SELECT id, code, name, sort_order, is_active FROM category_groups WHERE id = ?", [group_id],
    ).fetchone()
    return CategoryGroupItem(
        id=int(row[0]), code=str(row[1]), name=str(row[2]), sort_order=int(row[3]),
        is_active=bool(row[4]), removable=_ref_count(con, group_id, _GROUP_REF_TABLES) == 0,
    )


def _event_item(con: sqlite3.Connection, event_id: int) -> EventItem:
    row = con.execute(
        "SELECT id, name, date_from, date_to, auto_attach_enabled, auto_tags, is_active"
        " FROM events WHERE id = ?",
        [event_id],
    ).fetchone()
    return EventItem(
        id=int(row[0]), name=str(row[1]), date_from=str(row[2]), date_to=str(row[3]),
        auto_attach_enabled=bool(row[4]), auto_tags=decode_auto_tags_value(row[5], context="event add response"),
        is_active=bool(row[6]), removable=_ref_count(con, event_id, _EVENT_REF_TABLES) == 0,
    )


def _tag_item(con: sqlite3.Connection, tag_id: int) -> TagItem:
    row = con.execute("SELECT id, name, is_active FROM tags WHERE id = ?", [tag_id]).fetchone()
    return TagItem(
        id=int(row[0]), name=str(row[1]), is_active=bool(row[2]),
        removable=_ref_count(con, tag_id, _TAG_REF_TABLES) == 0,
    )
```

(`_tag_item`'s `removable` ignores `events.auto_tags` references, unlike the
full-snapshot `reference_counts()`. A freshly-created tag has none; a
reactivated one with stale auto-tag references would show `removable: true`
until the next full `/api/catalog` refresh — the backend's `delete_status`
remains authoritative if the user actually attempts to delete it.)

`src/dinary/api/catalog.py`:

```python
def _etag_response(con: sqlite3.Connection, response: Response, delete_result=None) -> AdminMutationResponse:
    version = get_catalog_version(con)
    body = AdminMutationResponse(
        catalog_version=version,
        delete_status=delete_result.status if delete_result else None,
        usage_count=delete_result.usage_count if delete_result else None,
    )
    response.headers["ETag"] = etag_for(version)
    return body
```

`edit_group_endpoint`/`delete_group_endpoint` (and the event/tag equivalents)
call `_etag_response(con, response, delete_result=result)` (delete) or
`_etag_response(con, response)` (patch) — `response_model=AdminMutationResponse`.

`add_group_endpoint` (and event/tag equivalents) build their own response:

```python
@router.post("/api/catalog/groups", response_model=GroupAddResponse)
def add_group_endpoint(body: GroupAddBody, response: Response, con=Depends(get_db)) -> GroupAddResponse:
    with handle_catalog_error():
        result = add_group(con, name=body.name, sort_order=body.sort_order)
    version = get_catalog_version(con)
    response.headers["ETag"] = etag_for(version)
    return GroupAddResponse(catalog_version=version, status=result.status, group=_group_item(con, result.id))
```

(same shape for `add_event_endpoint`/`add_tag_endpoint` with `_event_item`/`_tag_item`)

`AdminCatalogResponse` and `snapshot_response()` are removed entirely.

### 7. Remove `GET /api/categories` and `GET /api/categories/search`

- `src/dinary/api/category_templates.py`: remove the two `@router.get` routes
  (`get_categories`, `search_categories_endpoint`), and drop the now-unused
  imports they (and §6) make obsolete: `CategoriesResponse`,
  `CategorySearchItem`, `CreateCategoryResponse`, `ApplyTemplateResponse`,
  `get_categories_response`, `search_categories_response`.
- `src/dinary/api/controllers/category_templates.py`: remove
  `get_categories_response`, `search_categories_response`.
- `src/dinary/db/catalog.py`: remove `search_categories()`.
- `src/dinary/db/sql/search_categories.sql`: delete.
- `src/dinary/db/storage.py`: remove `CategorySearchRow`.

---

## Frontend

### 8. `webapp/src/api/catalog.js`

- Remove `getCategories()` and `searchCategories()`.
- `activateCategory`/`createCategory` now return `{catalog_version, category}`;
  `hideCategory`/`unhideCategory`/`moveCategory`/`renameCategory` now return
  `{catalog_version}`; `applyTemplate`'s response body changes shape — from
  today's `{active_template, catalog_version}` to the full snapshot +
  `active_template` (§6). No signature change for any of these — the
  `catalogApi.*` wrappers already just return the parsed JSON body.
- `adminAddGroup`/`adminAddEvent`/`adminAddTag` now return
  `{catalog_version, status, group|event|tag}`; `adminPatchGroup`/
  `adminPatchEvent`/`adminPatchTag` (and the `adminReactivate*`/
  `adminDeactivate*` wrappers built on them) now return `{catalog_version}`;
  `adminDeleteGroup`/`adminDeleteEvent`/`adminDeleteTag` now return
  `{catalog_version, delete_status, usage_count}`. No signature change.

### 9. `webapp/src/stores/catalog.js`

**Simplify `stripAdminEnvelope` → `_normalizeSnapshot`** (line ~40). *(Deferred
with §6b — this rename only applies once the admin envelope is removed; if §6b
is not done, `stripAdminEnvelope` stays as-is.)* Once
`AdminCatalogResponse` is gone (§6b), `applySnapshot()`'s only callers are
`load()` (`CatalogResponse`, always has `frequent_categories`) and
`applyTemplate()` (`CategoryMutationResponse` extends `CatalogResponse`, also
always has `frequent_categories`, plus the extra `active_template` field this
function should still drop). The `existingFrequentCategories` fallback
parameter and its "AdminCatalogResponse omits frequent_categories" comment are
no longer reachable — drop both, and rename for clarity:

```js
function _normalizeSnapshot(snapshot) {
  const { catalog_version, category_groups, categories, events, tags, frequent_categories } = snapshot ?? {};
  return {
    catalog_version,
    category_groups: category_groups ?? [],
    categories: categories ?? [],
    events: events ?? [],
    tags: tags ?? [],
    frequent_categories: frequent_categories ?? [],
  };
}
```

Update its two call sites: `writeCachedSnapshot()` (line ~81) and
`applySnapshot()` (line ~252, now a single-argument call — drop the
`snapshot.value?.frequent_categories ?? []` fallback argument).

Remove:
- `visibleCategories` ref, `visibleCategoriesVersion` ref
- `loadVisibleCategories()`, `loadVisibleCategoriesIfNeeded()`
- `_refreshVisibleCategoriesIfChanged()`
- `visibleCategoryByCode()` (old ref-backed version)
- `searchCategories()` (old API-backed wrapper)

Add a `visibleCategories` **computed**, derived from `snapshot`:

```js
const visibleCategories = computed(() => {
  if (!snapshot.value) return [];
  const groupsById = new Map(snapshot.value.category_groups.map((g) => [g.id, g]));
  return snapshot.value.categories
    .filter((c) => isActive(c) && !c.is_hidden)
    .map((c) => {
      const g = groupsById.get(c.group_id);
      return {
        id: c.id,
        code: c.code,
        name: c.name,
        group_id: c.group_id,
        group_name: c.group,
        group_code: g?.code ?? "",
        group_sort_order: g?.sort_order ?? 0,
      };
    })
    .sort((a, b) => a.group_sort_order - b.group_sort_order || a.name.localeCompare(b.name));
});
```

Unlike the backend's `VISIBLE_CATEGORY_PREDICATE`, this filter doesn't repeat
`!c.is_retired` — it relies on the invariant from the "Design" section above
(`category_seed._retire_vanished` always sets `is_active=0` together with
`is_retired=1`, and nothing reactivates a retired category), so `is_active`
alone already excludes retired rows.

The filter uses the existing lenient `isActive(c)` helper
(`catalog.js:16-20`, `is_active !== false`), **not** a bare `c.is_active`
truthiness check, for the same upgrade-safety reason the rest of the store
already does: a snapshot cached before this change lacks the new `code` /
`is_hidden` / `is_retired` columns, and `c.is_active` is already present so the
picker stays populated, but staying consistent with `isActive()` avoids a future
column rename silently emptying the picker. `searchCategories()` below reads
`c.is_retired` strictly (`!c.is_retired`) — on a pre-upgrade cached snapshot
that field is `undefined`, so retired rows briefly leak into search results
until the startup `/api/catalog` refresh lands; this window is the synchronous
gap between reading the cached snapshot and `load()` completing, and is
acceptable (transient, self-heals on the next render).

```js
function visibleCategoryByCode(code) {
  return visibleCategories.value.find((c) => c.code === code) ?? null;
}
```

Add a synchronous local `searchCategories(q)`, replicating
`search_categories.sql`'s old shape, retired-category filter
(`NOT c.is_retired`), and order (`is_active DESC, name`):

```js
function searchCategories(q) {
  if (!snapshot.value) return [];
  const needle = q.trim().toLowerCase();
  if (!needle) return [];
  return snapshot.value.categories
    .filter((c) => !c.is_retired && c.name.toLowerCase().includes(needle))
    .map((c) => ({ id: c.id, code: c.code, name: c.name, is_active: c.is_active, is_hidden: c.is_hidden }))
    .sort((a, b) => Number(b.is_active) - Number(a.is_active) || a.name.localeCompare(b.name));
}
```

Add local-patch helpers — each mutation already knows the new state it
requested, so it patches `snapshot.value` directly and just takes
`catalog_version` from the response (per the design principle above):

```js
function _setCatalogVersion(version) {
  if (!snapshot.value) return;
  snapshot.value = { ...snapshot.value, catalog_version: version };
  writeCachedSnapshot(snapshot.value);
}

function _patchCategory(code, patch) {
  if (!snapshot.value) return;
  snapshot.value = {
    ...snapshot.value,
    categories: snapshot.value.categories.map((c) => (c.code === code ? { ...c, ...patch } : c)),
  };
}

function _upsertCategory(category) {
  if (!snapshot.value) return;
  const categories = snapshot.value.categories.filter((c) => c.id !== category.id);
  categories.push(category);
  snapshot.value = { ...snapshot.value, categories };
}
```

**Sort at read, not at write.** `_upsertCategory` deliberately does *not*
replicate `build_catalog_snapshot`'s `ORDER BY g.sort_order, c.name` — pushing
to the end is fine because every reader of `snapshot.value.categories` that
cares about order sorts its own output. `visibleCategories` (computed above)
and `searchCategories()` already sort; the only two getters that today just
`filter` without sorting are `categories(groupId)` (`catalog.js:317-323`,
read by ExpenseForm/CorrectionSheet) and `inactiveCategories(groupId)` — add a
trailing `.sort((a, b) => a.name.localeCompare(b.name))` to both. Sorting in
the getter is idempotent and cannot drift; maintaining a sorted-array invariant
across every mutation path (and re-deriving the server's `ORDER BY` on the
client) is the more fragile option, so it is avoided here and — by extension —
is one more reason not to take on §6b's `_ENTRY_COMPARATORS`.

Category-mutation actions:

```js
async function hideCategory(code) {
  const resp = await catalogApi.hideCategory(code);
  _patchCategory(code, { is_hidden: true });
  _setCatalogVersion(resp.catalog_version);
  return resp;
}
// unhideCategory — same, { is_hidden: false }
// renameCategory(code, name) — same, { name }

async function moveCategory(code, groupCode) {
  const resp = await catalogApi.moveCategory(code, groupCode);
  const group = snapshot.value?.category_groups.find((g) => g.code === groupCode);
  if (group) _patchCategory(code, { group_id: group.id, group: group.name });
  _setCatalogVersion(resp.catalog_version);
  return resp;
}

async function activateCategory(code) {
  const resp = await catalogApi.activateCategory(code);
  _upsertCategory(resp.category); // group_id may have been resolved server-side
  _setCatalogVersion(resp.catalog_version);
  return resp;
}

async function createCategory(name, groupCode) {
  const resp = await catalogApi.createCategory(name, groupCode);
  _upsertCategory(resp.category); // id + slugified code are server-assigned
  _setCatalogVersion(resp.catalog_version);
  return resp; // resp.category.code is the new category's code
}
```

`applyTemplate` now applies the full snapshot from the response (most of the
catalog genuinely changed). This replaces the current implementation, which
calls `_refreshVisibleCategoriesIfChanged(resp.catalog_version)` — that
helper, and the conditional `GET /api/categories` it triggers, are both
removed by this plan:

```js
async function applyTemplate(code, lang) {
  const resp = await catalogApi.applyTemplate(code, lang);
  activeTemplate.value = resp.active_template;
  applySnapshot(resp);
  return resp;
}
```

Admin group/event/tag actions (`reactivate`/`deactivate`/`remove`/`add`/
`patch`, lines ~445-503) drop `applySnapshot(snap)` the same way:

```js
async function patch(kind, id, body) {
  const fn = { group: catalogApi.adminPatchGroup, event: catalogApi.adminPatchEvent, tag: catalogApi.adminPatchTag }[kind];
  if (!fn) throw new Error(`Unknown kind: ${kind}`);
  const resp = await fn(id, body);
  _patchEntry(kind, id, body);
  _setCatalogVersion(resp.catalog_version);
  return resp;
}

// reactivate(kind, id) / deactivate(kind, id) call patch(kind, id, { is_active: true|false })
// already — no separate change needed beyond patch() itself.

async function remove(kind, id) {
  const fn = { group: catalogApi.adminDeleteGroup, event: catalogApi.adminDeleteEvent, tag: catalogApi.adminDeleteTag }[kind];
  if (!fn) throw new Error(`Unknown kind: ${kind}`);
  const resp = await fn(id);
  if (resp.delete_status === "hard") {
    _removeEntry(kind, id);
  } else {
    _patchEntry(kind, id, { is_active: false });
  }
  _setCatalogVersion(resp.catalog_version);
  return resp; // resp.usage_count for the "still used by N" toast
}

async function add(kind, body) {
  const fn = { group: catalogApi.adminAddGroup, event: catalogApi.adminAddEvent, tag: catalogApi.adminAddTag }[kind];
  if (!fn) throw new Error(`Unknown kind: ${kind}`);
  const resp = await fn(body);
  _upsertEntry(kind, resp[kind]); // resp.group / resp.event / resp.tag — resolved row (handles "reactivated" too)
  _setCatalogVersion(resp.catalog_version);
  return resp;
}
```

`_patchEntry(kind, id, patch)`, `_removeEntry(kind, id)`, `_upsertEntry(kind, item)`
mirror `_patchCategory`/`_upsertCategory` but operate on
`snapshot.value.category_groups` / `events` / `tags` (keyed by `kind` →
`{ group: "category_groups", event: "events", tag: "tags" }`). Unlike
`_upsertCategory` (whose readers all sort at read), `_upsertEntry` *does* have
to re-sort its array to match `build_catalog_snapshot`'s server-side ordering —
the `groups`, events, and tags getters render their lists as-is without
sorting, so a newly added/reactivated entry would otherwise sit at the end
until the next full `/api/catalog` refresh. This client-side replication of the
server `ORDER BY` is exactly the fragility called out in the §6b scope note as
a reason to defer §6b; if §6b is done, the cleaner alternative is to make those
three getters sort at read (as `categories()` now does) and drop
`_ENTRY_COMPARATORS` entirely:

```js
const _ENTRY_COMPARATORS = {
  category_groups: (a, b) => a.sort_order - b.sort_order || a.id - b.id,
  events: (a, b) => a.date_from.localeCompare(b.date_from) || a.name.localeCompare(b.name),
  tags: (a, b) => a.id - b.id,
};

function _upsertEntry(kind, item) {
  if (!snapshot.value) return;
  const key = { group: "category_groups", event: "events", tag: "tags" }[kind];
  const list = snapshot.value[key].filter((x) => x.id !== item.id);
  list.push(item);
  list.sort(_ENTRY_COMPARATORS[key]);
  snapshot.value = { ...snapshot.value, [key]: list };
}
```

Export `visibleCategories`, `visibleCategoryByCode`, `searchCategories` in the
returned object; drop `visibleCategoriesVersion`, `loadVisibleCategories`,
`loadVisibleCategoriesIfNeeded`. The `_set*`/`_patch*`/`_upsert*`/`_remove*`
helpers are internal (not exported) — only the mutation actions call them.

`initActiveTemplate()` needs no relocation — `App.vue`'s `init()` already
calls it independently (`webapp/src/App.vue:64`), it was only redundantly
re-triggered by `loadVisibleCategories()`.

**Vestigial band-aid to drop (`loadIfNeeded`, line ~289).** The background
`load()` fired on a cache hit when `snapshot.value.frequent_categories` is empty
is a workaround for "a previous catalog mutation bug" that wiped
`frequent_categories` from the cached snapshot — i.e. exactly the
manually-synced-cache class of bug this plan removes. Once §12 lands (single
`frequent_categories` writer) and category mutations patch the snapshot in place
(never replacing it with an envelope that omits `frequent_categories`), nothing
can wipe the cached picks, so the `if (!snapshot.value.frequent_categories?.length)`
background-refetch branch becomes dead defensive code and should be removed —
`loadIfNeeded` collapses to the plain TTL check. Land this with §12 (same
root cause); if §12 is deferred, leave the band-aid in place.

### 10. `webapp/src/App.vue`

Line ~78, `handleVisibilityChange()`:

```js
// before
if (catalogStore.visibleCategoriesVersion >= 0) void catalogStore.loadVisibleCategories();
// after
void catalogStore.load();
```

`load()` does a conditional GET against `/api/catalog` (304 if unchanged) —
the one remaining "refresh on resume" path, now for the single shared
snapshot.

### 11. `webapp/src/components/CategorySheet.vue`

**Remove entirely** (all added across this session's earlier offline-retry
work, now moot since search has no network dependency):
- `SEARCH_DEBOUNCE_MS`, `SEARCH_RETRY_MS`, `debounceTimer`, `retryTimer`,
  `clearSearchTimers()`, `attemptSearch()`, `runSearch()`, `searchUnavailable`
  ref, the `onBeforeUnmount` hook, and the `.search-offline` template branch
  + CSS.

**Replace** the `watch(query, ...)` handler with a synchronous lookup:

```js
watch(query, (q) => {
  const trimmed = q.trim();
  searchResults.value = trimmed ? catalog.searchCategories(trimmed) : [];
});
```

**Replace** the open-watcher's `void catalog.loadVisibleCategoriesIfNeeded();`
with `void catalog.loadIfNeeded();` (snapshot is normally already loaded at
app startup; this is the defensive fallback `loadIfNeeded` already provides
for `snapshot`).

`groupedCategories`, `inSetResults`, `addableResults`, `visibleCategoryByCode`
calls, and `selectAddable`'s `isOnline` guard (activation/unhide are still
network mutations) are unchanged — they read from the new computed
`catalog.visibleCategories` / call the new local `catalog.searchCategories`,
same shapes as before.

### 12. Remove the duplicate `frequentCategories` store

`webapp/src/stores/frequentCategories.js` is a second cache of the same data
the catalog store already exposes as `catalog.frequentCategories` (computed
from `snapshot.frequent_categories`). The two are kept in lockstep by hand in
`flushQueue.js:52-55`, which calls **both** `catalog.applyFrequentCategories()`
and `freq.refresh()` with the identical `resp.frequent_categories` on every
post — the same "two caches of one thing, manually synced, free to drift"
shape this whole plan removes for `visibleCategories`. `ExpenseForm.vue:326-328`
already reads `catalog.frequentCategories`; only `RuleRow.vue:77` reads the
separate store.

- `webapp/src/components/RuleRow.vue`: drop `useFrequentCategoriesStore`; read
  `catalog.frequentCategories` instead (same `[{id, name}]` shape). The
  `.filter((c) => !usedCategoryIds.value.has(Number(c.id)))` at line ~77 is
  unchanged — only its source list changes.
- `webapp/src/composables/flushQueue.js`: remove the `freq` binding
  (lines ~9, 20) and the `freq.refresh(resp)` call (line ~55).
  `catalog.applyFrequentCategories(resp.frequent_categories)` (line ~52) stays —
  it is the single remaining writer.
- `webapp/src/stores/frequentCategories.js`: delete the file.

The catalog store's `frequentCategories` computed needs no change — it is
already the single source of truth, fed by `applyFrequentCategories()` on each
post and by the full snapshot on `load()`.

> Independent of the core consolidation: this can land before, after, or
> alongside §5–§11 — it touches none of the same files except `flushQueue.js`,
> and only the `freq` lines there.

---

## Tests

### Python — `tests/`

- `tests/category_templates/test_apply.py`:
  `test_used_category_dropped_from_visible_set_stays_visible` already covers
  this exact scenario (a category with expense history, placed in the new
  template's `hidden` bucket) but asserts the *old* behavior — update its
  `assert row["is_active"] == 0` to `== 1` and its docstring: the category now
  stays visible because `apply_template` itself keeps `is_active=1` (step 1),
  not because `list_visible_categories` falls back to a `has_expenses` check
  (step 3 removes that fallback entirely).
- `tests/api/test_api_category_templates.py`:
  - Remove `TestGetCategories` (both its tests — `test_returns_only_visible_grouped`
    and `test_304_on_matching_etag` — exercise the removed `/api/categories` GET
    endpoint and its ETag/304 behavior; equivalent coverage already exists for
    `GET /api/catalog` elsewhere).
  - Replace `client.get("/api/categories")` / `client.get("/api/categories/search")`
    assertions with assertions on the mutation response body itself:
    - `TestHideUnhide`, `TestMoveAndRename` — the mutation now returns
      `CatalogVersionResponse` (`{catalog_version}` only); assert the version
      bumped, then follow up with `client.get("/api/catalog")` to check
      `categories[].is_hidden` / `group_id` / `name`.
    - `TestSearchAndActivate`, `TestCreateCategory` — the mutation now returns
      `CategoryResultResponse` (`{catalog_version, category}`); assert on
      `category.{code,is_hidden,is_active,group_id,...}` directly.
    - `TestApplyTemplate` — response shape changes from `ApplyTemplateResponse`
      (`{active_template, catalog_version}`) to the full `CategoryMutationResponse`
      (`CatalogResponse` + `active_template`); update existing assertions and
      add coverage for `categories[].code` / `is_hidden` / `is_active` directly
      from this response.
  - `test_search_finds_hidden_category`: replace the
    `/api/categories/search` call with a `/api/catalog` check that the hidden
    category is present with `is_hidden: true`.
- `tests/category_templates/test_search_activate.py`: remove the
  `search_categories()` cases (DB function removed); keep
  `list_visible_categories()` cases, simplified per §3.
  - `TestActivateCategory` (§4b): `test_places_in_active_template_group_when_unplaced`
    is unaffected (template coverage still resolves "fruit" → "food"). Add a
    case asserting `activate_category` raises `ValueError` when `group_id`
    is `NULL` and there is no active template (use a connection that only
    ran `category_seed.seed_category_templates`, without `apply_template`).
- `tests/category_templates/test_visibility.py`, `test_create.py`:
  unaffected (`list_visible_categories()` stays, simplified predicate).
- **Invariant guard (new test).** Both the simplified
  `VISIBLE_CATEGORY_PREDICATE` (§2) and the frontend `visibleCategories`
  computed (§9) now rely on `category_seed._retire_vanished` setting
  `is_active=0` *together with* `is_retired=1` — if a future change retired a
  row without clearing `is_active`, retired categories would leak into the
  picker in two places at once. Add a test (in
  `tests/category_templates/test_seed.py` or wherever `_retire_vanished` is
  covered) asserting that after `_retire_vanished` runs, every row it retired
  has both `is_retired=1` **and** `is_active=0`, so a regression trips here
  rather than silently surfacing retired categories.
- `tests/test_webapp_api_contract.py`: re-run — should pass once the two
  routes and their frontend call sites are both gone.
- `tests/api/test_admin_catalog_delete.py` (and any other test asserting on
  `AdminCatalogResponse`'s `category_groups`/`categories`/`events`/`tags`
  arrays): update to the new per-endpoint shapes —
  `GroupAddResponse`/`EventAddResponse`/`TagAddResponse`
  (`{catalog_version, status, group|event|tag}`) for `add`, and
  `AdminMutationResponse` (`{catalog_version, delete_status, usage_count}`)
  for `patch`/`delete`. Follow up with `client.get("/api/catalog")` where the
  test needs the full list.

### JS — `webapp/tests/`

- `webapp/tests/CategorySheet.test.js`: largest change.
  - `mountSheet()` / setup helpers currently seed `store.visibleCategories`
    directly and mock `catalogApi.getCategories()` /
    `catalogApi.searchCategories()`. Replace with seeding
    `store.snapshot.categories` (+ `category_groups`) with the new fields
    (`code`, `is_hidden`, `is_retired`), so `visibleCategories` and
    `searchCategories()` derive correctly. Add a case with an `is_retired:
    true` row to assert `searchCategories()` excludes it.
  - Remove the entire "CategorySheet — search while offline" describe block
    (the `SEARCH_RETRY_MS` / network-error / fake-timer tests added earlier
    this session) — local search can't fail at the network level.
  - Mutation-triggering tests now mock `catalogApi.*` per the new shapes:
    `hideCategory`/`unhideCategory`/`renameCategory`/`moveCategory` →
    `{catalog_version}`; `activateCategory`/`createCategory` →
    `{catalog_version, category}`. Assert the store's local patch
    (`_patchCategory`/`_upsertCategory`) updated the matching entry in
    `store.snapshot.categories` and that `store.snapshot.catalog_version`
    bumped.
  - Keep the "blocks activation while offline" test (`selectAddable`'s
    `isOnline` guard is unchanged).

- `webapp/tests/store-catalog.test.js`: replace `catalogApi.getCategories()`
  mocks with snapshot-based setup; add/extend coverage for the new
  `visibleCategories` computed, `searchCategories()`, the local-patch category
  mutation actions (`{catalog_version}` / `{catalog_version, category}`
  mocks), `applyTemplate` (full-snapshot-shaped mock), and the admin
  `add`/`patch`/`remove`/`reactivate`/`deactivate` actions — mock
  `adminAddGroup` etc. with `{catalog_version, status, group|event|tag}` and
  `adminDeleteGroup` etc. with `{catalog_version, delete_status, usage_count}`;
  assert `_upsertEntry`/`_patchEntry`/`_removeEntry` updated
  `store.snapshot.category_groups`/`events`/`tags` accordingly (including the
  hard-vs-soft delete branch and the `status: "reactivated"` branch for
  `add`).

- `webapp/tests/TemplateSwitchSheet.test.js`: remove the
  `catalogApi.getCategories()` mock (line ~66) if nothing else needs it;
  `applyTemplate` mock now returns a full snapshot + `active_template`.

- `webapp/tests/api-catalog.test.js`: remove cases for `getCategories()` /
  `searchCategories()`; update any `adminAddGroup`/`adminPatchGroup`/
  `adminDeleteGroup` (and event/tag equivalents) fixtures from full-snapshot
  responses to the new per-endpoint shapes.

- **§12 (frequentCategories store removal):**
  `webapp/tests/frequentCategories.test.js` — delete (the store is gone).
  `webapp/tests/component-rule-row.test.js` — seed
  `store.snapshot.frequent_categories` (or stub `catalog.frequentCategories`)
  instead of the old `frequentCategories` store; assertions on the filtered
  list are otherwise unchanged. Any `flushQueue` test that asserted on
  `freq.refresh` drops that expectation; the `catalog.frequentCategories`
  assertion already covers the surviving path.

---

## Specs

- `specs/ui/components.md` (line ~76): `CategorySheet` description says
  search is "~300ms debounced". Update to describe it as instant/local
  (no debounce — filtering happens against the already-cached catalog).
- `specs/ui/patterns.md` (line ~142): same "debounced ~300ms" phrase in the
  search-and-manage pattern description — update similarly.
- `specs/reference/category-templates.md`: update the "pickable for new
  expenses" predicate description to the simplified invariant (`is_active`
  alone, maintained by `apply_template` + auto-activate-on-use) — drop the
  separate "or has at least one expense" framing if the spec spells out the
  old `OR EXISTS` mechanics.

---

## Verification

```
uv run inv pre        # ruff + ruff-format + pyrefly + hooks → "All checks passed!", 0 errors
uv run pytest         # N passed
cd webapp && npm test # all green, zero stderr ECONNREFUSED:3000
```

Manual smoke (`uv run inv dev --rebuild`):

- Open CategorySheet, type a query — results appear instantly (no spinner/
  debounce delay), including "Not in your set" addable matches.
- With the network disabled (devtools offline / airplane mode): search still
  returns full results from cache; only *activating* an addable/hidden result
  shows "Not available offline".
- Hide/unhide/rename/move/create a category, switch category set, in Manage
  mode — the picker list and search results reflect the change immediately
  (single POST, delta or snapshot applied from its response, no follow-up
  GET).
- Switch to a category set that hides a category with existing expense
  history — it stays visible/pickable (was the point of `has_expenses`,
  now guaranteed by `apply_template`'s fix).
- An expense whose category was later retired (`category_seed._retire_vanished`)
  still shows its category name in Review/Edit/Correction sheets —
  `findCategoryById` resolves it from the retired row, which the snapshot
  retains.
- In `ManageList` (groups/events/tags, via `ExpenseForm`/`CatalogSelectField`):
  add, rename, deactivate, reactivate, and delete an entry of each kind — the
  list updates immediately from each response's resolved row/local patch, no
  full-catalog refetch. Delete an entry that's still referenced by an expense
  and confirm it soft-deactivates instead of disappearing (`delete_status:
  "soft"`).

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
change; it gets the same treatment. `apply_template` (which genuinely
rewrites most of `category_groups`/`categories`) is the one case that keeps
returning — and applying — the full snapshot inline. Either way, a mutation is
a single POST round trip: no follow-up GET to refresh the cache.

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
  `webapp/src/composables/flushQueue.js:76-78`: every `POST /api/expenses`
  already returns `catalog_version`, and if it doesn't match the cached one,
  the frontend calls `catalog.load()` (conditional GET, returns the fresh
  snapshot because the ETag now differs). No new consistency mechanism is
  introduced — this plan just stops manufacturing reasons to bypass it.
- **`apply_template` is the genuine exception.** It rewrites most of
  `category_groups` and many `categories` rows in ways the frontend cannot
  reconstruct from the request alone, so it returns (and the frontend applies)
  the full snapshot — this is "we tell the backend what to do and get the new
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
  **only** place that sets `is_active = 0` for an existing category — for
  every code placed in the new template's `hidden` bucket, unconditionally.

Fix `apply_template` to never deactivate a category that already has expenses.
Then `is_active` always already equals `(is_active OR has_expenses)`, and the
predicate collapses to `NOT is_retired AND NOT is_hidden AND is_active`
everywhere — no `has_expenses` field, no `EXISTS` subquery, in the snapshot or
in any of the 4 call sites.

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
that can deactivate a category, and it never does so for one with expenses."

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
history (from a past template switch, before this fix). Add a small migration
(or a one-off `UPDATE`, run once against the personal dev DB) to reconcile:

```sql
UPDATE categories SET is_active = 1
WHERE is_active = 0
  AND id IN (SELECT DISTINCT category_id FROM expenses);
```

---

## Backend: snapshot + endpoint changes

### 5. Augment `build_catalog_snapshot()` — `src/dinary/api/controllers/catalog.py`

**Models** (lines ~22-36): `CategoryGroupItem` gains `code: str`; `CategoryItem`
gains `code: str` and `is_hidden: bool`.

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
exclude retired:

```python
category_rows = con.execute(
    "SELECT c.id, c.code, c.name, c.group_id, g.name, c.is_active, c.is_hidden"
    " FROM categories c JOIN category_groups g ON g.id = c.group_id"
    " WHERE NOT c.is_retired"
    " ORDER BY g.sort_order, c.name",
).fetchall()
```

Dict per category (currently lines ~301-309) gains `code`, `is_hidden`:

```python
{
    "id": int(r[0]),
    "code": str(r[1]),
    "name": str(r[2]),
    "group_id": int(r[3]),
    "group": str(r[4]),
    "is_active": bool(r[5]),
    "is_hidden": bool(r[6]),
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

**Base response** (`src/dinary/api/controllers/catalog.py`, near
`CategoryItem`):

```python
class CatalogVersionResponse(BaseModel):
    catalog_version: int
```

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
        "SELECT c.id, c.code, c.name, c.group_id, g.name, c.is_active, c.is_hidden"
        " FROM categories c JOIN category_groups g ON g.id = c.group_id"
        " WHERE c.code = ?",
        [code],
    ).fetchone()
    refs = con.execute(
        "SELECT (SELECT COUNT(*) FROM expenses WHERE category_id = ?)"
        " + (SELECT COUNT(*) FROM sheet_mapping WHERE category_id = ?)"
        " + (SELECT COUNT(*) FROM import_mapping WHERE category_id = ?)",
        [row["id"], row["id"], row["id"]],
    ).fetchone()
    return CategoryItem(
        id=int(row["id"]), code=str(row["code"]), name=str(row["name"]),
        group_id=int(row["group_id"]), group=str(row["group_name"]),
        is_active=bool(row["is_active"]), is_hidden=bool(row["is_hidden"]),
        removable=int(refs[0]) == 0,
    )
```

In `src/dinary/api/controllers/category_templates.py`:

```python
def activate_category_sync(con: sqlite3.Connection, code: str) -> CategoryResultResponse:
    activate_category(con, code)
    return CategoryResultResponse(catalog_version=get_catalog_version(con), category=_category_item(con, code))


def create_category_sync(con: sqlite3.Connection, body: CreateCategoryBody) -> CategoryResultResponse:
    code = create_category(con, body.name, body.group_code)
    return CategoryResultResponse(catalog_version=get_catalog_version(con), category=_category_item(con, code))


def hide_category_sync(con: sqlite3.Connection, code: str) -> CatalogVersionResponse:
    hide_category(con, code)
    return CatalogVersionResponse(catalog_version=get_catalog_version(con))
```

(same one-line shape for `unhide_category_sync`, `move_category_sync`,
`rename_category_sync`)

**`apply_template` keeps the full-snapshot response** (`category_groups` and
many `categories` rows genuinely change):

```python
class CategoryMutationResponse(CatalogResponse):
    active_template: str
```

```python
def apply_template_sync(con, body: ApplyTemplateBody) -> CategoryMutationResponse:
    apply_template(con, body.code, body.lang)
    return CategoryMutationResponse(**build_catalog_snapshot(con), active_template=body.code)
```

Remove now-unused models: `ApplyTemplateResponse`, `VisibleCategoryItem`,
`CategoriesResponse`, `CategorySearchItem`, `CreateCategoryResponse`.

### 6b. Admin group/event/tag mutations — drop `AdminCatalogResponse`

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

Shared per-id reference-count helper (generalizes the counting `_category_item`
already does in §6), plus one item-builder per kind:

```python
def _ref_count(con: sqlite3.Connection, row_id: int, tables_and_cols: tuple[tuple[str, str], ...]) -> int:
    total = 0
    for table, col in tables_and_cols:
        (n,) = con.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", [row_id]).fetchone()  # noqa: S608
        total += n
    return total


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
  (`get_categories`, `search_categories_endpoint`).
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
  `{catalog_version}`; `applyTemplate` unchanged (full snapshot +
  `active_template`). No signature change — these wrappers already just return
  the parsed JSON body.
- `adminAddGroup`/`adminAddEvent`/`adminAddTag` now return
  `{catalog_version, status, group|event|tag}`; `adminPatchGroup`/
  `adminPatchEvent`/`adminPatchTag` (and the `adminReactivate*`/
  `adminDeactivate*` wrappers built on them) now return `{catalog_version}`;
  `adminDeleteGroup`/`adminDeleteEvent`/`adminDeleteTag` now return
  `{catalog_version, delete_status, usage_count}`. No signature change.

### 9. `webapp/src/stores/catalog.js`

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
    .filter((c) => c.is_active && !c.is_hidden)
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

function visibleCategoryByCode(code) {
  return visibleCategories.value.find((c) => c.code === code) ?? null;
}
```

Add a synchronous local `searchCategories(q)`, replicating
`search_categories.sql`'s old shape and order (`is_active DESC, name`):

```js
function searchCategories(q) {
  if (!snapshot.value) return [];
  const needle = q.trim().toLowerCase();
  if (!needle) return [];
  return snapshot.value.categories
    .filter((c) => c.name.toLowerCase().includes(needle))
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

`applyTemplate` keeps applying the full snapshot (most of the catalog
genuinely changed):

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
`{ group: "category_groups", event: "events", tag: "tags" }`).

Export `visibleCategories`, `visibleCategoryByCode`, `searchCategories` in the
returned object; drop `visibleCategoriesVersion`, `loadVisibleCategories`,
`loadVisibleCategoriesIfNeeded`. The `_set*`/`_patch*`/`_upsert*`/`_remove*`
helpers are internal (not exported) — only the mutation actions call them.

`initActiveTemplate()` needs no relocation — `App.vue`'s `init()` already
calls it independently (`webapp/src/App.vue:64`), it was only redundantly
re-triggered by `loadVisibleCategories()`.

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

---

## Tests

### Python — `tests/`

- `tests/category_templates/test_apply.py`: add a case — a category with
  expense history, placed in the new template's `hidden` bucket, keeps
  `is_active=1` after `apply_template` (and so still appears in
  `list_visible_categories`).
- `tests/api/test_api_category_templates.py`:
  - Replace `client.get("/api/categories")` / `client.get("/api/categories/search")`
    assertions with assertions on the mutation response body itself:
    - `TestHideUnhide`, `TestMoveAndRename` — the mutation now returns
      `CatalogVersionResponse` (`{catalog_version}` only); assert the version
      bumped, then follow up with `client.get("/api/catalog")` to check
      `categories[].is_hidden` / `group_id` / `name`.
    - `TestSearchAndActivate`, `TestCreateCategory` — the mutation now returns
      `CategoryResultResponse` (`{catalog_version, category}`); assert on
      `category.{code,is_hidden,is_active,group_id,...}` directly.
    - `TestApplyTemplate` — unchanged: the mutation returns the full
      `CategoryMutationResponse` (`CatalogResponse` + `active_template`);
      assert on `categories[].code` / `is_hidden` / `is_active` directly from
      this response.
  - `test_search_finds_hidden_category`: replace the
    `/api/categories/search` call with a `/api/catalog` check that the hidden
    category is present with `is_hidden: true`.
- `tests/category_templates/test_search_activate.py`: remove the
  `search_categories()` cases (DB function removed); keep
  `list_visible_categories()` cases, simplified per §3.
- `tests/category_templates/test_visibility.py`, `test_create.py`:
  unaffected (`list_visible_categories()` stays, simplified predicate).
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
    (`code`, `is_hidden`), so `visibleCategories` and `searchCategories()`
    derive correctly.
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
- In `ManageList` (groups/events/tags, via `ExpenseForm`/`CatalogSelectField`):
  add, rename, deactivate, reactivate, and delete an entry of each kind — the
  list updates immediately from each response's resolved row/local patch, no
  full-catalog refetch. Delete an entry that's still referenced by an expense
  and confirm it soft-deactivates instead of disappearing (`delete_status:
  "soft"`).

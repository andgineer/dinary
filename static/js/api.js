/**
 * API client — thin fetch wrapper with relative URLs.
 *
 * Phase 2:
 *  - ``postExpense`` sends a 3D body with catalog primary keys
 *    (category_id, event_id, tag_ids). The server resolves 3D->2D at
 *    drain time from ``sheet_mapping`` (the curated ``map`` worksheet
 *    tab), so the client no longer picks a sheet target.
 *  - ``fetchCatalog`` hits the single ``/api/catalog`` endpoint. It
 *    wraps a localStorage cache keyed by catalog_version and
 *    round-trips with If-None-Match => 304 on every refresh. Steady
 *    state is zero catalog GETs per expense because POST /api/expenses
 *    returns the current catalog_version and callers only refetch
 *    when the server-side version differs.
 *  - Admin mutations (add / edit / delete / reactivate group /
 *    category / event / tag) currently have no authentication —
 *    the shared-token gate was removed pending a real authorization
 *    layer (see ``dinary.api.admin_catalog`` module docstring).
 *    Deployments must put the server behind network ACLs until then.
 */

const CATALOG_CACHE_KEY = "dinary:catalog:v1";

// Mirror of ``dinary.api.catalog._etag_for``. The value is a pure
// function of ``catalog_version`` so the client can derive it locally
// when sending ``If-None-Match``. Any change on the server must stay
// in lockstep with this helper.
function etagFor(catalogVersion) {
  return `W/"catalog-v${catalogVersion}"`;
}

export async function postExpense({
  client_expense_id,
  amount,
  currency,
  category_id,
  event_id,
  tag_ids,
  comment,
  date,
}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 30_000);
  try {
    const resp = await fetch("/api/expenses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        client_expense_id,
        amount,
        currency,
        category_id,
        event_id: event_id ?? null,
        tag_ids: tag_ids ?? [],
        comment,
        date,
      }),
      signal: ctrl.signal,
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      const e = new Error(err.detail || `HTTP ${resp.status}`);
      e.status = resp.status;
      throw e;
    }
    return resp.json();
  } finally {
    clearTimeout(timer);
  }
}

export async function parseQr(url) {
  const resp = await fetch("/api/qr/parse", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

// ---------------------------------------------------------------------------
// Catalog cache (ETag + localStorage)
// ---------------------------------------------------------------------------

function readCachedCatalog() {
  try {
    const raw = localStorage.getItem(CATALOG_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (
      !parsed ||
      typeof parsed !== "object" ||
      typeof parsed.catalog_version !== "number"
    ) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function writeCachedCatalog(snapshot) {
  try {
    localStorage.setItem(
      CATALOG_CACHE_KEY,
      JSON.stringify(toCatalogSnapshot(snapshot)),
    );
  } catch {
    // Quota or privacy-mode storage failure: next call will refetch.
  }
}

/**
 * Fetch the full catalog snapshot. Uses localStorage cache + ETag to
 * avoid downloading the full payload when nothing changed. Callers
 * should call this:
 *   - once on app init;
 *   - again after a POST /api/expenses response where
 *     response.catalog_version differs from the cached version.
 */
export async function fetchCatalog() {
  const cached = readCachedCatalog();
  const headers = {};
  if (cached) headers["If-None-Match"] = etagFor(cached.catalog_version);
  const resp = await fetch("/api/catalog", { headers });
  if (resp.status === 304 && cached) return cached;
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${resp.status}`);
  }
  const snapshot = await resp.json();
  writeCachedCatalog(snapshot);
  return snapshot;
}

export function cachedCatalogVersion() {
  const cached = readCachedCatalog();
  return cached ? cached.catalog_version : -1;
}

export function replaceCachedCatalog(snapshot) {
  writeCachedCatalog(snapshot);
}

// ---------------------------------------------------------------------------
// Admin catalog mutations
// ---------------------------------------------------------------------------

async function adminRequest(path, { method, body } = { method: "GET" }) {
  const init = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) init.body = JSON.stringify(body);
  const resp = await fetch(path, init);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    const e = new Error(err.detail || `HTTP ${resp.status}`);
    e.status = resp.status;
    throw e;
  }
  const snapshot = await resp.json();
  writeCachedCatalog(snapshot);
  return snapshot;
}

function toCatalogSnapshot(adminResponse) {
  // Keep in sync with ``build_catalog_snapshot`` on the server. The
  // admin envelope wraps the same dict-of-lists plus admin-only
  // fields (``new_id`` / ``status`` / ``delete_status`` /
  // ``usage_count``); we strip them here so the localStorage cache
  // mirrors exactly the ``GET /api/catalog`` shape.
  const {
    catalog_version,
    category_groups,
    categories,
    events,
    tags,
  } = adminResponse;
  return { catalog_version, category_groups, categories, events, tags };
}

export async function adminAddGroup({ name, sort_order }) {
  return adminRequest("/api/admin/catalog/groups", {
    method: "POST",
    body: { name, sort_order: sort_order ?? null },
  });
}

export async function adminAddCategory({ name, group_id, sheet_name, sheet_group }) {
  return adminRequest("/api/admin/catalog/categories", {
    method: "POST",
    body: {
      name,
      group_id,
      sheet_name: sheet_name ?? null,
      sheet_group: sheet_group ?? null,
    },
  });
}

export async function adminAddEvent({
  name,
  date_from,
  date_to,
  auto_attach_enabled,
  auto_tags,
}) {
  return adminRequest("/api/admin/catalog/events", {
    method: "POST",
    body: {
      name,
      date_from,
      date_to,
      auto_attach_enabled: auto_attach_enabled ?? false,
      auto_tags: auto_tags ?? null,
    },
  });
}

export async function adminAddTag({ name }) {
  return adminRequest("/api/admin/catalog/tags", {
    method: "POST",
    body: { name },
  });
}

// PATCH helpers used by the reactivate-in-picker affordance. The body
// carries only the fields the caller wants to change; server-side
// ``edit_*`` treats ``null`` / missing as "leave alone".

export async function adminPatchGroup(group_id, body) {
  return adminRequest(`/api/admin/catalog/groups/${group_id}`, {
    method: "PATCH",
    body,
  });
}

export async function adminPatchCategory(category_id, body) {
  return adminRequest(`/api/admin/catalog/categories/${category_id}`, {
    method: "PATCH",
    body,
  });
}

export async function adminPatchEvent(event_id, body) {
  return adminRequest(`/api/admin/catalog/events/${event_id}`, {
    method: "PATCH",
    body,
  });
}

export async function adminPatchTag(tag_id, body) {
  return adminRequest(`/api/admin/catalog/tags/${tag_id}`, {
    method: "PATCH",
    body,
  });
}

// Convenience reactivation / deactivation helpers: one PATCH flipping
// ``is_active``. Deactivation is the symmetric operation the picker
// "Manage" list exposes for active rows — no destructive semantics,
// no usage check; the row is simply hidden from normal dropdowns and
// can be re-surfaced via reactivation. Hard vs soft delete stays on
// the DELETE endpoint.

export async function adminReactivateGroup(group_id) {
  return adminPatchGroup(group_id, { is_active: true });
}

export async function adminReactivateCategory(category_id) {
  return adminPatchCategory(category_id, { is_active: true });
}

export async function adminReactivateEvent(event_id) {
  return adminPatchEvent(event_id, { is_active: true });
}

export async function adminReactivateTag(tag_id) {
  return adminPatchTag(tag_id, { is_active: true });
}

export async function adminDeactivateGroup(group_id) {
  return adminPatchGroup(group_id, { is_active: false });
}

export async function adminDeactivateCategory(category_id) {
  return adminPatchCategory(category_id, { is_active: false });
}

export async function adminDeactivateEvent(event_id) {
  return adminPatchEvent(event_id, { is_active: false });
}

export async function adminDeactivateTag(tag_id) {
  return adminPatchTag(tag_id, { is_active: false });
}

// DELETE helpers. Server decides hard vs soft based on whether the row
// is still referenced by expenses or any mapping table; the caller
// just asks for "remove" and inspects ``delete_status`` / ``usage_count``
// on the returned snapshot to phrase the toast ("deleted" vs
// "hidden — still used in N expenses").

export async function adminDeleteGroup(group_id) {
  return adminRequest(`/api/admin/catalog/groups/${group_id}`, {
    method: "DELETE",
  });
}

export async function adminDeleteCategory(category_id) {
  return adminRequest(`/api/admin/catalog/categories/${category_id}`, {
    method: "DELETE",
  });
}

export async function adminDeleteEvent(event_id) {
  return adminRequest(`/api/admin/catalog/events/${event_id}`, {
    method: "DELETE",
  });
}

export async function adminDeleteTag(tag_id) {
  return adminRequest(`/api/admin/catalog/tags/${tag_id}`, {
    method: "DELETE",
  });
}

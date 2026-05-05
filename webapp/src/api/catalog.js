// Pure fetch wrappers around the catalog and admin-catalog APIs.
// State (snapshot, ETag-based caching) is owned by stores/catalog.js;
// these helpers never touch localStorage or the DOM.

// Mirror of ``dinary.api.catalog._etag_for``. The value is a pure
// function of ``catalog_version`` so callers can build ``If-None-Match``
// without making an extra request. Any change on the server must stay
// in lockstep with this helper.
export function etagFor(catalogVersion) {
  return `W/"catalog-v${catalogVersion}"`;
}

export class NotModified {
  constructor() {
    this.notModified = true;
  }
}

/**
 * Fetch the full catalog snapshot. Pass the previous catalog_version
 * (if any) to enable conditional GET — the server returns 304 and this
 * helper returns ``{ notModified: true }`` so the caller keeps its
 * existing cache.
 *
 * Returns either a full catalog snapshot ({catalog_version, ...}) or a
 * NotModified instance.
 */
export async function fetchCatalog({ ifVersion } = {}) {
  const headers = {};
  if (typeof ifVersion === "number") {
    headers["If-None-Match"] = etagFor(ifVersion);
  }
  const resp = await fetch("/api/catalog", { headers });
  if (resp.status === 304) {
    return new NotModified();
  }
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

// ---------------------------------------------------------------------------
// Admin catalog mutations
// ---------------------------------------------------------------------------

async function adminRequest(path, { method = "GET", body } = {}) {
  const init = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) init.body = JSON.stringify(body);
  const resp = await fetch(path, init);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    const e = new Error(err.detail || `HTTP ${resp.status}`);
    e.status = resp.status;
    throw e;
  }
  return resp.json();
}

// Admin envelope wraps build_catalog_snapshot plus admin-only fields
// (new_id / status / delete_status / usage_count). The store strips them
// when it caches the snapshot.

export async function adminAddGroup({ name, sort_order } = {}) {
  return adminRequest("/api/admin/catalog/groups", {
    method: "POST",
    body: { name, sort_order: sort_order ?? null },
  });
}

export async function adminAddCategory({ name, group_id, sheet_name, sheet_group } = {}) {
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
} = {}) {
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

export async function adminAddTag({ name } = {}) {
  return adminRequest("/api/admin/catalog/tags", {
    method: "POST",
    body: { name },
  });
}

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

export const adminReactivateGroup = (id) => adminPatchGroup(id, { is_active: true });
export const adminReactivateCategory = (id) => adminPatchCategory(id, { is_active: true });
export const adminReactivateEvent = (id) => adminPatchEvent(id, { is_active: true });
export const adminReactivateTag = (id) => adminPatchTag(id, { is_active: true });

export const adminDeactivateGroup = (id) => adminPatchGroup(id, { is_active: false });
export const adminDeactivateCategory = (id) => adminPatchCategory(id, { is_active: false });
export const adminDeactivateEvent = (id) => adminPatchEvent(id, { is_active: false });
export const adminDeactivateTag = (id) => adminPatchTag(id, { is_active: false });

export async function adminDeleteGroup(group_id) {
  return adminRequest(`/api/admin/catalog/groups/${group_id}`, { method: "DELETE" });
}

export async function adminDeleteCategory(category_id) {
  return adminRequest(`/api/admin/catalog/categories/${category_id}`, { method: "DELETE" });
}

export async function adminDeleteEvent(event_id) {
  return adminRequest(`/api/admin/catalog/events/${event_id}`, { method: "DELETE" });
}

export async function adminDeleteTag(tag_id) {
  return adminRequest(`/api/admin/catalog/tags/${tag_id}`, { method: "DELETE" });
}

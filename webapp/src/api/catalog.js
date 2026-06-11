// Pure fetch wrappers around the catalog APIs.
// State (snapshot, ETag-based caching) is owned by stores/catalog.js;
// these helpers never touch localStorage or the DOM.

// Mirror of ``dinary.api.controllers.catalog.etag_for``. The value is a pure
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
// Category templates (наборы категорий)
// ---------------------------------------------------------------------------

import { apiRequest } from "./_request.js";

export async function listTemplates() {
  return apiRequest("/api/category-templates");
}

export async function getActiveTemplate() {
  return apiRequest("/api/category-templates/active");
}

export async function applyTemplate(code, lang) {
  return apiRequest("/api/category-templates/apply", {
    method: "POST",
    body: { code, lang },
  });
}

/**
 * Fetch the visible-categories list. Pass the previous catalog_version
 * (if any) to enable conditional GET — the server returns 304 and this
 * helper returns ``{ notModified: true }`` so the caller keeps its
 * existing cache.
 *
 * Returns either ``{ catalog_version, categories }`` or a NotModified
 * instance.
 */
export async function getCategories({ ifVersion } = {}) {
  const headers = {};
  if (typeof ifVersion === "number") {
    headers["If-None-Match"] = etagFor(ifVersion);
  }
  const resp = await fetch("/api/categories", { headers });
  if (resp.status === 304) {
    return new NotModified();
  }
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

export async function searchCategories(q) {
  return apiRequest(`/api/categories/search?q=${encodeURIComponent(q)}`);
}

export async function createCategory(name, groupCode) {
  return apiRequest("/api/categories", {
    method: "POST",
    body: { name, group_code: groupCode },
  });
}

export async function renameCategory(code, name) {
  return apiRequest(`/api/categories/${code}/rename`, {
    method: "POST",
    body: { name },
  });
}

export async function activateCategory(code) {
  return apiRequest(`/api/categories/${code}/activate`, { method: "POST" });
}

export async function hideCategory(code) {
  return apiRequest(`/api/categories/${code}/hide`, { method: "POST" });
}

export async function unhideCategory(code) {
  return apiRequest(`/api/categories/${code}/unhide`, { method: "POST" });
}

export async function moveCategory(code, groupCode) {
  return apiRequest(`/api/categories/${code}/move`, {
    method: "POST",
    body: { group_code: groupCode },
  });
}

// ---------------------------------------------------------------------------
// Catalog mutations (groups, categories, events, tags)
// ---------------------------------------------------------------------------

export async function adminAddGroup({ name, sort_order } = {}) {
  return apiRequest("/api/catalog/groups", {
    method: "POST",
    body: { name, sort_order: sort_order ?? null },
  });
}

export async function adminAddEvent({
  name,
  date_from,
  date_to,
  auto_attach_enabled,
  auto_tags,
} = {}) {
  return apiRequest("/api/catalog/events", {
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
  return apiRequest("/api/catalog/tags", {
    method: "POST",
    body: { name },
  });
}

export async function adminPatchGroup(group_id, body) {
  return apiRequest(`/api/catalog/groups/${group_id}`, { method: "PATCH", body });
}

export async function adminPatchEvent(event_id, body) {
  return apiRequest(`/api/catalog/events/${event_id}`, { method: "PATCH", body });
}

export async function adminPatchTag(tag_id, body) {
  return apiRequest(`/api/catalog/tags/${tag_id}`, { method: "PATCH", body });
}

export const adminReactivateGroup = (id) => adminPatchGroup(id, { is_active: true });
export const adminReactivateEvent = (id) => adminPatchEvent(id, { is_active: true });
export const adminReactivateTag = (id) => adminPatchTag(id, { is_active: true });

export const adminDeactivateGroup = (id) => adminPatchGroup(id, { is_active: false });
export const adminDeactivateEvent = (id) => adminPatchEvent(id, { is_active: false });
export const adminDeactivateTag = (id) => adminPatchTag(id, { is_active: false });

export async function adminDeleteGroup(group_id) {
  return apiRequest(`/api/catalog/groups/${group_id}`, { method: "DELETE" });
}

export async function adminDeleteEvent(event_id) {
  return apiRequest(`/api/catalog/events/${event_id}`, { method: "DELETE" });
}

export async function adminDeleteTag(tag_id) {
  return apiRequest(`/api/catalog/tags/${tag_id}`, { method: "DELETE" });
}

export async function adminReloadMap() {
  return apiRequest("/api/catalog/reload-map", { method: "POST" });
}

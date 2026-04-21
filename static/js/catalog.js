/**
 * Catalog store — the PWA's cache of the 3D taxonomy
 * (category_groups, categories, events, tags).
 *
 * Backed by ``api.fetchCatalog()``, which itself is backed by an
 * ETag-aware localStorage cache. This module exposes higher-level
 * helpers (dropdown population, event filtering, group->category
 * drill-down).
 *
 * Active-vs-inactive policy:
 *
 *  - ``GET /api/catalog`` returns *every* row (active + inactive).
 *  - Dropdowns / tag lists show only ``is_active = true`` items by
 *    default. Each picker carries a per-picker ``Показать неактивные``
 *    toggle that surfaces inactive rows next to the picker with a
 *    ``Активировать`` button (which PATCHes ``is_active = true`` via
 *    ``adminReactivate*``).
 */

import {
  adminDeactivateCategory,
  adminDeactivateEvent,
  adminDeactivateGroup,
  adminDeactivateTag,
  adminDeleteCategory,
  adminDeleteEvent,
  adminDeleteGroup,
  adminDeleteTag,
  adminReactivateCategory,
  adminReactivateEvent,
  adminReactivateGroup,
  adminReactivateTag,
  fetchCatalog,
  replaceCachedCatalog,
} from "./api.js";

const MS_PER_DAY = 24 * 60 * 60 * 1000;
const EVENT_WINDOW_DAYS = 30;

let _snapshot = null;
let _lastError = null;

export async function loadCatalog() {
  _lastError = null;
  try {
    _snapshot = await fetchCatalog();
  } catch (e) {
    _lastError = e;
    console.error("Failed to fetch catalog:", e);
  }
  return _snapshot;
}

export function replaceSnapshot(snapshot) {
  _snapshot = snapshot;
  replaceCachedCatalog(snapshot);
}

export function getLastError() {
  return _lastError;
}

export function getSnapshot() {
  return _snapshot;
}

export function getCatalogVersion() {
  return _snapshot ? _snapshot.catalog_version : -1;
}

// ---------------------------------------------------------------------------
// Active/inactive helpers
// ---------------------------------------------------------------------------

function isActive(item) {
  // Be lenient with older cached snapshots that pre-date the
  // ``is_active`` column: treat missing as active so the picker
  // doesn't silently empty itself during a version upgrade.
  return item.is_active !== false;
}

// ---------------------------------------------------------------------------
// Groups + categories
// ---------------------------------------------------------------------------

export function getGroups({ includeInactive = false } = {}) {
  if (!_snapshot) return [];
  return includeInactive
    ? _snapshot.category_groups.slice()
    : _snapshot.category_groups.filter(isActive);
}

export function getInactiveGroups() {
  if (!_snapshot) return [];
  return _snapshot.category_groups.filter((g) => !isActive(g));
}

export function getCategoriesByGroup(groupId, { includeInactive = false } = {}) {
  if (!_snapshot) return [];
  const gid = Number(groupId);
  return _snapshot.categories.filter(
    (c) => c.group_id === gid && (includeInactive || isActive(c)),
  );
}

export function getInactiveCategoriesByGroup(groupId) {
  if (!_snapshot) return [];
  const gid = Number(groupId);
  return _snapshot.categories.filter((c) => c.group_id === gid && !isActive(c));
}

export function findCategoryById(categoryId) {
  if (!_snapshot) return null;
  const cid = Number(categoryId);
  return _snapshot.categories.find((c) => c.id === cid) || null;
}

// Case-insensitive, locale-aware name match. Cyrillic "Еда" vs "еда"
// (the PWA default constants use lowercase, but the seeded catalog
// ships capitalised group names) used to miss each other with plain
// ``===``, which left the category dropdown stuck on its HTML
// placeholder after first paint. ``localeCompare`` with the
// ``sensitivity: "accent"`` option treats case as equivalent while
// still distinguishing accents.
function namesEqual(a, b) {
  if (a == null || b == null) return false;
  return String(a).localeCompare(String(b), undefined, { sensitivity: "accent" }) === 0;
}

export function findCategoryByName(name, { groupId = null } = {}) {
  if (!_snapshot) return null;
  return (
    _snapshot.categories.find(
      (c) =>
        namesEqual(c.name, name) &&
        (groupId === null || c.group_id === Number(groupId)),
    ) || null
  );
}

export function findGroupByName(name) {
  if (!_snapshot) return null;
  return _snapshot.category_groups.find((g) => namesEqual(g.name, name)) || null;
}

export function populateGroupDropdown(selectEl) {
  selectEl.innerHTML = "";
  for (const g of getGroups()) {
    const opt = document.createElement("option");
    opt.value = String(g.id);
    opt.textContent = g.name || "—";
    selectEl.appendChild(opt);
  }
}

export function populateCategoryDropdown(selectEl, groupId) {
  selectEl.innerHTML = "";
  for (const c of getCategoriesByGroup(groupId)) {
    const opt = document.createElement("option");
    opt.value = String(c.id);
    opt.textContent = c.name;
    selectEl.appendChild(opt);
  }
}

// ---------------------------------------------------------------------------
// Events — filter to [anchor-30d, anchor+30d]
// ---------------------------------------------------------------------------

function parseIsoDate(s) {
  const parts = s.split("-").map(Number);
  return new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
}

function toUtcMidnight(d) {
  return new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
}

function anchorToUtcDate(anchor) {
  return typeof anchor === "string" ? parseIsoDate(anchor) : toUtcMidnight(anchor);
}

/**
 * Events active within ±30 days of ``anchor`` — overlap test with
 * [date_from, date_to]. Inactive events are hidden unless
 * ``includeInactive`` is true.
 */
export function getActiveEvents(anchor = new Date(), { includeInactive = false } = {}) {
  if (!_snapshot) return [];
  const anchorUtc = anchorToUtcDate(anchor);
  const start = new Date(anchorUtc.getTime() - EVENT_WINDOW_DAYS * MS_PER_DAY);
  const end = new Date(anchorUtc.getTime() + EVENT_WINDOW_DAYS * MS_PER_DAY);
  return _snapshot.events.filter((e) => {
    if (!includeInactive && !isActive(e)) return false;
    const from = parseIsoDate(e.date_from);
    const to = parseIsoDate(e.date_to);
    return from <= end && to >= start;
  });
}

export function getInactiveEventsInWindow(anchor = new Date()) {
  if (!_snapshot) return [];
  const anchorUtc = anchorToUtcDate(anchor);
  const start = new Date(anchorUtc.getTime() - EVENT_WINDOW_DAYS * MS_PER_DAY);
  const end = new Date(anchorUtc.getTime() + EVENT_WINDOW_DAYS * MS_PER_DAY);
  return _snapshot.events.filter((e) => {
    if (isActive(e)) return false;
    const from = parseIsoDate(e.date_from);
    const to = parseIsoDate(e.date_to);
    return from <= end && to >= start;
  });
}

/**
 * Active events whose [date_from, date_to] range contains
 * ``anchor`` *and* that opt into auto-attach (``auto_attach_enabled ==
 * true``). Used by the "auto-select event when date is in an
 * ongoing trip" affordance.
 *
 * We return *all* matches ordered by the most specific (shortest
 * range) first so a hand-curated "Доломиты Апрель" wins over the
 * catch-all "отпуск-2026" without the caller having to know the
 * priority rule.
 */
export function getAutoAttachEventsOn(anchor = new Date()) {
  if (!_snapshot) return [];
  const anchorUtc = anchorToUtcDate(anchor);
  return _snapshot.events
    .filter((e) => {
      if (!isActive(e)) return false;
      if (!e.auto_attach_enabled) return false;
      const from = parseIsoDate(e.date_from);
      const to = parseIsoDate(e.date_to);
      return from <= anchorUtc && anchorUtc <= to;
    })
    .sort((a, b) => {
      const ra = parseIsoDate(a.date_to) - parseIsoDate(a.date_from);
      const rb = parseIsoDate(b.date_to) - parseIsoDate(b.date_from);
      return ra - rb;
    });
}

export function populateEventDropdown(selectEl, anchor = new Date()) {
  selectEl.innerHTML = "";
  const active = getActiveEvents(anchor);
  const none = document.createElement("option");
  none.value = "";
  none.textContent = active.length === 0 ? "— нет активных —" : "— без события —";
  selectEl.appendChild(none);
  for (const ev of active) {
    const opt = document.createElement("option");
    opt.value = String(ev.id);
    opt.textContent = ev.name;
    selectEl.appendChild(opt);
  }
}

// ---------------------------------------------------------------------------
// Tags
// ---------------------------------------------------------------------------

export function getTags({ includeInactive = false } = {}) {
  if (!_snapshot) return [];
  return includeInactive
    ? _snapshot.tags.slice()
    : _snapshot.tags.filter(isActive);
}

export function getInactiveTags() {
  if (!_snapshot) return [];
  return _snapshot.tags.filter((t) => !isActive(t));
}

export function populateTagsList(containerEl) {
  const previouslySelected = new Set(
    readSelectedTagIds(containerEl).map((n) => String(n)),
  );
  containerEl.innerHTML = "";
  for (const tag of getTags()) {
    const label = document.createElement("label");
    label.className = "tag-chip";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.name = "tag";
    cb.value = String(tag.id);
    if (previouslySelected.has(String(tag.id))) {
      cb.checked = true;
    }
    label.appendChild(cb);
    const span = document.createElement("span");
    span.textContent = tag.name;
    label.appendChild(span);
    containerEl.appendChild(label);
  }
}

export function readSelectedTagIds(containerEl) {
  return Array.from(containerEl.querySelectorAll("input[name=tag]:checked")).map((el) =>
    Number(el.value),
  );
}

// ---------------------------------------------------------------------------
// Reactivation (per-picker "Активировать" button)
// ---------------------------------------------------------------------------

export async function reactivateGroup(groupId) {
  const snap = await adminReactivateGroup(groupId);
  replaceSnapshot(snap);
  return snap;
}

export async function reactivateCategory(categoryId) {
  const snap = await adminReactivateCategory(categoryId);
  replaceSnapshot(snap);
  return snap;
}

export async function reactivateEvent(eventId) {
  const snap = await adminReactivateEvent(eventId);
  replaceSnapshot(snap);
  return snap;
}

export async function reactivateTag(tagId) {
  const snap = await adminReactivateTag(tagId);
  replaceSnapshot(snap);
  return snap;
}

// ---------------------------------------------------------------------------
// Deactivation (per-picker "Скрыть" button on active rows)
//
// Symmetric to reactivation: flips ``is_active`` to false without
// touching row references. The row disappears from the normal
// dropdown and becomes surfaced again only in the "Управлять" list
// until the operator either reactivates it or hard-deletes via the
// "Удалить" path.
// ---------------------------------------------------------------------------

export async function deactivateGroup(groupId) {
  const snap = await adminDeactivateGroup(groupId);
  replaceSnapshot(snap);
  return snap;
}

export async function deactivateCategory(categoryId) {
  const snap = await adminDeactivateCategory(categoryId);
  replaceSnapshot(snap);
  return snap;
}

export async function deactivateEvent(eventId) {
  const snap = await adminDeactivateEvent(eventId);
  replaceSnapshot(snap);
  return snap;
}

export async function deactivateTag(tagId) {
  const snap = await adminDeactivateTag(tagId);
  replaceSnapshot(snap);
  return snap;
}

// ---------------------------------------------------------------------------
// Deletion (per-picker "Удалить" button on inactive rows)
//
// Hard-vs-soft is decided server-side: if the row is still referenced
// by any expense or by a mapping table, the server keeps it
// (``delete_status === "soft"``) and returns a ``usage_count``; the
// UI translates that into a toast so the operator understands why the
// row did not actually disappear.
// ---------------------------------------------------------------------------

export async function deleteGroup(groupId) {
  const snap = await adminDeleteGroup(groupId);
  replaceSnapshot(snap);
  return snap;
}

export async function deleteCategory(categoryId) {
  const snap = await adminDeleteCategory(categoryId);
  replaceSnapshot(snap);
  return snap;
}

export async function deleteEvent(eventId) {
  const snap = await adminDeleteEvent(eventId);
  replaceSnapshot(snap);
  return snap;
}

export async function deleteTag(tagId) {
  const snap = await adminDeleteTag(tagId);
  replaceSnapshot(snap);
  return snap;
}

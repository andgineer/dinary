/**
 * Catalog store — the PWA's cache of the 3D taxonomy
 * (category_groups, categories, events, tags).
 *
 * Backed by ``api.fetchCatalog()``, which itself is backed by an
 * ETag-aware localStorage cache. This module exposes higher-level
 * helpers (dropdown population, event filtering, group->category
 * drill-down).
 */

import { fetchCatalog, replaceCachedCatalog } from "./api.js";

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
  // Called by the admin "+ Новый" flows: the admin POST response
  // already contains the full post-mutation snapshot, so the UI can
  // refresh without another round-trip.
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
// Groups + categories
// ---------------------------------------------------------------------------

export function getGroups() {
  return _snapshot ? _snapshot.category_groups.slice() : [];
}

export function getCategoriesByGroup(groupId) {
  if (!_snapshot) return [];
  const gid = Number(groupId);
  return _snapshot.categories.filter((c) => c.group_id === gid);
}

export function findCategoryById(categoryId) {
  if (!_snapshot) return null;
  const cid = Number(categoryId);
  return _snapshot.categories.find((c) => c.id === cid) || null;
}

export function populateGroupDropdown(selectEl) {
  selectEl.innerHTML = "";
  const groups = getGroups();
  for (const g of groups) {
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
// Events — filter to [today-30d, today+30d]
// ---------------------------------------------------------------------------

function parseIsoDate(s) {
  // ``s`` is an RFC3339-ish "YYYY-MM-DD" from the server.
  const parts = s.split("-").map(Number);
  return new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
}

function toUtcMidnight(d) {
  // Collapse a ``Date`` (which the browser interprets in the user's
  // local zone) to UTC midnight of the same Y-M-D. parseIsoDate
  // already returns UTC midnight for ``YYYY-MM-DD`` strings, so both
  // sides of the overlap check end up in the same timezone and
  // events don't flicker in/out of the window based on the hour of
  // the local clock.
  return new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
}

/**
 * Events active within ±30 days of ``anchor`` — an event counts as
 * "active" on day D iff its [date_from, date_to] range overlaps the
 * window [anchor-30, anchor+30]. All event kinds are treated the
 * same regardless of origin: yearly "отпуск-YYYY" rows, explicit
 * "релокация-в-Сербию" rows and user-created events go through the
 * same filter.
 *
 * ``anchor`` can be a Date or a "YYYY-MM-DD" string; strings are
 * parsed as UTC midnight to stay aligned with the server's event
 * date columns.
 */
export function getActiveEvents(anchor = new Date()) {
  if (!_snapshot) return [];
  const anchorUtc =
    typeof anchor === "string" ? parseIsoDate(anchor) : toUtcMidnight(anchor);
  const start = new Date(anchorUtc.getTime() - EVENT_WINDOW_DAYS * MS_PER_DAY);
  const end = new Date(anchorUtc.getTime() + EVENT_WINDOW_DAYS * MS_PER_DAY);
  return _snapshot.events.filter((e) => {
    const from = parseIsoDate(e.date_from);
    const to = parseIsoDate(e.date_to);
    return from <= end && to >= start;
  });
}

/**
 * ``anchor`` is the date the operator is logging the expense for
 * (taken from the ``#date`` picker). Events are filtered relative to
 * *that* date rather than "today", so back-dating a December trip
 * still surfaces December's ``командировка-YYYY`` even when logged
 * in January.
 */
export function populateEventDropdown(selectEl, anchor = new Date()) {
  selectEl.innerHTML = "";
  const active = getActiveEvents(anchor);
  const none = document.createElement("option");
  none.value = "";
  // Distinct placeholder copy when no events fall in the window so
  // the operator doesn't suspect the dropdown is broken.
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

export function getTags() {
  return _snapshot ? _snapshot.tags.slice() : [];
}

export function populateTagsList(containerEl) {
  // Snapshot current user selection so a repopulate (e.g. after "+
  // Новый tag" adds a row) doesn't silently uncheck the in-progress
  // form state.
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

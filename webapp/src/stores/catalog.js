import { defineStore } from "pinia";
import { computed, ref } from "vue";
import * as catalogApi from "../api/catalog.js";
import { useToastStore } from "./toast.js";

const MS_PER_DAY = 24 * 60 * 60 * 1000;
const EVENT_WINDOW_DAYS = 30;
const SNAPSHOT_CACHE_KEY = "dinary:catalog:v1";
const DEFAULTS_CACHE_KEY = "dinary:defaults:v1";
const FETCHED_KEY = "dinary:catalog:fetchedAt";
const CATALOG_TTL_MS = MS_PER_DAY;

function isActive(item) {
  // Lenient: treat missing is_active as active so older cached snapshots
  // (pre-Phase-2 columns) don't silently empty the picker on upgrade.
  return item != null && item.is_active !== false;
}

function parseIsoDate(s) {
  const [y, m, d] = String(s).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

function toUtcMidnight(date) {
  return new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
}

function anchorToUtcDate(anchor) {
  return typeof anchor === "string" ? parseIsoDate(anchor) : toUtcMidnight(anchor);
}

function namesEqual(a, b) {
  if (a == null || b == null) return false;
  return String(a).localeCompare(String(b), undefined, { sensitivity: "accent" }) === 0;
}

function stripAdminEnvelope(snapshot, existingFrequentCategories = []) {
  // build_catalog_snapshot returns dict-of-lists; admin* responses also
  // include new_id / status / delete_status / usage_count. Strip those
  // before persisting the snapshot.
  // AdminCatalogResponse omits frequent_categories — fall back to the
  // previously known list so catalog mutations don't silently clear the picks.
  const { catalog_version, category_groups, categories, events, tags, frequent_categories } = snapshot ?? {};
  return {
    catalog_version,
    category_groups: category_groups ?? [],
    categories: categories ?? [],
    events: events ?? [],
    tags: tags ?? [],
    frequent_categories: frequent_categories ?? existingFrequentCategories,
  };
}

function readStoredDefaults() {
  try {
    const raw = localStorage.getItem(DEFAULTS_CACHE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function readCachedSnapshot() {
  try {
    const raw = localStorage.getItem(SNAPSHOT_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed.catalog_version !== "number") return null;
    if (!Array.isArray(parsed.frequent_categories)) return null;
    return parsed;
  } catch {
    return null;
  }
}

function writeCachedSnapshot(snapshot) {
  try {
    localStorage.setItem(SNAPSHOT_CACHE_KEY, JSON.stringify(stripAdminEnvelope(snapshot)));
  } catch {
    // Quota / private mode: harmless, next load will refetch.
  }
}

export const useCatalogStore = defineStore("catalog", () => {
  const snapshot = ref(readCachedSnapshot());
  const lastError = ref(null);
  const _defaults = ref(readStoredDefaults());
  const catalogFetchedAt = ref(Number(localStorage.getItem(FETCHED_KEY)) || null);

  function _stampFresh() {
    const now = Date.now();
    catalogFetchedAt.value = now;
    localStorage.setItem(FETCHED_KEY, String(now));
  }

  function applySnapshot(rawSnapshot) {
    if (!rawSnapshot) return;
    snapshot.value = stripAdminEnvelope(rawSnapshot, snapshot.value?.frequent_categories ?? []);
    writeCachedSnapshot(snapshot.value);
    _stampFresh();
  }

  function applyFrequentCategories(list) {
    if (!snapshot.value || !Array.isArray(list)) return;
    snapshot.value = { ...snapshot.value, frequent_categories: list };
    writeCachedSnapshot(snapshot.value);
  }

  async function load() {
    lastError.value = null;
    try {
      const cur = snapshot.value;
      const result = await catalogApi.fetchCatalog({
        ifVersion: typeof cur?.catalog_version === "number" ? cur.catalog_version : undefined,
      });
      if (result instanceof catalogApi.NotModified) {
        _stampFresh();
        return snapshot.value;
      }
      applySnapshot(result);
      return snapshot.value;
    } catch (e) {
      lastError.value = e;
      useToastStore().show(e?.message || "Failed to load catalog", "error");
      return snapshot.value;
    }
  }

  async function loadIfNeeded() {
    const age = catalogFetchedAt.value ? Date.now() - catalogFetchedAt.value : Infinity;
    if (snapshot.value && catalogFetchedAt.value && age <= CATALOG_TTL_MS) {
      // Cache hit, but if frequent_categories is empty the cached snapshot may
      // be stale (e.g. wiped by a previous catalog mutation bug). Refetch in
      // the background so picks appear without blocking the initial render.
      if (!snapshot.value.frequent_categories?.length) {
        load().catch(() => {});
      }
      return snapshot.value;
    }
    return load();
  }

  function replaceSnapshot(rawSnapshot) {
    applySnapshot(rawSnapshot);
  }

  // ----- getters ---------------------------------------------------------

  const catalogVersion = computed(() =>
    snapshot.value && typeof snapshot.value.catalog_version === "number"
      ? snapshot.value.catalog_version
      : -1,
  );

  const groups = computed(() =>
    snapshot.value ? snapshot.value.category_groups.filter(isActive) : [],
  );

  const inactiveGroups = computed(() =>
    snapshot.value ? snapshot.value.category_groups.filter((g) => !isActive(g)) : [],
  );

  function categories(groupId, { includeInactive = false } = {}) {
    if (!snapshot.value) return [];
    const gid = Number(groupId);
    return snapshot.value.categories.filter(
      (c) => c.group_id === gid && (includeInactive || isActive(c)),
    );
  }

  function inactiveCategories(groupId) {
    if (!snapshot.value) return [];
    const gid = Number(groupId);
    return snapshot.value.categories.filter((c) => c.group_id === gid && !isActive(c));
  }

  function findCategoryById(id) {
    if (!snapshot.value) return null;
    const cid = Number(id);
    return snapshot.value.categories.find((c) => c.id === cid) ?? null;
  }

  function findCategoryByName(name, { groupId = null } = {}) {
    if (!snapshot.value) return null;
    return (
      snapshot.value.categories.find(
        (c) =>
          namesEqual(c.name, name) && (groupId === null || c.group_id === Number(groupId)),
      ) ?? null
    );
  }

  function findGroupByName(name) {
    if (!snapshot.value) return null;
    return snapshot.value.category_groups.find((g) => namesEqual(g.name, name)) ?? null;
  }

  function defaultCategoryForGroup(groupId) {
    return _defaults.value?.default_category_ids?.[String(groupId)] ?? null;
  }

  const defaultGroupId = computed(() => _defaults.value?.default_group_id ?? null);

  function applyExpenseDefaults({ default_group_id, default_category_ids }) {
    if (default_group_id == null && !default_category_ids) return;
    const d = {
      default_group_id: default_group_id ?? null,
      default_category_ids: default_category_ids ?? {},
    };
    _defaults.value = d;
    try {
      localStorage.setItem(DEFAULTS_CACHE_KEY, JSON.stringify(d));
    } catch {
      // quota / private mode — harmless
    }
  }

  function events(anchor = new Date(), { includeInactive = false } = {}) {
    if (!snapshot.value) return [];
    const anchorUtc = anchorToUtcDate(anchor);
    const start = new Date(anchorUtc.getTime() - EVENT_WINDOW_DAYS * MS_PER_DAY);
    const end = new Date(anchorUtc.getTime() + EVENT_WINDOW_DAYS * MS_PER_DAY);
    return snapshot.value.events.filter((e) => {
      if (!includeInactive && !isActive(e)) return false;
      return parseIsoDate(e.date_from) <= end && parseIsoDate(e.date_to) >= start;
    });
  }

  function inactiveEventsInWindow(anchor = new Date()) {
    if (!snapshot.value) return [];
    const anchorUtc = anchorToUtcDate(anchor);
    const start = new Date(anchorUtc.getTime() - EVENT_WINDOW_DAYS * MS_PER_DAY);
    const end = new Date(anchorUtc.getTime() + EVENT_WINDOW_DAYS * MS_PER_DAY);
    return snapshot.value.events.filter((e) => {
      if (isActive(e)) return false;
      return parseIsoDate(e.date_from) <= end && parseIsoDate(e.date_to) >= start;
    });
  }

  function inactiveEventsLast(days = 365) {
    if (!snapshot.value) return [];
    const cutoff = new Date(toUtcMidnight(new Date()).getTime() - days * MS_PER_DAY);
    return snapshot.value.events.filter(
      (e) => !isActive(e) && parseIsoDate(e.date_to) >= cutoff,
    );
  }

  function autoAttachEventsOn(anchor = new Date()) {
    if (!snapshot.value) return [];
    const anchorUtc = anchorToUtcDate(anchor);
    return snapshot.value.events
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

  const frequentCategories = computed(() =>
    snapshot.value ? snapshot.value.frequent_categories ?? [] : [],
  );

  const tags = computed(() => (snapshot.value ? snapshot.value.tags.filter(isActive) : []));

  const inactiveTags = computed(() =>
    snapshot.value ? snapshot.value.tags.filter((t) => !isActive(t)) : [],
  );

  function activeEventsLast(days = 365) {
    if (!snapshot.value) return [];
    const cutoff = new Date(toUtcMidnight(new Date()).getTime() - days * MS_PER_DAY);
    return snapshot.value.events
      .filter((e) => isActive(e) && parseIsoDate(e.date_to) >= cutoff)
      .sort((a, b) => parseIsoDate(b.date_to) - parseIsoDate(a.date_to));
  }

  function findEventById(id) {
    if (!snapshot.value || id == null) return null;
    return snapshot.value.events.find((e) => e.id === Number(id)) ?? null;
  }

  // ----- mutating actions ------------------------------------------------

  async function reactivate(kind, id) {
    const fn = {
      group: catalogApi.adminReactivateGroup,
      category: catalogApi.adminReactivateCategory,
      event: catalogApi.adminReactivateEvent,
      tag: catalogApi.adminReactivateTag,
    }[kind];
    if (!fn) throw new Error(`Unknown kind: ${kind}`);
    const snap = await fn(id);
    applySnapshot(snap);
    return snap;
  }

  async function deactivate(kind, id) {
    const fn = {
      group: catalogApi.adminDeactivateGroup,
      category: catalogApi.adminDeactivateCategory,
      event: catalogApi.adminDeactivateEvent,
      tag: catalogApi.adminDeactivateTag,
    }[kind];
    if (!fn) throw new Error(`Unknown kind: ${kind}`);
    const snap = await fn(id);
    applySnapshot(snap);
    return snap;
  }

  async function remove(kind, id) {
    const fn = {
      group: catalogApi.adminDeleteGroup,
      category: catalogApi.adminDeleteCategory,
      event: catalogApi.adminDeleteEvent,
      tag: catalogApi.adminDeleteTag,
    }[kind];
    if (!fn) throw new Error(`Unknown kind: ${kind}`);
    const snap = await fn(id);
    applySnapshot(snap);
    return snap;
  }

  async function add(kind, body) {
    const fn = {
      group: catalogApi.adminAddGroup,
      category: catalogApi.adminAddCategory,
      event: catalogApi.adminAddEvent,
      tag: catalogApi.adminAddTag,
    }[kind];
    if (!fn) throw new Error(`Unknown kind: ${kind}`);
    const snap = await fn(body);
    applySnapshot(snap);
    return snap;
  }

  async function patch(kind, id, body) {
    const fn = {
      group: catalogApi.adminPatchGroup,
      category: catalogApi.adminPatchCategory,
      event: catalogApi.adminPatchEvent,
      tag: catalogApi.adminPatchTag,
    }[kind];
    if (!fn) throw new Error(`Unknown kind: ${kind}`);
    const snap = await fn(id, body);
    applySnapshot(snap);
    return snap;
  }

  return {
    snapshot,
    lastError,
    catalogVersion,
    groups,
    inactiveGroups,
    frequentCategories,
    tags,
    inactiveTags,
    categories,
    inactiveCategories,
    findCategoryById,
    findCategoryByName,
    findGroupByName,
    defaultCategoryForGroup,
    defaultGroupId,
    applyExpenseDefaults,
    applyFrequentCategories,
    events,
    activeEventsLast,
    findEventById,
    inactiveEventsInWindow,
    inactiveEventsLast,
    autoAttachEventsOn,
    catalogFetchedAt,
    load,
    loadIfNeeded,
    replaceSnapshot,
    reactivate,
    deactivate,
    remove,
    add,
    patch,
  };
});

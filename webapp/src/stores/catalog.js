import { defineStore } from "pinia";
import { computed, ref } from "vue";
import * as catalogApi from "../api/catalog.js";
import { useToastStore } from "./toast.js";
import { resolveUiLang } from "../composables/uiLang.js";

const MS_PER_DAY = 24 * 60 * 60 * 1000;
const EVENT_WINDOW_DAYS = 30;
const SNAPSHOT_CACHE_KEY = "dinary:catalog:v1";
const DEFAULTS_CACHE_KEY = "dinary:defaults:v1";
const FETCHED_KEY = "dinary:catalog:fetchedAt";
const CATALOG_TTL_MS = MS_PER_DAY;
const LAST_LANG_KEY = "dinary:catalog:lastLang";
const NUDGE_FLAG_KEY = "dinary:catalog:nudgeActive";
const ACTIVE_TEMPLATE_KEY = "dinary:catalog:activeTemplate";

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

function _normalizeSnapshot(snapshot) {
  const { catalog_version, category_groups, categories, events, tags, frequent_categories } =
    snapshot ?? {};
  return {
    catalog_version,
    category_groups: category_groups ?? [],
    categories: categories ?? [],
    events: events ?? [],
    tags: tags ?? [],
    frequent_categories: frequent_categories ?? [],
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
    localStorage.setItem(SNAPSHOT_CACHE_KEY, JSON.stringify(_normalizeSnapshot(snapshot)));
  } catch {
    // Quota / private mode: harmless, next load will refetch.
  }
}

// The active template is persisted so a returning client renders the
// correct screen (onboarding vs. main app) offline, without a network
// round-trip on every launch. undefined = never resolved (must fetch
// once, online); null = resolved to "no active template" (onboarding);
// string = the active template code.
function readStoredActiveTemplate() {
  try {
    const raw = localStorage.getItem(ACTIVE_TEMPLATE_KEY);
    return raw === null ? undefined : JSON.parse(raw);
  } catch {
    return undefined;
  }
}

function writeStoredActiveTemplate(value) {
  try {
    localStorage.setItem(ACTIVE_TEMPLATE_KEY, JSON.stringify(value));
  } catch {
    // Quota / private mode: harmless, next launch refetches.
  }
}

export const useCatalogStore = defineStore("catalog", () => {
  const snapshot = ref(readCachedSnapshot());
  const lastError = ref(null);
  const _defaults = ref(readStoredDefaults());
  const catalogFetchedAt = ref(Number(localStorage.getItem(FETCHED_KEY)) || null);

  // ----- category templates (наборы категорий) ---------------------------

  const activeTemplate = ref(readStoredActiveTemplate());
  let _resolveTemplateReady;
  const templateReady = new Promise((resolve) => {
    _resolveTemplateReady = resolve;
  });

  async function _refreshActiveTemplate() {
    try {
      const resp = await catalogApi.getActiveTemplate();
      activeTemplate.value = resp.active_template;
      writeStoredActiveTemplate(resp.active_template);
    } catch (e) {
      lastError.value = e;
    }
  }

  async function initActiveTemplate() {
    // templateReady must resolve on every path — never leave it pending,
    // even if a future change makes the refresh throw.
    try {
      // A cached value lets us render immediately and offline. The active
      // template only ever changes through applyTemplate() in this app, so
      // we don't poll it per launch — a daily background refresh (shared
      // with the catalog TTL) is enough to pick up a switch made elsewhere.
      if (activeTemplate.value !== undefined) {
        if (navigator.onLine && _isCatalogStale()) void _refreshActiveTemplate();
        return;
      }
      // First launch with nothing cached: we must learn the template before
      // we can choose onboarding vs. main app. Offline this fetch simply fails
      // and we render nothing — unavoidable until the first online load seeds.
      await _refreshActiveTemplate();
    } finally {
      _resolveTemplateReady();
    }
  }

  async function applyTemplate(code, lang) {
    const resp = await catalogApi.applyTemplate(code, lang);
    activeTemplate.value = resp.active_template;
    writeStoredActiveTemplate(resp.active_template);
    applySnapshot(resp);
    return resp;
  }

  // ----- template switcher (shared sheet + preview cache) -----------------

  const templateCatalog = ref([]);
  const templateCatalogLoaded = ref(false);
  const templateLang = ref("ru");
  const templateSwitchOpen = ref(false);

  async function ensureTemplateCatalog() {
    if (templateCatalogLoaded.value) return;
    templateCatalog.value = await catalogApi.listTemplates();
    templateCatalogLoaded.value = true;
    const available = Object.keys(templateCatalog.value[0]?.names ?? { ru: "" });
    const stored = localStorage.getItem(LAST_LANG_KEY);
    templateLang.value = stored && available.includes(stored) ? stored : resolveUiLang(available);
  }

  function persistTemplateLang() {
    localStorage.setItem(LAST_LANG_KEY, templateLang.value);
  }

  const activeTemplateName = computed(() => {
    const tpl = templateCatalog.value.find((t) => t.code === activeTemplate.value);
    if (!tpl) return activeTemplate.value ?? "";
    return tpl.names?.[templateLang.value] ?? tpl.names?.ru ?? tpl.code;
  });

  function openTemplateSwitch() {
    templateSwitchOpen.value = true;
  }

  function closeTemplateSwitch() {
    templateSwitchOpen.value = false;
  }

  // ----- out-of-set nudge banner ------------------------------------------

  const showSetNudge = ref(localStorage.getItem(NUDGE_FLAG_KEY) === "1");

  function setSetNudge(active) {
    showSetNudge.value = active;
    if (active) {
      localStorage.setItem(NUDGE_FLAG_KEY, "1");
    } else {
      localStorage.removeItem(NUDGE_FLAG_KEY);
    }
  }

  // ----- visible categories (picker / Manage mode) ------------------------

  // Doesn't repeat !c.is_retired: category_seed._retire_vanished always
  // pairs is_retired=1 with is_active=0, so isActive(c) alone excludes
  // retired rows too.
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

  function visibleCategoryByCode(code) {
    return visibleCategories.value.find((c) => c.code === code) ?? null;
  }

  function searchCategories(q) {
    if (!snapshot.value) return [];
    const needle = q.trim().toLowerCase();
    if (!needle) return [];
    return snapshot.value.categories
      .filter((c) => !c.is_retired && c.name.toLowerCase().includes(needle))
      .map((c) => ({
        id: c.id,
        code: c.code,
        name: c.name,
        is_active: c.is_active,
        is_hidden: c.is_hidden,
      }))
      .sort((a, b) => Number(b.is_active) - Number(a.is_active) || a.name.localeCompare(b.name));
  }

  // ----- local-patch helpers ----------------------------------------------
  //
  // Each mutation already knows the new state it requested, so it patches
  // snapshot.value directly and just takes catalog_version from the response.

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

  async function activateCategory(code) {
    const resp = await catalogApi.activateCategory(code);
    _upsertCategory(resp.category);
    _setCatalogVersion(resp.catalog_version);
    return resp;
  }

  async function unhideCategory(code) {
    const resp = await catalogApi.unhideCategory(code);
    _patchCategory(code, { is_hidden: false });
    _setCatalogVersion(resp.catalog_version);
    return resp;
  }

  async function hideCategory(code) {
    const resp = await catalogApi.hideCategory(code);
    _patchCategory(code, { is_hidden: true });
    _setCatalogVersion(resp.catalog_version);
    return resp;
  }

  async function renameCategory(code, name) {
    const resp = await catalogApi.renameCategory(code, name);
    _patchCategory(code, { name });
    _setCatalogVersion(resp.catalog_version);
    return resp;
  }

  async function moveCategory(code, groupCode) {
    const resp = await catalogApi.moveCategory(code, groupCode);
    const group = snapshot.value?.category_groups.find((g) => g.code === groupCode);
    if (group) _patchCategory(code, { group_id: group.id, group: group.name });
    _setCatalogVersion(resp.catalog_version);
    return resp;
  }

  async function createCategory(name, groupCode) {
    const resp = await catalogApi.createCategory(name, groupCode);
    _upsertCategory(resp.category);
    _setCatalogVersion(resp.catalog_version);
    return resp;
  }

  function _stampFresh() {
    const now = Date.now();
    catalogFetchedAt.value = now;
    localStorage.setItem(FETCHED_KEY, String(now));
  }

  function applySnapshot(rawSnapshot) {
    if (!rawSnapshot) return;
    snapshot.value = _normalizeSnapshot(rawSnapshot);
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

  function _isCatalogStale() {
    const at = catalogFetchedAt.value;
    return !at || Date.now() - at > CATALOG_TTL_MS;
  }

  async function loadIfNeeded() {
    if (snapshot.value && !_isCatalogStale()) {
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
    return snapshot.value.categories
      .filter((c) => c.group_id === gid && (includeInactive || isActive(c)))
      .sort((a, b) => a.name.localeCompare(b.name));
  }

  function inactiveCategories(groupId) {
    if (!snapshot.value) return [];
    const gid = Number(groupId);
    return snapshot.value.categories
      .filter((c) => c.group_id === gid && !isActive(c))
      .sort((a, b) => a.name.localeCompare(b.name));
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

  const _ENTRY_KEYS = { group: "category_groups", event: "events", tag: "tags" };

  const _ENTRY_COMPARATORS = {
    category_groups: (a, b) => a.sort_order - b.sort_order || a.id - b.id,
    events: (a, b) => a.date_from.localeCompare(b.date_from) || a.name.localeCompare(b.name),
    tags: (a, b) => a.id - b.id,
  };

  function _patchEntry(kind, id, patch) {
    if (!snapshot.value) return;
    const key = _ENTRY_KEYS[kind];
    snapshot.value = {
      ...snapshot.value,
      [key]: snapshot.value[key].map((x) => (x.id === id ? { ...x, ...patch } : x)),
    };
  }

  function _removeEntry(kind, id) {
    if (!snapshot.value) return;
    const key = _ENTRY_KEYS[kind];
    snapshot.value = { ...snapshot.value, [key]: snapshot.value[key].filter((x) => x.id !== id) };
  }

  function _upsertEntry(kind, item) {
    if (!snapshot.value) return;
    const key = _ENTRY_KEYS[kind];
    const list = snapshot.value[key].filter((x) => x.id !== item.id);
    list.push(item);
    list.sort(_ENTRY_COMPARATORS[key]);
    snapshot.value = { ...snapshot.value, [key]: list };
  }

  async function reactivate(kind, id) {
    return patch(kind, id, { is_active: true });
  }

  async function deactivate(kind, id) {
    return patch(kind, id, { is_active: false });
  }

  async function remove(kind, id) {
    const fn = {
      group: catalogApi.adminDeleteGroup,
      event: catalogApi.adminDeleteEvent,
      tag: catalogApi.adminDeleteTag,
    }[kind];
    if (!fn) throw new Error(`Unknown kind: ${kind}`);
    const resp = await fn(id);
    if (resp.delete_status === "hard") {
      _removeEntry(kind, id);
    } else {
      _patchEntry(kind, id, { is_active: false });
    }
    _setCatalogVersion(resp.catalog_version);
    return resp;
  }

  async function add(kind, body) {
    const fn = {
      group: catalogApi.adminAddGroup,
      event: catalogApi.adminAddEvent,
      tag: catalogApi.adminAddTag,
    }[kind];
    if (!fn) throw new Error(`Unknown kind: ${kind}`);
    const resp = await fn(body);
    _upsertEntry(kind, resp[kind]);
    _setCatalogVersion(resp.catalog_version);
    return resp;
  }

  async function patch(kind, id, body) {
    const fn = {
      group: catalogApi.adminPatchGroup,
      event: catalogApi.adminPatchEvent,
      tag: catalogApi.adminPatchTag,
    }[kind];
    if (!fn) throw new Error(`Unknown kind: ${kind}`);
    const resp = await fn(id, body);
    _patchEntry(kind, id, body);
    _setCatalogVersion(resp.catalog_version);
    return resp;
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
    activeTemplate,
    templateReady,
    initActiveTemplate,
    applyTemplate,
    templateCatalog,
    templateLang,
    templateSwitchOpen,
    ensureTemplateCatalog,
    persistTemplateLang,
    activeTemplateName,
    openTemplateSwitch,
    closeTemplateSwitch,
    showSetNudge,
    setSetNudge,
    visibleCategories,
    visibleCategoryByCode,
    searchCategories,
    activateCategory,
    unhideCategory,
    hideCategory,
    renameCategory,
    moveCategory,
    createCategory,
  };
});

/**
 * Catalog cache shape tests for ``static/js/api.js``.
 *
 * The admin API response wraps ``build_catalog_snapshot`` plus two
 * admin-only fields (``new_id``, ``status``). ``postAdmin`` must
 * strip the admin-only fields before caching, but MUST preserve the
 * full snapshot shape that ``fetchCatalog`` / ``readCachedCatalog``
 * and the ``catalog.js`` dropdown helpers expect. In particular the
 * ``category_groups`` key must survive (an earlier version of the
 * cache helper destructured ``groups`` by mistake and silently
 * stripped the real payload).
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import * as allure from "allure-js-commons";

const CATALOG_CACHE_KEY = "dinary:catalog:v1";

function installLocalStorageStub() {
  const store = new Map();
  const stub = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
    clear: () => store.clear(),
    key: (i) => Array.from(store.keys())[i] ?? null,
    get length() {
      return store.size;
    },
  };
  vi.stubGlobal("localStorage", stub);
  return stub;
}

beforeEach(async () => {
  vi.restoreAllMocks();
  vi.resetModules();
  installLocalStorageStub();
});

const ADMIN_ADD_RESPONSE = {
  new_id: 42,
  status: "created",
  catalog_version: 7,
  // Note: no ``etag`` field — the server no longer ships it in the
  // body; the PWA derives it from ``catalog_version`` via
  // ``etagFor``. These tests exercise that contract.
  category_groups: [
    { id: 1, name: "Еда", sort_order: 1 },
    { id: 2, name: "Транспорт", sort_order: 2 },
  ],
  categories: [
    { id: 10, name: "еда", group: "Еда", group_id: 1 },
    { id: 11, name: "машина", group: "Транспорт", group_id: 2 },
  ],
  events: [
    {
      id: 100,
      name: "отпуск-2026",
      date_from: "2026-01-01",
      date_to: "2026-12-31",
      auto_attach_enabled: true,
    },
  ],
  tags: [{ id: 1, name: "собака" }],
};

function mockOkFetch(body) {
  const resp = {
    ok: true,
    status: 200,
    json: async () => body,
  };
  return vi.fn().mockResolvedValue(resp);
}

async function importApi() {
  // Re-imported per test because api.js has no public reset hook and
  // we want each test to start from a clean module state.
  return await import("../../static/js/api.js");
}

describe("api.js — postAdmin catalog cache shape", () => {
  it("writes category_groups (not groups) into the cached snapshot", async () => {
    await allure.feature("Catalog cache");
    vi.stubGlobal("fetch", mockOkFetch(ADMIN_ADD_RESPONSE));

    const api = await importApi();
    const snapshot = await api.adminAddGroup({ name: "Новая" });

    expect(snapshot.new_id).toBe(42);
    expect(snapshot.status).toBe("created");

    const rawCached = localStorage.getItem(CATALOG_CACHE_KEY);
    expect(rawCached).toBeTruthy();
    const cached = JSON.parse(rawCached);
    // The caching bug we're guarding against: ``groups`` would be
    // ``undefined`` and ``category_groups`` would be absent.
    expect(cached).not.toHaveProperty("groups");
    expect(cached.category_groups).toEqual(ADMIN_ADD_RESPONSE.category_groups);
    expect(cached.categories).toEqual(ADMIN_ADD_RESPONSE.categories);
    expect(cached.events).toEqual(ADMIN_ADD_RESPONSE.events);
    expect(cached.tags).toEqual(ADMIN_ADD_RESPONSE.tags);
    expect(cached.catalog_version).toBe(7);
    // ``etag`` is not stored — it's derived at ``If-None-Match`` time
    // from the cached ``catalog_version``.
    expect(cached).not.toHaveProperty("etag");
    // Admin-only fields must not be persisted into the general cache.
    expect(cached).not.toHaveProperty("new_id");
    expect(cached).not.toHaveProperty("status");
  });

  it("cached snapshot is structurally identical to GET /api/catalog body", async () => {
    await allure.feature("Catalog cache");
    const getCatalogBody = {
      catalog_version: 7,
      category_groups: ADMIN_ADD_RESPONSE.category_groups,
      categories: ADMIN_ADD_RESPONSE.categories,
      events: ADMIN_ADD_RESPONSE.events,
      tags: ADMIN_ADD_RESPONSE.tags,
    };

    vi.stubGlobal("fetch", mockOkFetch(ADMIN_ADD_RESPONSE));
    const api = await importApi();
    await api.adminAddTag({ name: "кошка" });
    const afterAdmin = JSON.parse(localStorage.getItem(CATALOG_CACHE_KEY));

    vi.stubGlobal("fetch", mockOkFetch(getCatalogBody));
    vi.resetModules();
    installLocalStorageStub();
    const api2 = await importApi();
    await api2.fetchCatalog();
    const afterGet = JSON.parse(localStorage.getItem(CATALOG_CACHE_KEY));

    expect(Object.keys(afterAdmin).sort()).toEqual(Object.keys(afterGet).sort());
  });

  // Former "401 clears admin token" test lived here. The shared-token
  // admin gate was removed (see ``DINARY_ADMIN_API_TOKEN`` in
  // ``config.py::_DEPRECATED_ENV_REMOVED``); the PWA no longer
  // reads or writes ``dinary:admin_token``. Nothing left to guard
  // on the client for this auth mode — re-adding tests is blocked
  // on the real auth layer being re-introduced.

  it("fetchCatalog derives If-None-Match from cached catalog_version", async () => {
    // Server-side ETag stopped shipping in the body, so the client
    // must reconstruct it locally. Regression guard: if a refactor
    // forgets to call ``etagFor``, the second fetch will 200 every
    // time and the cache will churn on each reload.
    await allure.feature("Catalog cache");
    const api = await importApi();
    // Seed cache via a normal fetch first.
    vi.stubGlobal("fetch", mockOkFetch({ ...ADMIN_ADD_RESPONSE }));
    await api.fetchCatalog();

    const spy = vi.fn().mockResolvedValue({
      ok: true,
      status: 304,
      json: async () => ({}),
    });
    vi.stubGlobal("fetch", spy);
    await api.fetchCatalog();

    expect(spy).toHaveBeenCalledTimes(1);
    const [, init] = spy.mock.calls[0];
    expect(init.headers["If-None-Match"]).toBe('W/"catalog-v7"');
  });

  it("replaceCachedCatalog strips admin-only fields", async () => {
    // Guards the ``catalog-add.js -> catalog.js::replaceSnapshot
    // -> api.js::replaceCachedCatalog`` path used by the "+ Новый"
    // flow. The earlier bug: ``replaceCachedCatalog`` delegated to
    // the low-level writer without stripping, so the full admin
    // response (including ``new_id`` / ``status``) leaked into
    // ``localStorage`` behind ``postAdmin``'s back.
    await allure.feature("Catalog cache");
    const api = await importApi();
    api.replaceCachedCatalog(ADMIN_ADD_RESPONSE);
    const cached = JSON.parse(localStorage.getItem(CATALOG_CACHE_KEY));
    expect(cached).not.toHaveProperty("new_id");
    expect(cached).not.toHaveProperty("status");
    expect(cached.category_groups).toEqual(ADMIN_ADD_RESPONSE.category_groups);
    expect(cached.categories).toEqual(ADMIN_ADD_RESPONSE.categories);
    expect(cached.events).toEqual(ADMIN_ADD_RESPONSE.events);
    expect(cached.tags).toEqual(ADMIN_ADD_RESPONSE.tags);
  });
});

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useCatalogStore } from "../src/stores/catalog.js";
import * as catalogApi from "../src/api/catalog.js";

const SAMPLE = {
  catalog_version: 1,
  category_groups: [
    { id: 1, name: "food", is_active: true },
    { id: 2, name: "trips", is_active: false },
  ],
  categories: [
    { id: 10, group_id: 1, name: "cafe", is_active: true },
    { id: 11, group_id: 1, name: "snack", is_active: false },
    { id: 12, group_id: 2, name: "hotel", is_active: true },
  ],
  events: [
    {
      id: 100,
      name: "trip-april",
      date_from: "2026-04-01",
      date_to: "2026-04-10",
      auto_attach_enabled: true,
      is_active: true,
    },
    {
      id: 101,
      name: "umbrella",
      date_from: "2026-01-01",
      date_to: "2026-12-31",
      auto_attach_enabled: true,
      is_active: true,
    },
    {
      id: 102,
      name: "old",
      date_from: "2024-01-01",
      date_to: "2024-12-31",
      auto_attach_enabled: false,
      is_active: false,
    },
  ],
  tags: [
    { id: 200, name: "vacation", is_active: true },
    { id: 201, name: "old-tag", is_active: false },
  ],
};

beforeEach(() => {
  setActivePinia(createPinia());
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("catalog store: load + caching", () => {
  it("load() fetches and stores the snapshot, persisting to localStorage", async () => {
    vi.spyOn(catalogApi, "fetchCatalog").mockResolvedValueOnce({ ...SAMPLE });

    const store = useCatalogStore();
    await store.load();

    expect(store.catalogVersion).toBe(1);
    expect(store.groups).toHaveLength(1);
    expect(store.groups[0].name).toBe("food");
    const cached = JSON.parse(localStorage.getItem("dinary:catalog:v1"));
    expect(cached.catalog_version).toBe(1);
    expect(cached.new_id).toBeUndefined();
  });

  it("load() sends If-None-Match using cached catalog_version", async () => {
    const spy = vi.spyOn(catalogApi, "fetchCatalog").mockResolvedValueOnce(new catalogApi.NotModified());
    localStorage.setItem(
      "dinary:catalog:v1",
      JSON.stringify(SAMPLE),
    );

    const store = useCatalogStore();
    await store.load();

    expect(spy).toHaveBeenCalledWith({ ifVersion: 1 });
  });

  it("load() captures errors in lastError and keeps cached snapshot", async () => {
    localStorage.setItem("dinary:catalog:v1", JSON.stringify(SAMPLE));
    vi.spyOn(catalogApi, "fetchCatalog").mockRejectedValueOnce(new Error("boom"));

    const store = useCatalogStore();
    await store.load();

    expect(store.lastError?.message).toBe("boom");
    expect(store.catalogVersion).toBe(1);
  });

  it("strips admin envelope fields when caching", () => {
    const store = useCatalogStore();
    store.replaceSnapshot({
      ...SAMPLE,
      new_id: 99,
      status: "added",
      delete_status: "soft",
      usage_count: 3,
    });
    const cached = JSON.parse(localStorage.getItem("dinary:catalog:v1"));
    expect(cached.new_id).toBeUndefined();
    expect(cached.status).toBeUndefined();
    expect(cached.delete_status).toBeUndefined();
    expect(cached.usage_count).toBeUndefined();
    expect(cached.catalog_version).toBe(1);
  });
});

describe("catalog store: getters", () => {
  beforeEach(() => {
    const store = useCatalogStore();
    store.replaceSnapshot(SAMPLE);
  });

  it("groups returns only active groups; inactiveGroups returns inactive", () => {
    const store = useCatalogStore();
    expect(store.groups.map((g) => g.id)).toEqual([1]);
    expect(store.inactiveGroups.map((g) => g.id)).toEqual([2]);
  });

  it("categories(groupId) filters to active by default", () => {
    const store = useCatalogStore();
    expect(store.categories(1).map((c) => c.id)).toEqual([10]);
    expect(store.categories(1, { includeInactive: true }).map((c) => c.id)).toEqual([10, 11]);
    expect(store.inactiveCategories(1).map((c) => c.id)).toEqual([11]);
  });

  it("findCategoryById resolves both active and inactive categories", () => {
    const store = useCatalogStore();
    expect(store.findCategoryById(11)?.name).toBe("snack");
    expect(store.findCategoryById(999)).toBeNull();
  });

  it("findGroupByName / findCategoryByName are case-insensitive", () => {
    const store = useCatalogStore();
    expect(store.findGroupByName("FOOD")?.id).toBe(1);
    expect(store.findCategoryByName("CAFE", { groupId: 1 })?.id).toBe(10);
    expect(store.findCategoryByName("snack")?.id).toBe(11);
  });

  it("events(anchor) filters to ±30d window, hides inactive by default", () => {
    const store = useCatalogStore();
    const evs = store.events(new Date("2026-04-15T00:00:00Z"));
    const ids = evs.map((e) => e.id).sort();
    expect(ids).toContain(100);
    expect(ids).toContain(101);
    expect(ids).not.toContain(102);
  });

  it("autoAttachEventsOn returns the most-specific (shortest range) first", () => {
    const store = useCatalogStore();
    const evs = store.autoAttachEventsOn(new Date("2026-04-05T00:00:00Z"));
    expect(evs[0].id).toBe(100);
    expect(evs[1].id).toBe(101);
  });

  it("tags / inactiveTags partition by is_active", () => {
    const store = useCatalogStore();
    expect(store.tags.map((t) => t.id)).toEqual([200]);
    expect(store.inactiveTags.map((t) => t.id)).toEqual([201]);
  });
});

describe("catalog store: admin actions", () => {
  it("add('group', body) calls adminAddGroup and updates snapshot", async () => {
    const next = { ...SAMPLE, catalog_version: 2 };
    vi.spyOn(catalogApi, "adminAddGroup").mockResolvedValue(next);

    const store = useCatalogStore();
    await store.add("group", { name: "new" });

    expect(store.catalogVersion).toBe(2);
  });

  it("reactivate / deactivate / remove call the matching API", async () => {
    const next = { ...SAMPLE, catalog_version: 5 };
    const reactivate = vi
      .spyOn(catalogApi, "adminReactivateCategory")
      .mockResolvedValue(next);
    const deactivate = vi
      .spyOn(catalogApi, "adminDeactivateEvent")
      .mockResolvedValue(next);
    const del = vi.spyOn(catalogApi, "adminDeleteTag").mockResolvedValue(next);

    const store = useCatalogStore();
    await store.reactivate("category", 11);
    await store.deactivate("event", 100);
    await store.remove("tag", 201);

    expect(reactivate).toHaveBeenCalledWith(11);
    expect(deactivate).toHaveBeenCalledWith(100);
    expect(del).toHaveBeenCalledWith(201);
    expect(store.catalogVersion).toBe(5);
  });

  it("rejects unknown kinds", async () => {
    const store = useCatalogStore();
    await expect(store.add("nope", {})).rejects.toThrow(/Unknown kind/);
  });
});

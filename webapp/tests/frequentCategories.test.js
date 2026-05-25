import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useFrequentCategoriesStore } from "../src/stores/frequentCategories.js";
import { useCatalogStore } from "../src/stores/catalog.js";

beforeEach(async () => {
  await allure.epic("Catalog");
  await allure.feature("Frontend");
  await allure.story("frequentCategories");
});

const MS_24H = 24 * 60 * 60 * 1000;

const CATALOG = {
  catalog_version: 1,
  category_groups: [{ id: 1, name: "Food", is_active: true }],
  categories: [{ id: 10, group_id: 1, name: "groceries", is_active: true }],
  events: [],
  tags: [],
  frequent_categories: [{ id: 10, name: "groceries" }],
};

beforeEach(() => {
  localStorage.clear();
  setActivePinia(createPinia());
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("frequentCategories store — ensureLoaded", () => {
  it("populates from catalog when empty", () => {
    useCatalogStore().replaceSnapshot(CATALOG);
    const store = useFrequentCategoriesStore();
    expect(store.categories).toHaveLength(0);
    store.ensureLoaded();
    expect(store.categories).toHaveLength(1);
    expect(store.categories[0].name).toBe("groceries");
  });

  it("is a no-op when data is fresh (< 24h)", () => {
    useCatalogStore().replaceSnapshot(CATALOG);
    const store = useFrequentCategoriesStore();
    store.ensureLoaded(); // populate
    store.categories = [{ id: 99, name: "manual" }];
    store.lastFetched = Date.now(); // mark as fresh
    store.ensureLoaded(); // should not re-read
    expect(store.categories[0].name).toBe("manual");
  });

  it("re-reads from catalog when stale (> 24h)", () => {
    useCatalogStore().replaceSnapshot(CATALOG);
    const store = useFrequentCategoriesStore();
    store.categories = [{ id: 99, name: "old" }];
    store.lastFetched = Date.now() - MS_24H - 1;
    store.ensureLoaded();
    expect(store.categories[0].name).toBe("groceries");
  });
});

describe("frequentCategories store — refresh", () => {
  it("overwrites categories and bumps lastFetched", () => {
    const store = useFrequentCategoriesStore();
    const before = Date.now();
    store.refresh({ frequent_categories: [{ id: 5, name: "pizza" }] });
    expect(store.categories).toHaveLength(1);
    expect(store.categories[0].name).toBe("pizza");
    expect(store.lastFetched).toBeGreaterThanOrEqual(before);
  });

  it("is a no-op when responseData has no frequent_categories", () => {
    const store = useFrequentCategoriesStore();
    store.categories = [{ id: 1, name: "existing" }];
    store.refresh({});
    expect(store.categories[0].name).toBe("existing");
  });
});

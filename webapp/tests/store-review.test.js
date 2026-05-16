import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useReviewStore } from "../src/stores/review.js";
import * as expenseCorrections from "../src/api/expenseCorrections.js";
import * as reviewApi from "../src/api/review.js";
import { useCatalogStore } from "../src/stores/catalog.js";

beforeEach(() => {
  setActivePinia(createPinia());
});

afterEach(() => {
  vi.restoreAllMocks();
});

function seedCatalog() {
  const catalog = useCatalogStore();
  catalog.replaceSnapshot({
    catalog_version: 1,
    category_groups: [{ id: 1, name: "food", is_active: true }],
    categories: [{ id: 2, group_id: 1, name: "snacks", is_active: true }],
    events: [],
    tags: [],
  });
}

describe("review store: correct()", () => {
  it("uses item.expense_id (not item.id) for doubtful items", async () => {
    const spy = vi
      .spyOn(expenseCorrections, "correctCategory")
      .mockResolvedValueOnce({ corrected_expense_id: 42, batch_updated_count: 0, count: 1 });

    seedCatalog();
    const store = useReviewStore();
    store.items = [{ id: 7, expense_id: 42, is_doubtful: true, name: "hleb", count: 1 }];

    await store.correct(store.items[0], 2);

    expect(spy).toHaveBeenCalledWith(42, 2);
  });

  it("filters the item by item.id after a successful correction", async () => {
    vi.spyOn(expenseCorrections, "correctCategory").mockResolvedValueOnce({
      corrected_expense_id: 42,
      batch_updated_count: 0,
      count: 1,
    });

    seedCatalog();
    const store = useReviewStore();
    store.items = [
      { id: 7, expense_id: 42, is_doubtful: true, name: "hleb", count: 1 },
      { id: 8, expense_id: 99, is_doubtful: true, name: "mleko", count: 1 },
    ];

    await store.correct(store.items[0], 2);

    expect(store.items).toHaveLength(1);
    expect(store.items[0].id).toBe(8);
  });

  it("uses item.id directly for certain items (expense_id equals id)", async () => {
    const spy = vi
      .spyOn(expenseCorrections, "correctCategory")
      .mockResolvedValueOnce({ corrected_expense_id: 100, batch_updated_count: 0, count: 1 });

    seedCatalog();
    const store = useReviewStore();
    // For certain items expense_id is undefined, so item.expense_id ?? item.id == item.id
    store.items = [{ id: 100, is_doubtful: false, store: "Lidl", total: 200 }];

    await store.correct(store.items[0], 2);

    expect(spy).toHaveBeenCalledWith(100, 2);
  });

  it("keeps certain items in the list after correction and updates their category", async () => {
    vi.spyOn(expenseCorrections, "correctCategory").mockResolvedValueOnce({
      corrected_expense_id: 100,
      batch_updated_count: 0,
    });

    seedCatalog();
    const store = useReviewStore();
    store.items = [{ id: 100, is_doubtful: false, store: "Lidl", total: 200, category_id: 1 }];

    await store.correct(store.items[0], 2);

    expect(store.items).toHaveLength(1);
    expect(store.items[0].category_id).toBe(2);
    expect(store.items[0].category_name).toBe("snacks");
  });

  it("removes doubtful items from the list after correction", async () => {
    vi.spyOn(expenseCorrections, "correctCategory").mockResolvedValueOnce({
      corrected_expense_id: 42,
      batch_updated_count: 0,
    });

    seedCatalog();
    const store = useReviewStore();
    store.items = [{ id: 7, expense_id: 42, is_doubtful: true, name: "hleb", count: 1 }];
    store.doubtfulCount = 1;

    await store.correct(store.items[0], 2);

    expect(store.items).toHaveLength(0);
    expect(store.doubtfulCount).toBe(0);
  });

  it("does not decrement doubtfulCount when correcting a certain item", async () => {
    vi.spyOn(expenseCorrections, "correctCategory").mockResolvedValueOnce({
      corrected_expense_id: 100,
      batch_updated_count: 0,
    });

    seedCatalog();
    const store = useReviewStore();
    store.items = [{ id: 100, is_doubtful: false, store: "Lidl", total: 200 }];
    store.doubtfulCount = 3;

    await store.correct(store.items[0], 2);

    expect(store.doubtfulCount).toBe(3);
  });
});

describe("review store: loadNextPage()", () => {
  it("appends items and tracks hasMore", async () => {
    vi.spyOn(reviewApi, "getReviewFeed").mockResolvedValueOnce({
      items: [{ id: 1, is_doubtful: false }],
      doubtful_count: 0,
      has_more: false,
    });

    const store = useReviewStore();
    await store.loadNextPage();

    expect(store.items).toHaveLength(1);
    expect(store.hasMore).toBe(false);
  });
});

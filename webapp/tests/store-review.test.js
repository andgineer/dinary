import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useReviewStore } from "../src/stores/review.js";
import * as expenseCorrections from "../src/api/expenseCorrections.js";
import * as reviewApi from "../src/api/review.js";
import { useCatalogStore } from "../src/stores/catalog.js";

function mockOnLine(value) {
  const ownBefore = Object.getOwnPropertyDescriptor(navigator, "onLine");
  Object.defineProperty(navigator, "onLine", { configurable: true, get: () => value });
  return () => {
    if (ownBefore) {
      Object.defineProperty(navigator, "onLine", ownBefore);
    } else {
      delete navigator.onLine;
    }
  };
}

beforeEach(() => {
  localStorage.clear();
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

    expect(spy).toHaveBeenCalledWith(42, 2, "all");
  });

  it("converts corrected doubtful item to certain and places it after remaining doubtful items", async () => {
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

    expect(store.items).toHaveLength(2);
    expect(store.items[0].id).toBe(8);
    expect(store.items[0].is_doubtful).toBe(true);
    expect(store.items[1].id).toBe(7);
    expect(store.items[1].is_doubtful).toBe(false);
    expect(store.items[1].category_id).toBe(2);
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

    expect(spy).toHaveBeenCalledWith(100, 2, "all");
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

  it("moves corrected doubtful item into certain section and decrements doubtfulCount", async () => {
    vi.spyOn(expenseCorrections, "correctCategory").mockResolvedValueOnce({
      corrected_expense_id: 42,
      batch_updated_count: 0,
    });

    seedCatalog();
    const store = useReviewStore();
    store.items = [{ id: 7, expense_id: 42, is_doubtful: true, name: "hleb", count: 1 }];
    store.doubtfulCount = 1;

    await store.correct(store.items[0], 2);

    expect(store.items).toHaveLength(1);
    expect(store.items[0].id).toBe(7);
    expect(store.items[0].is_doubtful).toBe(false);
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

  it("forwards chosen scope to API for certain items", async () => {
    const spy = vi
      .spyOn(expenseCorrections, "correctCategory")
      .mockResolvedValueOnce({ corrected_expense_id: 100, batch_updated_count: 0 });

    seedCatalog();
    const store = useReviewStore();
    store.items = [{ id: 100, expense_id: 100, is_doubtful: false, store: "Lidl", total: 200 }];

    await store.correct(store.items[0], 2, "month");

    expect(spy).toHaveBeenCalledWith(100, 2, "month");
  });
});

describe("review store: loadNextPage() offline", () => {
  it("suppresses error toast when offline and API fails", async () => {
    vi.spyOn(reviewApi, "getReviewFeed").mockRejectedValueOnce(new Error("Network error"));
    const restore = mockOnLine(false);
    try {
      const store = useReviewStore();
      const { useToastStore } = await import("../src/stores/toast.js");
      const toast = useToastStore();
      const showSpy = vi.spyOn(toast, "show");
      await store.loadNextPage();
      expect(showSpy).not.toHaveBeenCalled();
    } finally {
      restore();
    }
  });

  it("shows error toast when online and API fails", async () => {
    vi.spyOn(reviewApi, "getReviewFeed").mockRejectedValueOnce(new Error("Server error"));
    const store = useReviewStore();
    const { useToastStore } = await import("../src/stores/toast.js");
    const toast = useToastStore();
    const showSpy = vi.spyOn(toast, "show");
    await store.loadNextPage();
    expect(showSpy).toHaveBeenCalledWith(expect.stringContaining("Server error"), "error");
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

  it("deduplicates incoming items by id to prevent double-display after local correction", async () => {
    vi.spyOn(reviewApi, "getReviewFeed").mockResolvedValueOnce({
      items: [{ id: 42, is_doubtful: false, name: "hleb" }],
      doubtful_count: 0,
      has_more: false,
    });

    const store = useReviewStore();
    // Simulate a locally-converted certain item already in the list (same id=42)
    store.items = [{ id: 42, is_doubtful: false, name: "hleb" }];

    await store.loadNextPage();

    expect(store.items).toHaveLength(1);
  });
});

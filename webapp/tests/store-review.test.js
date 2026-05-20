import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useReviewStore } from "../src/stores/review.js";
import * as expenseCorrections from "../src/api/expenseCorrections.js";
import * as reviewApi from "../src/api/review.js";
import { useCatalogStore } from "../src/stores/catalog.js";
import { useToastStore } from "../src/stores/toast.js";

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

  it("clears confidence_level and updates category on matching expenses when correcting a doubtful rule", async () => {
    vi.spyOn(expenseCorrections, "correctCategory").mockResolvedValueOnce({
      corrected_expense_id: 42,
      batch_updated_count: 2,
      count: 3,
    });
    seedCatalog();
    const store = useReviewStore();
    store.items = [{ id: 7, expense_id: 42, is_doubtful: true, name: "hleb", count: 3 }];
    store.expenses = [
      { id: 42, item_name: "hleb", confidence_level: 2, category_id: 1, category_name: "old" },
      { id: 43, item_name: "hleb", confidence_level: 2, category_id: 1, category_name: "old" },
      { id: 44, item_name: "mleko", confidence_level: 2, category_id: 1, category_name: "old" },
    ];

    await store.correct(store.items[0], 2);

    expect(store.expenses[0].confidence_level).toBeNull();
    expect(store.expenses[0].category_id).toBe(2);
    expect(store.expenses[0].category_name).toBe("snacks");
    expect(store.expenses[1].confidence_level).toBeNull();
    expect(store.expenses[1].category_id).toBe(2);
    expect(store.expenses[2].confidence_level).toBe(2);
    expect(store.expenses[2].category_id).toBe(1);
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

describe("review store: markDirty()", () => {
  it("sets dirtyFlag to true and persists to localStorage", () => {
    const store = useReviewStore();
    expect(store.dirtyFlag).toBe(false);
    store.markDirty();
    expect(store.dirtyFlag).toBe(true);
    expect(localStorage.getItem("dinary:review:dirty")).toBe("1");
  });
});

describe("review store: loadIfNeeded()", () => {
  it("fetches (reset + page 1) when dirtyFlag is set", async () => {
    vi.spyOn(reviewApi, "getReviewFeed").mockResolvedValue({
      items: [{ id: 1, is_doubtful: false }],
      doubtful_count: 0,
      has_more: false,
      pending_receipts: 0,
    });
    const store = useReviewStore();
    store.markDirty();
    await store.loadIfNeeded();
    expect(reviewApi.getReviewFeed).toHaveBeenCalledTimes(1);
    expect(store.items).toHaveLength(1);
  });

  it("fetches when no lastFetchedAt (never loaded)", async () => {
    vi.spyOn(reviewApi, "getReviewFeed").mockResolvedValue({
      items: [],
      doubtful_count: 0,
      has_more: false,
      pending_receipts: 0,
    });
    const store = useReviewStore();
    expect(store.lastFetchedAt).toBeNull();
    await store.loadIfNeeded();
    expect(reviewApi.getReviewFeed).toHaveBeenCalledTimes(1);
  });

  it("fetches when data is older than 24h", async () => {
    const old = Date.now() - 25 * 60 * 60 * 1000;
    localStorage.setItem("dinary:review:fetchedAt", String(old));
    vi.spyOn(reviewApi, "getReviewFeed").mockResolvedValue({
      items: [],
      doubtful_count: 0,
      has_more: false,
      pending_receipts: 0,
    });
    setActivePinia(createPinia());
    const store = useReviewStore();
    await store.loadIfNeeded();
    expect(reviewApi.getReviewFeed).toHaveBeenCalledTimes(1);
  });

  it("skips fetch when clean and data is recent", async () => {
    localStorage.setItem("dinary:review:fetchedAt", String(Date.now() - 60_000));
    vi.spyOn(reviewApi, "getReviewFeed").mockResolvedValue({
      items: [],
      doubtful_count: 0,
      has_more: false,
      pending_receipts: 0,
    });
    setActivePinia(createPinia());
    const store = useReviewStore();
    expect(store.dirtyFlag).toBe(false);
    await store.loadIfNeeded();
    expect(reviewApi.getReviewFeed).not.toHaveBeenCalled();
  });
});

describe("review store: pending_receipts clears dirty flag", () => {
  it("clears dirtyFlag when loadNextPage returns pending_receipts=0", async () => {
    vi.spyOn(reviewApi, "getReviewFeed").mockResolvedValue({
      items: [],
      doubtful_count: 0,
      has_more: false,
      pending_receipts: 0,
    });
    const store = useReviewStore();
    store.markDirty();
    await store.loadNextPage();
    expect(store.dirtyFlag).toBe(false);
    expect(localStorage.getItem("dinary:review:dirty")).toBeNull();
  });

  it("keeps dirtyFlag when loadNextPage returns pending_receipts > 0", async () => {
    vi.spyOn(reviewApi, "getReviewFeed").mockResolvedValue({
      items: [],
      doubtful_count: 0,
      has_more: false,
      pending_receipts: 2,
    });
    const store = useReviewStore();
    store.markDirty();
    await store.loadNextPage();
    expect(store.dirtyFlag).toBe(true);
  });

  it("sets lastFetchedAt after successful loadNextPage", async () => {
    vi.spyOn(reviewApi, "getReviewFeed").mockResolvedValue({
      items: [],
      doubtful_count: 0,
      has_more: false,
      pending_receipts: 0,
    });
    const store = useReviewStore();
    const before = Date.now();
    await store.loadNextPage();
    expect(store.lastFetchedAt).toBeGreaterThanOrEqual(before);
  });

  it("reset() clears lastFetchedAt from localStorage", () => {
    localStorage.setItem("dinary:review:fetchedAt", String(Date.now()));
    const store = useReviewStore();
    store.reset();
    expect(store.lastFetchedAt).toBeNull();
    expect(localStorage.getItem("dinary:review:fetchedAt")).toBeNull();
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

describe("review store: updateExpense()", () => {
  it("calls editExpense API with the given payload", async () => {
    const spy = vi.spyOn(expenseCorrections, "editExpense").mockResolvedValueOnce({
      id: 10,
      category_id: 20,
      category_name: "cafe",
      tag_ids: [5],
      event_id: 3,
      event_name: "Trip",
    });

    const store = useReviewStore();
    await store.updateExpense(10, { category_id: 20, tag_ids: [5], event_id: 3 });

    expect(spy).toHaveBeenCalledWith(10, { category_id: 20, tag_ids: [5], event_id: 3 });
  });

  it("shows error toast and rethrows when API fails", async () => {
    vi.spyOn(expenseCorrections, "editExpense").mockRejectedValueOnce(new Error("Server error"));
    const store = useReviewStore();
    const toast = useToastStore();
    const showSpy = vi.spyOn(toast, "show");
    await expect(store.updateExpense(10, {})).rejects.toThrow("Server error");
    expect(showSpy).toHaveBeenCalledWith(expect.stringContaining("Server error"), "error");
  });

  it("removes matching doubtful rule from items when update_rule is true", async () => {
    vi.spyOn(expenseCorrections, "editExpense").mockResolvedValueOnce({});
    const store = useReviewStore();
    store.items = [
      { id: 7, expense_id: 42, is_doubtful: true, name: "hleb" },
      { id: 8, expense_id: 99, is_doubtful: true, name: "mleko" },
    ];
    store.doubtfulCount = 2;

    await store.updateExpense(42, { update_rule: true });

    expect(store.items.map((i) => i.id)).toEqual([8]);
    expect(store.doubtfulCount).toBe(1);
  });

  it("clears confidence_level on sibling expenses with same item_name when update_rule is true", async () => {
    vi.spyOn(expenseCorrections, "editExpense").mockResolvedValueOnce({});
    const store = useReviewStore();
    store.items = [{ id: 7, expense_id: 42, is_doubtful: true, name: "hleb" }];
    store.expenses = [
      { id: 42, item_name: "hleb", confidence_level: 2, category_id: 1, category_name: "old" },
      { id: 43, item_name: "hleb", confidence_level: 2, category_id: 1, category_name: "old" },
      { id: 44, item_name: "mleko", confidence_level: 2, category_id: 1, category_name: "old" },
    ];
    store.doubtfulCount = 1;

    await store.updateExpense(42, { update_rule: true, scope: "single", category_id: 2 });

    expect(store.expenses[0].confidence_level).toBeNull();
    expect(store.expenses[1].confidence_level).toBeNull();
    expect(store.expenses[2].confidence_level).toBe(2);
    // scope=single: category not updated on siblings
    expect(store.expenses[1].category_id).toBe(1);
    expect(store.expenses[1].category_name).toBe("old");
  });

  it("updates category on siblings when update_rule is true and scope is broader than single", async () => {
    vi.spyOn(expenseCorrections, "editExpense").mockResolvedValueOnce({});
    seedCatalog();
    const store = useReviewStore();
    store.items = [{ id: 7, expense_id: 42, is_doubtful: true, name: "hleb" }];
    store.expenses = [
      { id: 42, item_name: "hleb", confidence_level: 2, category_id: 1, category_name: "old" },
      { id: 43, item_name: "hleb", confidence_level: 2, category_id: 1, category_name: "old" },
      { id: 44, item_name: "mleko", confidence_level: 2, category_id: 1, category_name: "old" },
    ];
    store.doubtfulCount = 1;

    await store.updateExpense(42, { update_rule: true, scope: "month", category_id: 2 });

    expect(store.expenses[0].confidence_level).toBeNull();
    expect(store.expenses[0].category_id).toBe(2);
    expect(store.expenses[0].category_name).toBe("snacks");
    expect(store.expenses[1].confidence_level).toBeNull();
    expect(store.expenses[1].category_id).toBe(2);
    expect(store.expenses[1].category_name).toBe("snacks");
    // unrelated item_name: untouched
    expect(store.expenses[2].confidence_level).toBe(2);
    expect(store.expenses[2].category_id).toBe(1);
  });

  it("does not modify items when update_rule is false", async () => {
    vi.spyOn(expenseCorrections, "editExpense").mockResolvedValueOnce({});
    const store = useReviewStore();
    store.items = [{ id: 7, expense_id: 42, is_doubtful: true, name: "hleb" }];
    store.doubtfulCount = 1;

    await store.updateExpense(42, { update_rule: false });

    expect(store.items).toHaveLength(1);
    expect(store.doubtfulCount).toBe(1);
  });

  it("does not remove certain items (is_doubtful: false) even when update_rule is true", async () => {
    vi.spyOn(expenseCorrections, "editExpense").mockResolvedValueOnce({});
    const store = useReviewStore();
    store.items = [{ id: 7, expense_id: 42, is_doubtful: false, name: "hleb" }];
    store.doubtfulCount = 0;

    await store.updateExpense(42, { update_rule: true });

    expect(store.items).toHaveLength(1);
    expect(store.doubtfulCount).toBe(0);
  });
});

describe("review store: patchExpense()", () => {
  it("merges patch fields onto the matching expense", () => {
    const store = useReviewStore();
    store.expenses = [
      { id: 10, category_id: 1, category_name: "old", tags: [], event_id: null },
      { id: 20, category_id: 2, category_name: "other", tags: [], event_id: null },
    ];

    store.patchExpense(10, {
      category_id: 5,
      category_name: "new",
      tags: [{ id: 3, name: "собака" }],
      event_id: null,
      event_name: null,
    });

    expect(store.expenses[0].category_id).toBe(5);
    expect(store.expenses[0].category_name).toBe("new");
    expect(store.expenses[0].tags).toEqual([{ id: 3, name: "собака" }]);
  });

  it("does not touch other expenses", () => {
    const store = useReviewStore();
    store.expenses = [
      { id: 10, category_name: "a", tags: [] },
      { id: 20, category_name: "b", tags: [] },
    ];

    store.patchExpense(10, { category_name: "updated" });

    expect(store.expenses[1].category_name).toBe("b");
  });

  it("is a no-op when the id is not found", () => {
    const store = useReviewStore();
    store.expenses = [{ id: 10, category_name: "a", tags: [] }];

    store.patchExpense(99, { category_name: "updated" });

    expect(store.expenses[0].category_name).toBe("a");
  });

  it("replaces the array reference so v-for re-renders", () => {
    const store = useReviewStore();
    const before = store.expenses;
    store.expenses = [{ id: 10, category_name: "a", tags: [] }];
    const afterSet = store.expenses;

    store.patchExpense(10, { category_name: "b" });

    expect(store.expenses).not.toBe(before);
    expect(store.expenses).not.toBe(afterSet);
  });
});

describe("review store: confirmAll()", () => {
  it("removes confirmed items from items list", async () => {
    vi.spyOn(reviewApi, "confirmAllRules").mockResolvedValueOnce({ confirmed: 2 });
    const store = useReviewStore();
    store.items = [
      { id: 1, is_doubtful: true },
      { id: 2, is_doubtful: true },
      { id: 3, is_doubtful: false },
    ];
    store.doubtfulCount = 2;
    await store.confirmAll([1, 2]);
    expect(store.items.map((i) => i.id)).toEqual([3]);
  });

  it("decrements doubtfulCount by confirmed count", async () => {
    vi.spyOn(reviewApi, "confirmAllRules").mockResolvedValueOnce({ confirmed: 2 });
    const store = useReviewStore();
    store.items = [
      { id: 1, is_doubtful: true },
      { id: 2, is_doubtful: true },
    ];
    store.doubtfulCount = 3;
    await store.confirmAll([1, 2]);
    expect(store.doubtfulCount).toBe(1);
  });

  it("shows success toast with count", async () => {
    vi.spyOn(reviewApi, "confirmAllRules").mockResolvedValueOnce({ confirmed: 3 });
    const store = useReviewStore();
    store.items = [{ id: 1, is_doubtful: true }, { id: 2, is_doubtful: true }, { id: 3, is_doubtful: true }];
    store.doubtfulCount = 3;
    const toast = useToastStore();
    const showSpy = vi.spyOn(toast, "show");
    await store.confirmAll([1, 2, 3]);
    expect(showSpy).toHaveBeenCalledWith(expect.stringContaining("3"), "success");
  });

  it("shows error toast when API fails", async () => {
    vi.spyOn(reviewApi, "confirmAllRules").mockRejectedValueOnce(new Error("Server error"));
    const store = useReviewStore();
    const toast = useToastStore();
    const showSpy = vi.spyOn(toast, "show");
    await store.confirmAll([1]);
    expect(showSpy).toHaveBeenCalledWith(expect.stringContaining("Server error"), "error");
  });

  it("reloads expenses after confirming so updated confidence_level is reflected", async () => {
    vi.spyOn(reviewApi, "confirmAllRules").mockResolvedValueOnce({ confirmed: 1 });
    vi.spyOn(reviewApi, "getExpensesFeed").mockResolvedValueOnce({
      items: [{ id: 10, confidence_level: 4, category_name: "food" }],
      has_more: false,
    });
    const store = useReviewStore();
    store.items = [{ id: 1, expense_id: 10, is_doubtful: true }];
    store.expenses = [{ id: 10, confidence_level: 2, category_name: "food" }];
    store.expensesPage = 1;

    await store.confirmAll([1]);

    expect(reviewApi.getExpensesFeed).toHaveBeenCalled();
    expect(store.expenses[0].confidence_level).toBe(4);
  });
});

describe("review store: setOpenRow()", () => {
  it("sets openRowId to the given id", () => {
    const store = useReviewStore();
    expect(store.openRowId).toBeNull();
    store.setOpenRow(42);
    expect(store.openRowId).toBe(42);
  });

  it("updates openRowId when called again with a different id", () => {
    const store = useReviewStore();
    store.setOpenRow(1);
    store.setOpenRow(2);
    expect(store.openRowId).toBe(2);
  });
});

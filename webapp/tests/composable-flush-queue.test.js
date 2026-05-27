import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { flushQueue, _resetForTest as resetFlush } from "../src/composables/flushQueue.js";
import { useQueueStore, _resetForTest as resetQueueStore } from "../src/stores/queue.js";
import { useCatalogStore } from "../src/stores/catalog.js";
import * as expensesApi from "../src/api/expenses.js";
import * as catalogApi from "../src/api/catalog.js";

beforeEach(async () => {
  await allure.epic("Expenses");
  await allure.feature("Frontend");
  await allure.story("flushQueue");
});

async function resetQueueDb() {
  await resetQueueStore();
  await new Promise((resolve) => {
    const del = indexedDB.deleteDatabase("dinary-v2");
    del.onsuccess = del.onerror = del.onblocked = () => resolve();
    setTimeout(resolve, 1000);
  });
}

beforeEach(async () => {
  localStorage.clear();
  setActivePinia(createPinia());
  resetFlush();
  await resetQueueDb();
  vi.spyOn(catalogApi, "fetchCatalog").mockResolvedValue({
    catalog_version: -1,
    category_groups: [],
    categories: [],
    events: [],
    tags: [],
  });
});

afterEach(async () => {
  vi.restoreAllMocks();
  await resetQueueDb();
});

describe("flushQueue", () => {
  it("sends every queued item via postExpense and removes them from the queue", async () => {
    const queue = useQueueStore();
    await queue.enqueue({ amount: 1, currency: "RSD", category_id: 10, date: "2026-05-04" });
    await queue.enqueue({ amount: 2, currency: "RSD", category_id: 10, date: "2026-05-04" });

    const post = vi.spyOn(expensesApi, "postExpense").mockResolvedValue({ catalog_version: 5 });

    await flushQueue();

    expect(post).toHaveBeenCalledTimes(2);
    expect(queue.items).toHaveLength(0);
  });

  it("drops legacy items that have no category_id", async () => {
    const queue = useQueueStore();
    await queue.enqueue({ amount: 1, currency: "RSD", date: "2026-05-04" });
    const post = vi.spyOn(expensesApi, "postExpense");
    await flushQueue();
    expect(post).not.toHaveBeenCalled();
    expect(queue.items).toHaveLength(0);
  });

  it("removes the item on 409 (server already recorded)", async () => {
    const queue = useQueueStore();
    await queue.enqueue({ amount: 1, currency: "RSD", category_id: 10, date: "2026-05-04" });

    vi.spyOn(expensesApi, "postExpense").mockRejectedValue(
      Object.assign(new Error("conflict"), { status: 409 }),
    );

    await flushQueue();
    expect(queue.items).toHaveLength(0);
  });

  it("stops the sweep on 401 and keeps remaining items", async () => {
    const queue = useQueueStore();
    await queue.enqueue({ amount: 1, currency: "RSD", category_id: 10, date: "2026-05-04" });
    await queue.enqueue({ amount: 2, currency: "RSD", category_id: 10, date: "2026-05-04" });

    vi.spyOn(expensesApi, "postExpense").mockRejectedValueOnce(
      Object.assign(new Error("auth"), { status: 401 }),
    );
    await flushQueue();
    expect(queue.items.length).toBeGreaterThanOrEqual(2);
  });

  it("stops the sweep on a transient error and records lastFlushError", async () => {
    const queue = useQueueStore();
    await queue.enqueue({ amount: 1, currency: "RSD", category_id: 10, date: "2026-05-04" });
    await queue.enqueue({ amount: 2, currency: "RSD", category_id: 10, date: "2026-05-04" });

    const err = new Error("network down");
    vi.spyOn(expensesApi, "postExpense").mockRejectedValue(err);

    await flushQueue();
    expect(queue.items).toHaveLength(2);
    expect(queue.lastFlushError?.message).toBe("network down");
  });

  it("re-fetches the catalog when the server returns a newer catalog_version", async () => {
    const queue = useQueueStore();
    const catalog = useCatalogStore();
    catalog.replaceSnapshot({
      catalog_version: 1,
      category_groups: [],
      categories: [],
      events: [],
      tags: [],
    });
    await queue.enqueue({ amount: 1, currency: "RSD", category_id: 10, date: "2026-05-04" });

    vi.spyOn(expensesApi, "postExpense").mockResolvedValue({ catalog_version: 5 });
    const fetchCatalog = vi
      .spyOn(catalogApi, "fetchCatalog")
      .mockResolvedValue({
        catalog_version: 5,
        category_groups: [],
        categories: [],
        events: [],
        tags: [],
      });

    await flushQueue();
    expect(fetchCatalog).toHaveBeenCalled();
    expect(catalog.catalogVersion).toBe(5);
  });

  it("is reentrant-safe: a second flushQueue while one is in flight is a no-op", async () => {
    const queue = useQueueStore();
    await queue.enqueue({ amount: 1, currency: "RSD", category_id: 10, date: "2026-05-04" });

    const post = vi.spyOn(expensesApi, "postExpense").mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve({}), 50)),
    );

    const a = flushQueue();
    const b = flushQueue();
    await Promise.all([a, b]);
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("does not call markDirty (only receipt sends invalidate llm/review)", async () => {
    const queue = useQueueStore();
    await queue.enqueue({ amount: 1, currency: "RSD", category_id: 10, date: "2026-05-04" });

    vi.spyOn(expensesApi, "postExpense").mockResolvedValue({ catalog_version: 1 });
    const { useLlmStore } = await import("../src/stores/llm.js");
    const { useReviewStore } = await import("../src/stores/review.js");
    const llmSpy = vi.spyOn(useLlmStore(), "markDirty");
    const reviewSpy = vi.spyOn(useReviewStore(), "markDirty");

    await flushQueue();

    expect(llmSpy).not.toHaveBeenCalled();
    expect(reviewSpy).not.toHaveBeenCalled();
  });
});

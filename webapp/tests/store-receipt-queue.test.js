import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import {
  useReceiptQueueStore,
  _resetForTest,
} from "../src/stores/receiptQueue.js";

beforeEach(async () => {
  await allure.epic("Receipts");
  await allure.feature("Frontend");
  await allure.story("Receipt queue store");
});

async function resetDb() {
  await _resetForTest();
  await new Promise((resolve) => {
    const del = indexedDB.deleteDatabase("dinary-receipts");
    del.onsuccess = del.onerror = del.onblocked = () => resolve();
    setTimeout(resolve, 200);
  });
}

beforeEach(async () => {
  setActivePinia(createPinia());
  await resetDb();
});

afterEach(async () => {
  vi.restoreAllMocks();
  await resetDb();
});

describe("receipt queue store: enqueue / list / remove", () => {
  it("enqueue persists item with a generated client_receipt_id and exposes it via items", async () => {
    const store = useReceiptQueueStore();
    const result = await store.enqueue("https://suf.purs.gov.rs/v/?vl=AAAA");
    expect(result).toBe("queued");
    expect(store.items).toHaveLength(1);
    expect(store.items[0].url).toBe("https://suf.purs.gov.rs/v/?vl=AAAA");
    expect(store.items[0].client_receipt_id).toBeTypeOf("string");
    expect(store.items[0].client_receipt_id.length).toBeGreaterThan(0);
    expect(store.items[0].queued_at).toBeTypeOf("number");
  });

  it("multiple enqueues accumulate; remove deletes by id", async () => {
    const store = useReceiptQueueStore();
    await store.enqueue("https://example.com/r1");
    await store.enqueue("https://example.com/r2");
    await store.enqueue("https://example.com/r3");
    expect(await store.count()).toBe(3);

    const target = store.items[1];
    await store.remove(target.id);
    expect(store.items).toHaveLength(2);
    expect(store.items.map((i) => i.url)).not.toContain("https://example.com/r2");
  });

  it("different URLs produce different client_receipt_ids", async () => {
    const store = useReceiptQueueStore();
    await store.enqueue("https://example.com/r1");
    await store.enqueue("https://example.com/r2");
    const ids = store.items.map((i) => i.client_receipt_id);
    expect(new Set(ids).size).toBe(2);
  });

  it("same URL produces the same client_receipt_id (stable hash)", async () => {
    const store = useReceiptQueueStore();
    await store.enqueue("https://suf.purs.gov.rs/v/?vl=AAAA");
    const id1 = store.items[0].client_receipt_id;
    // remove and re-enqueue to get a fresh entry for comparison
    await store.remove(store.items[0].id);
    await store.enqueue("https://suf.purs.gov.rs/v/?vl=AAAA");
    expect(store.items[0].client_receipt_id).toBe(id1);
  });

  it("enqueuing the same URL twice only adds one entry and second call returns false", async () => {
    const store = useReceiptQueueStore();
    const first = await store.enqueue("https://suf.purs.gov.rs/v/?vl=DUPLICATE");
    const second = await store.enqueue("https://suf.purs.gov.rs/v/?vl=DUPLICATE");
    expect(first).toBe("queued");
    expect(second).toBe("in-queue");
    expect(store.items).toHaveLength(1);
  });

  it("refresh re-reads items from IndexedDB", async () => {
    const store = useReceiptQueueStore();
    await store.enqueue("https://example.com/r");
    store.items = [];
    await store.refresh();
    expect(store.items).toHaveLength(1);
  });

  it("count returns the number of pending items", async () => {
    const store = useReceiptQueueStore();
    expect(await store.count()).toBe(0);
    await store.enqueue("https://example.com/r1");
    await store.enqueue("https://example.com/r2");
    expect(await store.count()).toBe(2);
  });
});

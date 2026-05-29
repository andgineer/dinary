import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import {
  useReceiptQueueStore,
  _resetForTest,
} from "../src/stores/receiptQueue.js";
import {
  flushReceiptQueue,
  _resetForTest as resetFlush,
} from "../src/composables/flushReceiptQueue.js";
import * as receiptsApi from "../src/api/receipts.js";

beforeEach(async () => {
  await allure.epic("Receipts");
  await allure.feature("Frontend");
  await allure.story("Receipt queue durability");
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
  resetFlush();
  await resetDb();
});

afterEach(async () => {
  vi.restoreAllMocks();
  await resetDb();
});

describe("receipt queue durability", () => {
  it("item survives store re-instantiation (page reload simulation)", async () => {
    const store = useReceiptQueueStore();
    await store.enqueue("https://suf.purs.gov.rs/v/?vl=DURABILITY1");
    const originalId = store.items[0].client_receipt_id;

    await _resetForTest();

    setActivePinia(createPinia());
    const freshStore = useReceiptQueueStore();
    await freshStore.refresh();

    expect(freshStore.items).toHaveLength(1);
    expect(freshStore.items[0].url).toBe("https://suf.purs.gov.rs/v/?vl=DURABILITY1");
    expect(freshStore.items[0].client_receipt_id).toBe(originalId);
  });

  it("offline enqueue survives store re-instantiation", async () => {
    const onlineDesc = Object.getOwnPropertyDescriptor(
      Object.getPrototypeOf(navigator),
      "onLine",
    );
    Object.defineProperty(navigator, "onLine", { configurable: true, get: () => false });
    try {
      const store = useReceiptQueueStore();
      await store.enqueue("https://suf.purs.gov.rs/v/?vl=OFFLINE1");
      expect(store.items).toHaveLength(1);

      await _resetForTest();
      setActivePinia(createPinia());
      const freshStore = useReceiptQueueStore();
      await freshStore.refresh();

      expect(freshStore.items).toHaveLength(1);
      expect(freshStore.items[0].url).toBe("https://suf.purs.gov.rs/v/?vl=OFFLINE1");
    } finally {
      if (onlineDesc) {
        Object.defineProperty(Object.getPrototypeOf(navigator), "onLine", onlineDesc);
      } else {
        Object.defineProperty(navigator, "onLine", { configurable: true, get: () => true });
      }
    }
  });

  it("item not lost when flush errors", async () => {
    const store = useReceiptQueueStore();
    await store.enqueue("https://suf.purs.gov.rs/v/?vl=FLUSHFAIL1");

    vi.spyOn(receiptsApi, "postReceipt").mockRejectedValue(new Error("network down"));
    await flushReceiptQueue();

    await _resetForTest();
    setActivePinia(createPinia());
    const freshStore = useReceiptQueueStore();
    await freshStore.refresh();

    expect(freshStore.items).toHaveLength(1);
    expect(freshStore.items[0].url).toBe("https://suf.purs.gov.rs/v/?vl=FLUSHFAIL1");
  });
});

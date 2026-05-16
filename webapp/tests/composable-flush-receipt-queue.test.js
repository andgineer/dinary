import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import {
  flushReceiptQueue,
  _resetForTest as resetFlush,
} from "../src/composables/flushReceiptQueue.js";
import {
  useReceiptQueueStore,
  _resetForTest as resetReceiptQueueStore,
} from "../src/stores/receiptQueue.js";
import { useToastStore } from "../src/stores/toast.js";
import * as receiptsApi from "../src/api/receipts.js";

async function resetDb() {
  await resetReceiptQueueStore();
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

describe("flushReceiptQueue", () => {
  it("posts each queued receipt and removes them on success", async () => {
    const queue = useReceiptQueueStore();
    await queue.enqueue("https://example.com/r1");
    await queue.enqueue("https://example.com/r2");

    const post = vi
      .spyOn(receiptsApi, "postReceipt")
      .mockResolvedValue({ status: "ok" });

    await flushReceiptQueue();

    expect(post).toHaveBeenCalledTimes(2);
    expect(queue.items).toHaveLength(0);
  });

  it("shows 'Receipt saved' toast on ok and 'Already saved' toast on duplicate", async () => {
    const queue = useReceiptQueueStore();
    await queue.enqueue("https://example.com/r1");
    await queue.enqueue("https://example.com/r2");

    vi.spyOn(receiptsApi, "postReceipt")
      .mockResolvedValueOnce({ status: "ok" })
      .mockResolvedValueOnce({ status: "duplicate" });

    const toast = useToastStore();
    const showSpy = vi.spyOn(toast, "show");

    await flushReceiptQueue();

    expect(showSpy).toHaveBeenCalledWith("Receipt saved", "success");
    expect(showSpy).toHaveBeenCalledWith("Already saved", "info");
  });

  it("passes client_receipt_id and url to postReceipt", async () => {
    const queue = useReceiptQueueStore();
    await queue.enqueue("https://example.com/r");
    const item = queue.items[0];

    const post = vi
      .spyOn(receiptsApi, "postReceipt")
      .mockResolvedValue({ status: "ok" });

    await flushReceiptQueue();

    expect(post).toHaveBeenCalledWith({
      client_receipt_id: item.client_receipt_id,
      url: item.url,
    });
  });

  it("removes the item on 409 conflict and continues to the next", async () => {
    const queue = useReceiptQueueStore();
    await queue.enqueue("https://example.com/r1");
    await queue.enqueue("https://example.com/r2");

    vi.spyOn(receiptsApi, "postReceipt")
      .mockRejectedValueOnce(Object.assign(new Error("conflict"), { status: 409 }))
      .mockResolvedValueOnce({ status: "ok" });

    await flushReceiptQueue();

    expect(queue.items).toHaveLength(0);
  });

  it("stops the sweep on a transient error and records lastFlushError", async () => {
    const queue = useReceiptQueueStore();
    await queue.enqueue("https://example.com/r1");
    await queue.enqueue("https://example.com/r2");

    const err = new Error("network down");
    vi.spyOn(receiptsApi, "postReceipt").mockRejectedValue(err);

    await flushReceiptQueue();

    expect(queue.items).toHaveLength(2);
    expect(queue.lastFlushError?.message).toBe("network down");
  });

  it("stops the sweep on a 500 error and keeps remaining items", async () => {
    const queue = useReceiptQueueStore();
    await queue.enqueue("https://example.com/r1");
    await queue.enqueue("https://example.com/r2");

    vi.spyOn(receiptsApi, "postReceipt")
      .mockRejectedValueOnce(Object.assign(new Error("server error"), { status: 500 }));

    await flushReceiptQueue();

    expect(queue.items.length).toBeGreaterThanOrEqual(1);
    expect(queue.lastFlushError?.message).toBe("server error");
  });

  it("is reentrant-safe: a second flushReceiptQueue while one is in flight is a no-op", async () => {
    const queue = useReceiptQueueStore();
    await queue.enqueue("https://example.com/r");

    const post = vi
      .spyOn(receiptsApi, "postReceipt")
      .mockImplementation(() => new Promise((resolve) => setTimeout(() => resolve({}), 50)));

    const a = flushReceiptQueue();
    const b = flushReceiptQueue();
    await Promise.all([a, b]);

    expect(post).toHaveBeenCalledTimes(1);
  });

  it("clears lastFlushError from a previous sweep when a new sweep starts", async () => {
    const queue = useReceiptQueueStore();
    await queue.enqueue("https://example.com/r");

    vi.spyOn(receiptsApi, "postReceipt").mockRejectedValue(new Error("first"));
    await flushReceiptQueue();
    expect(queue.lastFlushError?.message).toBe("first");

    vi.spyOn(receiptsApi, "postReceipt").mockResolvedValue({ status: "ok" });
    resetFlush();
    await flushReceiptQueue();
    expect(queue.lastFlushError).toBeNull();
  });
});

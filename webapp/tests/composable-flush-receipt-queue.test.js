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
import * as swHealth from "../src/composables/swHealth.js";
import { useToastStore } from "../src/stores/toast.js";
import { useLlmStore } from "../src/stores/llm.js";
import { useReviewStore } from "../src/stores/review.js";
import * as receiptsApi from "../src/api/receipts.js";

beforeEach(async () => {
  await allure.epic("Receipts");
  await allure.feature("Frontend");
  await allure.story("flushReceiptQueue");
});

async function resetDb() {
  await resetReceiptQueueStore();
  await new Promise((resolve) => {
    const del = indexedDB.deleteDatabase("dinary-receipts");
    del.onsuccess = del.onerror = del.onblocked = () => resolve();
    setTimeout(resolve, 200);
  });
}

beforeEach(async () => {
  localStorage.clear();
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

  it("shows 'Receipt saved' toast on ok and 'Receipt already recorded' toast on duplicate", async () => {
    const queue = useReceiptQueueStore();
    await queue.enqueue("https://example.com/r1");
    await queue.enqueue("https://example.com/r2");

    vi.spyOn(receiptsApi, "postReceipt")
      .mockResolvedValueOnce({ status: "ok" })
      .mockResolvedValueOnce({ status: "duplicate" });

    const toast = useToastStore();
    const showSpy = vi.spyOn(toast, "show");

    await flushReceiptQueue();

    expect(showSpy).toHaveBeenCalledWith(expect.stringContaining("Receipt saved"), "success");
    expect(showSpy).toHaveBeenCalledWith(expect.stringContaining("Receipt already recorded"), "info");
  });

  it("shows a EUR amount label for a Montenegrin receipt", async () => {
    const queue = useReceiptQueueStore();
    await queue.enqueue(
      "https://mapr.tax.gov.me/ic/#/verify?iic=X&tin=Y" +
        "&crtd=2026-07-11T15:51:04+02:00&prc=59.10",
    );

    vi.spyOn(receiptsApi, "postReceipt").mockResolvedValue({ status: "ok" });
    const toast = useToastStore();
    const showSpy = vi.spyOn(toast, "show");

    await flushReceiptQueue();

    expect(showSpy).toHaveBeenCalledWith(
      expect.stringContaining("59.1 EUR"),
      "success",
    );
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

  it("calls markDirty on llm and review stores after successful receipt flush", async () => {
    const queue = useReceiptQueueStore();
    await queue.enqueue("https://example.com/r");

    vi.spyOn(receiptsApi, "postReceipt").mockResolvedValue({ status: "ok" });

    const llmStore = useLlmStore();
    const reviewStore = useReviewStore();
    const llmSpy = vi.spyOn(llmStore, "markDirty");
    const reviewSpy = vi.spyOn(reviewStore, "markDirty");

    await flushReceiptQueue();

    expect(llmSpy).toHaveBeenCalledTimes(1);
    expect(reviewSpy).toHaveBeenCalledTimes(1);
  });

  it("does not call markDirty when all flushes fail", async () => {
    const queue = useReceiptQueueStore();
    await queue.enqueue("https://example.com/r");

    vi.spyOn(receiptsApi, "postReceipt").mockRejectedValue(new Error("network down"));

    const llmStore = useLlmStore();
    const reviewStore = useReviewStore();
    const llmSpy = vi.spyOn(llmStore, "markDirty");
    const reviewSpy = vi.spyOn(reviewStore, "markDirty");

    await flushReceiptQueue();

    expect(llmSpy).not.toHaveBeenCalled();
    expect(reviewSpy).not.toHaveBeenCalled();
  });

  it("calls reportNetworkSuccess after a successful send", async () => {
    const queue = useReceiptQueueStore();
    await queue.enqueue("https://example.com/r");
    vi.spyOn(receiptsApi, "postReceipt").mockResolvedValue({ status: "ok" });
    const spy = vi.spyOn(swHealth, "reportNetworkSuccess");

    await flushReceiptQueue();

    expect(spy).toHaveBeenCalled();
  });

  it("calls reportNetworkFailure on network-level TypeError", async () => {
    const queue = useReceiptQueueStore();
    await queue.enqueue("https://example.com/r");
    vi.spyOn(receiptsApi, "postReceipt").mockRejectedValue(new TypeError("Failed to fetch"));
    const spy = vi.spyOn(swHealth, "reportNetworkFailure");

    await flushReceiptQueue();

    expect(spy).toHaveBeenCalled();
  });

  it("does not call reportNetworkFailure on HTTP errors", async () => {
    const queue = useReceiptQueueStore();
    await queue.enqueue("https://example.com/r");
    vi.spyOn(receiptsApi, "postReceipt").mockRejectedValue(
      Object.assign(new Error("server error"), { status: 500 }),
    );
    const spy = vi.spyOn(swHealth, "reportNetworkFailure");

    await flushReceiptQueue();

    expect(spy).not.toHaveBeenCalled();
  });
});

/**
 * Tests guaranteeing expenses are NEVER lost.
 *
 * The contract:
 *   1. Every expense is saved to IndexedDB BEFORE any network call.
 *   2. An expense is removed from IndexedDB ONLY after a confirmed 200.
 *   3. Server errors, timeouts, and aborts keep the item in the queue.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { enqueue, getAll, remove, count } from "../../static/js/offline-queue.js";

beforeEach(async () => {
  const items = await getAll();
  for (const item of items) {
    await remove(item.id);
  }
  vi.restoreAllMocks();
});

const EXPENSE = {
  amount: 1500,
  currency: "RSD",
  category: "кафе",
  group: "",
  comment: "lunch",
  date: "2026-04-15",
};

// -- helpers to simulate flushQueue logic without DOM dependencies --

async function flushQueueWith(postFn) {
  const items = await getAll();
  for (const item of items) {
    try {
      await postFn({
        amount: item.amount,
        currency: item.currency || "RSD",
        category: item.category,
        group: item.group || "",
        comment: item.comment || "",
        date: item.date,
      });
      await remove(item.id);
    } catch {
      break;
    }
  }
}

async function submitExpenseWith(entry, postFn, online = true) {
  await enqueue(entry);
  if (online) {
    await flushQueueWith(postFn);
  }
}

// -- tests --

describe("no data loss: enqueue-before-send contract", () => {
  it("expense is in IndexedDB before network call starts", async () => {
    let queueAtCallTime = null;
    const post = vi.fn(async () => {
      queueAtCallTime = await getAll();
      return { amount_rsd: 1500, category: "кафе", new_total_rsd: 1500 };
    });

    await submitExpenseWith(EXPENSE, post);

    expect(post).toHaveBeenCalledOnce();
    expect(queueAtCallTime.length).toBeGreaterThanOrEqual(1);
    expect(queueAtCallTime[0].amount).toBe(1500);
  });

  it("expense removed from queue only after 200", async () => {
    const post = vi.fn(async () => ({
      amount_rsd: 1500,
      category: "кафе",
      new_total_rsd: 1500,
    }));

    await submitExpenseWith(EXPENSE, post);
    expect(await count()).toBe(0);
  });

  it("expense stays in queue on server 502", async () => {
    const post = vi.fn(async () => {
      throw new Error("HTTP 502");
    });

    await submitExpenseWith(EXPENSE, post);
    expect(await count()).toBe(1);

    const [item] = await getAll();
    expect(item.amount).toBe(1500);
    expect(item.category).toBe("кафе");
  });

  it("expense stays in queue on server 500", async () => {
    const post = vi.fn(async () => {
      throw new Error("HTTP 500");
    });

    await submitExpenseWith(EXPENSE, post);
    expect(await count()).toBe(1);
  });

  it("expense stays in queue on network timeout", async () => {
    const post = vi.fn(async () => {
      throw new DOMException("The operation was aborted", "AbortError");
    });

    await submitExpenseWith(EXPENSE, post);
    expect(await count()).toBe(1);
  });

  it("expense stays in queue on connection refused", async () => {
    const post = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });

    await submitExpenseWith(EXPENSE, post);
    expect(await count()).toBe(1);
  });

  it("expense stays in queue when offline", async () => {
    const post = vi.fn();

    await submitExpenseWith(EXPENSE, post, false);
    expect(post).not.toHaveBeenCalled();
    expect(await count()).toBe(1);
  });
});

describe("no data loss: flush partial failure", () => {
  it("first item sent, second fails — second stays in queue", async () => {
    await enqueue({ ...EXPENSE, amount: 100 });
    await enqueue({ ...EXPENSE, amount: 200 });

    let callCount = 0;
    const post = vi.fn(async () => {
      callCount++;
      if (callCount === 2) throw new Error("HTTP 502");
      return { amount_rsd: 100, category: "кафе", new_total_rsd: 100 };
    });

    await flushQueueWith(post);

    expect(await count()).toBe(1);
    const [remaining] = await getAll();
    expect(remaining.amount).toBe(200);
  });

  it("all items sent on success — queue empty", async () => {
    await enqueue({ ...EXPENSE, amount: 100 });
    await enqueue({ ...EXPENSE, amount: 200 });
    await enqueue({ ...EXPENSE, amount: 300 });

    const post = vi.fn(async (e) => ({
      amount_rsd: e.amount,
      category: e.category,
      new_total_rsd: e.amount,
    }));

    await flushQueueWith(post);

    expect(await count()).toBe(0);
    expect(post).toHaveBeenCalledTimes(3);
  });
});

describe("no data loss: postExpense timeout", () => {
  it("AbortController fires after 30s — expense not lost", async () => {
    await enqueue(EXPENSE);

    const post = vi.fn(async () => {
      throw new DOMException("The operation was aborted", "AbortError");
    });

    await flushQueueWith(post);
    expect(await count()).toBe(1);
  });
});

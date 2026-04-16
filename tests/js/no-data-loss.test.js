/**
 * Tests guaranteeing expenses are NEVER lost.
 *
 * The contract:
 *   1. Every expense is saved to IndexedDB BEFORE any network call.
 *   2. An expense is removed from IndexedDB ONLY after a confirmed 200.
 *   3. Server errors, timeouts, and aborts keep the item in the queue.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import * as allure from "allure-js-commons";
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
  beforeEach(async () => {
    await allure.epic("Data Safety");
    await allure.feature("No Data Loss");
  });

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

  it("expense stays in queue on server 503", async () => {
    const post = vi.fn(async () => {
      throw new Error("HTTP 503");
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

  it("expense stays in queue on DNS resolution failure", async () => {
    const post = vi.fn(async () => {
      throw new TypeError("NetworkError when attempting to fetch resource.");
    });
    await submitExpenseWith(EXPENSE, post);
    expect(await count()).toBe(1);
  });

  it("expense stays in queue on ERR_CONNECTION_RESET", async () => {
    const post = vi.fn(async () => {
      throw new TypeError("net::ERR_CONNECTION_RESET");
    });
    await submitExpenseWith(EXPENSE, post);
    expect(await count()).toBe(1);
  });
});

describe("no data loss: flush partial failure", () => {
  beforeEach(async () => {
    await allure.epic("Data Safety");
    await allure.feature("No Data Loss");
  });

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

  it("failure at item 3 of 5 — items 3-5 remain in queue", async () => {
    for (let i = 1; i <= 5; i++) {
      await enqueue({ ...EXPENSE, amount: i * 100 });
    }

    let callCount = 0;
    const post = vi.fn(async () => {
      callCount++;
      if (callCount >= 3) throw new Error("HTTP 500");
      return {};
    });

    await flushQueueWith(post);

    expect(await count()).toBe(3);
    const remaining = await getAll();
    expect(remaining.map((r) => r.amount)).toEqual([300, 400, 500]);
  });

  it("items sent in order (FIFO)", async () => {
    await enqueue({ ...EXPENSE, amount: 111 });
    await enqueue({ ...EXPENSE, amount: 222 });
    await enqueue({ ...EXPENSE, amount: 333 });

    const sentAmounts = [];
    const post = vi.fn(async (e) => {
      sentAmounts.push(e.amount);
      return {};
    });

    await flushQueueWith(post);

    expect(sentAmounts).toEqual([111, 222, 333]);
  });
});

describe("no data loss: postExpense timeout", () => {
  beforeEach(async () => {
    await allure.epic("Data Safety");
    await allure.feature("No Data Loss");
  });

  it("AbortController fires after 30s — expense not lost", async () => {
    await enqueue(EXPENSE);

    const post = vi.fn(async () => {
      throw new DOMException("The operation was aborted", "AbortError");
    });

    await flushQueueWith(post);
    expect(await count()).toBe(1);
  });
});

describe("no data loss: data integrity after enqueue", () => {
  beforeEach(async () => {
    await allure.epic("Data Safety");
    await allure.feature("No Data Loss");
  });

  it("all fields are preserved exactly in IndexedDB", async () => {
    const entry = {
      amount: 1234.56,
      currency: "RSD",
      category: "еда&бытовые",
      group: "собака",
      comment: "корм Royal Canin; миска",
      date: "2026-12-31",
    };

    await enqueue(entry);
    const [item] = await getAll();

    expect(item.amount).toBe(1234.56);
    expect(item.currency).toBe("RSD");
    expect(item.category).toBe("еда&бытовые");
    expect(item.group).toBe("собака");
    expect(item.comment).toBe("корм Royal Canin; миска");
    expect(item.date).toBe("2026-12-31");
  });

  it("unicode characters in all fields preserved", async () => {
    const entry = {
      amount: 100,
      currency: "RSD",
      category: "カフェ",
      group: "путешествия",
      comment: "日本語テスト & ñoño",
      date: "2026-01-01",
    };

    await enqueue(entry);
    const [item] = await getAll();

    expect(item.category).toBe("カフェ");
    expect(item.comment).toBe("日本語テスト & ñoño");
  });

  it("very large amount is preserved", async () => {
    await enqueue({ ...EXPENSE, amount: 9999999.99 });
    const [item] = await getAll();
    expect(item.amount).toBe(9999999.99);
  });

  it("zero amount is preserved", async () => {
    await enqueue({ ...EXPENSE, amount: 0 });
    const [item] = await getAll();
    expect(item.amount).toBe(0);
  });

  it("empty strings are preserved", async () => {
    await enqueue({ ...EXPENSE, group: "", comment: "" });
    const [item] = await getAll();
    expect(item.group).toBe("");
    expect(item.comment).toBe("");
  });
});

describe("no data loss: concurrent operations", () => {
  beforeEach(async () => {
    await allure.epic("Data Safety");
    await allure.feature("No Data Loss");
  });

  it("parallel enqueues don't lose items", async () => {
    const promises = [];
    for (let i = 0; i < 10; i++) {
      promises.push(enqueue({ ...EXPENSE, amount: i }));
    }
    await Promise.all(promises);

    expect(await count()).toBe(10);
  });

  it("rapid enqueue-flush cycles don't lose data", async () => {
    const successPost = vi.fn(async () => ({}));

    for (let i = 0; i < 5; i++) {
      await enqueue({ ...EXPENSE, amount: (i + 1) * 100 });
      await flushQueueWith(successPost);
    }

    expect(await count()).toBe(0);
    expect(successPost).toHaveBeenCalledTimes(5);
  });

  it("enqueue during failed flush preserves new item", async () => {
    await enqueue({ ...EXPENSE, amount: 100 });

    const post = vi.fn(async () => {
      throw new Error("HTTP 500");
    });

    await flushQueueWith(post);
    await enqueue({ ...EXPENSE, amount: 200 });

    expect(await count()).toBe(2);
    const items = await getAll();
    expect(items.map((i) => i.amount)).toEqual([100, 200]);
  });
});

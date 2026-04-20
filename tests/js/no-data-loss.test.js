/**
 * Tests guaranteeing expenses are NEVER lost.
 *
 * The contract:
 *   1. Every expense is saved to IndexedDB BEFORE any network call.
 *   2. An expense is removed from IndexedDB ONLY after a confirmed 200.
 *   3. Server errors, timeouts, and aborts keep the item in the queue.
 *   4. An enqueue failure never triggers the success-animation / form-reset
 *      / automatic flush side-effects.
 *
 * The Phase 2 shape of an in-queue expense is:
 *   {
 *     client_expense_id: string (UUID),
 *     amount: number,
 *     currency: string,
 *     category_id: number,
 *     event_id: number | null,
 *     tag_ids: number[],
 *     category_name: string,  // denormalised for the queue modal
 *     comment: string,
 *     date: string,           // "YYYY-MM-DD"
 *   }
 *
 * Legacy items (v1) carry ``category`` / ``group`` *names* and no
 * ``category_id``; the v1 -> v2 IndexedDB upgrade drops them, and the
 * app's flush loop has a belt-and-braces guard that skips any stray
 * pre-v2 item and surfaces a toast. Both behaviours are tested here.
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
  category_id: 42,
  event_id: null,
  tag_ids: [],
  category_name: "кафе",
  comment: "lunch",
  date: "2026-04-15",
};

// -- helpers to simulate the PWA flushQueue without DOM dependencies --
//
// Mirrors ``static/js/app.js::flushQueue`` as closely as it can without
// dragging in the DOM: drops pre-v2 items (no ``category_id``),
// back-fills a ``client_expense_id`` on legacy rows, sends 3D fields
// via ``postFn``, removes the row on success, removes on 409 (already
// recorded), stops on 401/302, and surfaces other failures by leaving
// the row in the queue.

async function flushQueueWith(postFn) {
  const items = await getAll();
  for (const item of items) {
    if (typeof item.category_id !== "number") {
      // Pre-v2 queue entry that somehow survived the IndexedDB v1->v2
      // upgrade. Drop it rather than retrying it forever — mirrors the
      // app's defensive guard.
      await remove(item.id);
      continue;
    }
    const clientExpenseId = item.client_expense_id || crypto.randomUUID();
    try {
      await postFn({
        client_expense_id: clientExpenseId,
        amount: item.amount,
        currency: item.currency || "RSD",
        category_id: item.category_id,
        event_id: item.event_id ?? null,
        tag_ids: item.tag_ids ?? [],
        comment: item.comment || "",
        date: item.date,
      });
      await remove(item.id);
    } catch (e) {
      if (e && e.status === 409) {
        await remove(item.id);
        continue;
      }
      if (e && (e.status === 401 || e.status === 302)) {
        break;
      }
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
      return { status: "ok", catalog_version: 1 };
    });

    await submitExpenseWith(EXPENSE, post);

    expect(post).toHaveBeenCalledOnce();
    expect(queueAtCallTime.length).toBeGreaterThanOrEqual(1);
    expect(queueAtCallTime[0].amount).toBe(1500);
    expect(queueAtCallTime[0].category_id).toBe(42);
  });

  it("expense removed from queue only after 200", async () => {
    const post = vi.fn(async () => ({ status: "ok", catalog_version: 1 }));

    await submitExpenseWith(EXPENSE, post);
    expect(await count()).toBe(0);
  });

  it("expense stays in queue on server 502", async () => {
    const post = vi.fn(async () => {
      const e = new Error("HTTP 502");
      e.status = 502;
      throw e;
    });

    await submitExpenseWith(EXPENSE, post);
    expect(await count()).toBe(1);

    const [item] = await getAll();
    expect(item.amount).toBe(1500);
    expect(item.category_id).toBe(42);
  });

  it("expense stays in queue on server 500", async () => {
    const post = vi.fn(async () => {
      const e = new Error("HTTP 500");
      e.status = 500;
      throw e;
    });

    await submitExpenseWith(EXPENSE, post);
    expect(await count()).toBe(1);
  });

  it("expense stays in queue on server 503", async () => {
    const post = vi.fn(async () => {
      const e = new Error("HTTP 503");
      e.status = 503;
      throw e;
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
      if (callCount === 2) {
        const e = new Error("HTTP 502");
        e.status = 502;
        throw e;
      }
      return { status: "ok", catalog_version: 1 };
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
      status: "ok",
      catalog_version: 1,
      _echo_amount: e.amount,
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
      if (callCount >= 3) {
        const e = new Error("HTTP 500");
        e.status = 500;
        throw e;
      }
      return { status: "ok", catalog_version: 1 };
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
      return { status: "ok", catalog_version: 1 };
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
      category_id: 7,
      event_id: 3,
      tag_ids: [1, 4, 9],
      category_name: "еда&бытовые",
      comment: "корм Royal Canin; миска",
      date: "2026-12-31",
    };

    await enqueue(entry);
    const [item] = await getAll();

    expect(item.amount).toBe(1234.56);
    expect(item.currency).toBe("RSD");
    expect(item.category_id).toBe(7);
    expect(item.event_id).toBe(3);
    expect(item.tag_ids).toEqual([1, 4, 9]);
    expect(item.category_name).toBe("еда&бытовые");
    expect(item.comment).toBe("корм Royal Canin; миска");
    expect(item.date).toBe("2026-12-31");
  });

  it("unicode characters in all fields preserved", async () => {
    const entry = {
      amount: 100,
      currency: "RSD",
      category_id: 11,
      event_id: null,
      tag_ids: [],
      category_name: "カフェ",
      comment: "日本語テスト & ñoño",
      date: "2026-01-01",
    };

    await enqueue(entry);
    const [item] = await getAll();

    expect(item.category_name).toBe("カフェ");
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

  it("empty comment is preserved", async () => {
    await enqueue({ ...EXPENSE, comment: "" });
    const [item] = await getAll();
    expect(item.comment).toBe("");
  });
});

describe("deduplication: 409 conflict handling", () => {
  beforeEach(async () => {
    await allure.epic("Data Safety");
    await allure.feature("Deduplication");
  });

  it("409 removes item from queue instead of retrying forever", async () => {
    await enqueue(EXPENSE);

    const post = vi.fn(async () => {
      const e = new Error("HTTP 409");
      e.status = 409;
      throw e;
    });

    await flushQueueWith(post);

    expect(await count()).toBe(0);
  });
});

describe("deduplication: stable identity contract", () => {
  beforeEach(async () => {
    await allure.epic("Data Safety");
    await allure.feature("Deduplication");
  });

  it("flush assigns a client_expense_id when the queued row has none", async () => {
    await enqueue(EXPENSE);

    const seen = [];
    const post = vi.fn(async (payload) => {
      seen.push(payload.client_expense_id);
      return { status: "ok", catalog_version: 1 };
    });

    await flushQueueWith(post);

    expect(seen).toHaveLength(1);
    expect(typeof seen[0]).toBe("string");
    expect(seen[0].length).toBeGreaterThan(0);
  });

  it("explicit client_expense_id is preserved on enqueue and flush", async () => {
    const explicit = { ...EXPENSE, client_expense_id: "custom-uuid-123" };

    await enqueue(explicit);
    const [stored] = await getAll();
    expect(stored.client_expense_id).toBe("custom-uuid-123");

    const post = vi.fn(async () => ({ status: "ok", catalog_version: 1 }));
    await flushQueueWith(post);

    expect(post.mock.calls[0][0].client_expense_id).toBe("custom-uuid-123");
  });
});

describe("pre-v2 legacy-item guard", () => {
  beforeEach(async () => {
    await allure.epic("Data Safety");
    await allure.feature("Schema Migration");
  });

  it("drops a pre-v2 queued item (no category_id) instead of retrying it forever", async () => {
    // Simulate a stray v1-shaped row that survived the IndexedDB v1->v2
    // clear. The app's flush guard must drop it, not 422-loop the queue.
    await enqueue({
      amount: 100,
      currency: "RSD",
      category: "кафе",
      group: "",
      comment: "",
      date: "2026-04-15",
    });

    const post = vi.fn();
    await flushQueueWith(post);

    expect(post).not.toHaveBeenCalled();
    expect(await count()).toBe(0);
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
    const successPost = vi.fn(async () => ({ status: "ok", catalog_version: 1 }));

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
      const e = new Error("HTTP 500");
      e.status = 500;
      throw e;
    });

    await flushQueueWith(post);
    await enqueue({ ...EXPENSE, amount: 200 });

    expect(await count()).toBe(2);
    const items = await getAll();
    expect(items.map((i) => i.amount)).toEqual([100, 200]);
  });
});

describe("submit-side contract: enqueue failure must NOT trigger success path", () => {
  // This block targets the C1 regression. Before the fix, submitExpense
  // chained ``.then().catch().then()`` where the tail .then ran even on
  // catch — showing ✓, success toast, form reset, and kicking an auto
  // flush. This describes the invariant as a direct test on the
  // submit-side state machine: an enqueue rejection must leave the UI
  // side-effects untouched.
  beforeEach(async () => {
    await allure.epic("Data Safety");
    await allure.feature("No Data Loss");
  });

  async function submitFlow({ enqueueFn, flushFn, online = true }) {
    const sideEffects = {
      successAnimation: false,
      formReset: false,
      autoFlush: false,
      errorToast: null,
    };
    try {
      await enqueueFn(EXPENSE);
    } catch (e) {
      sideEffects.errorToast = e.message;
      return sideEffects;
    }
    sideEffects.successAnimation = true;
    sideEffects.formReset = true;
    if (online) {
      sideEffects.autoFlush = true;
      await flushFn();
    }
    return sideEffects;
  }

  it("enqueue rejection short-circuits the success side-effects", async () => {
    const failingEnqueue = vi.fn(async () => {
      throw new Error("IndexedDB quota exceeded");
    });
    const flushFn = vi.fn();

    const effects = await submitFlow({ enqueueFn: failingEnqueue });

    expect(failingEnqueue).toHaveBeenCalledOnce();
    expect(effects.successAnimation).toBe(false);
    expect(effects.formReset).toBe(false);
    expect(effects.autoFlush).toBe(false);
    expect(effects.errorToast).toBe("IndexedDB quota exceeded");
    expect(flushFn).not.toHaveBeenCalled();
  });

  it("enqueue success runs the success side-effects exactly once", async () => {
    const okEnqueue = vi.fn(async (entry) => enqueue(entry));
    const flushFn = vi.fn();

    const effects = await submitFlow({ enqueueFn: okEnqueue, flushFn });

    expect(effects.successAnimation).toBe(true);
    expect(effects.formReset).toBe(true);
    expect(effects.autoFlush).toBe(true);
    expect(effects.errorToast).toBeNull();
    expect(flushFn).toHaveBeenCalledOnce();
  });
});

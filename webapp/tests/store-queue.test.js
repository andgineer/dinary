import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import {
  useQueueStore,
  _resetForTest,
  isDisconnectError,
} from "../src/stores/queue.js";

beforeEach(async () => {
  await allure.epic("Stores");
  await allure.feature("Queue store");
});

async function resetQueueDb() {
  await _resetForTest();
  await new Promise((resolve) => {
    const del = indexedDB.deleteDatabase("dinary-v2");
    del.onsuccess = del.onerror = del.onblocked = () => resolve();
    setTimeout(resolve, 1000);
  });
}

beforeEach(async () => {
  setActivePinia(createPinia());
  await resetQueueDb();
});

afterEach(async () => {
  vi.restoreAllMocks();
  await resetQueueDb();
});

describe("queue store: enqueue / list / remove", () => {
  it("enqueue persists item and exposes it via items", async () => {
    const store = useQueueStore();
    await store.enqueue({ amount: 100, currency: "RSD", date: "2026-05-04" });
    expect(store.items).toHaveLength(1);
    expect(store.items[0]).toMatchObject({ amount: 100, currency: "RSD" });
    expect(store.items[0].client_expense_id).toBeTypeOf("string");
    expect(store.items[0].queued_at).toBeTypeOf("number");
  });

  it("preserves client_expense_id when caller supplies one", async () => {
    const store = useQueueStore();
    await store.enqueue({
      client_expense_id: "fixed-id",
      amount: 1,
      currency: "RSD",
      date: "2026-05-04",
    });
    expect(store.items[0].client_expense_id).toBe("fixed-id");
  });

  it("multiple enqueues accumulate; remove deletes by id", async () => {
    const store = useQueueStore();
    await store.enqueue({ amount: 1, currency: "RSD", date: "2026-01-01" });
    await store.enqueue({ amount: 2, currency: "RSD", date: "2026-01-02" });
    await store.enqueue({ amount: 3, currency: "RSD", date: "2026-01-03" });
    expect(await store.count()).toBe(3);

    const target = store.items[1];
    await store.remove(target.id);
    expect(store.items.map((i) => i.amount).sort()).toEqual([1, 3]);
  });

  it("update modifies an existing item in place", async () => {
    const store = useQueueStore();
    await store.enqueue({ amount: 100, currency: "RSD", date: "2026-05-04" });
    const item = { ...store.items[0], comment: "edited" };
    await store.update(item);
    expect(store.items[0].comment).toBe("edited");
  });

  it("refresh re-reads from IndexedDB", async () => {
    const store = useQueueStore();
    await store.enqueue({ amount: 5, currency: "RSD", date: "2026-05-04" });
    store.items = [];
    await store.refresh();
    expect(store.items).toHaveLength(1);
  });
});

describe("queue store: flush", () => {
  it("removes items the sender succeeds on, keeps failures, and records first error", async () => {
    const store = useQueueStore();
    await store.enqueue({ amount: 1, currency: "RSD", date: "2026-01-01" });
    await store.enqueue({ amount: 2, currency: "RSD", date: "2026-01-01" });
    await store.enqueue({ amount: 3, currency: "RSD", date: "2026-01-01" });

    const sendOne = vi.fn(async (item) => {
      if (item.amount === 2) throw new Error("server 500");
    });

    await store.flush(sendOne);

    expect(sendOne).toHaveBeenCalledTimes(3);
    expect(store.items).toHaveLength(1);
    expect(store.items[0].amount).toBe(2);
    expect(store.lastFlushError?.message).toBe("server 500");
  });

  it("clears lastFlushError when a fresh flush succeeds", async () => {
    const store = useQueueStore();
    await store.enqueue({ amount: 1, currency: "RSD", date: "2026-01-01" });

    await store.flush(async () => {
      throw new Error("first");
    });
    expect(store.lastFlushError?.message).toBe("first");

    // Re-enqueue (it failed), then succeed.
    await store.flush(async () => {});
    expect(store.lastFlushError).toBeNull();
  });
});

describe("queue store: Bug #4 — disconnect-and-retry", () => {
  it("retries once after an InvalidStateError and succeeds", async () => {
    const store = useQueueStore();
    await store.enqueue({ amount: 1, currency: "RSD", date: "2026-01-01" });

    // Simulate the cached IndexedDB connection being unexpectedly
    // closed: monkeypatch transaction() to throw InvalidStateError on
    // first call, then restore. The store's withDb helper must drop
    // the cached _dbPromise, reopen, and complete the second attempt.
    const original = IDBDatabase.prototype.transaction;
    let calls = 0;
    IDBDatabase.prototype.transaction = function patched(...args) {
      calls += 1;
      if (calls === 1) {
        const err = new Error("Database is disconnecting");
        err.name = "InvalidStateError";
        throw err;
      }
      return original.apply(this, args);
    };

    try {
      await store.enqueue({ amount: 2, currency: "RSD", date: "2026-01-01" });
    } finally {
      IDBDatabase.prototype.transaction = original;
    }

    expect(calls).toBeGreaterThanOrEqual(2);
    expect(store.items.map((i) => i.amount).sort()).toEqual([1, 2]);
  });

});

describe("queue store: isDisconnectError", () => {
  it("matches the IndexedDB disconnect error names", () => {
    for (const name of [
      "InvalidStateError",
      "TransactionInactiveError",
      "AbortError",
      "UnknownError",
    ]) {
      const err = new Error("x");
      err.name = name;
      expect(isDisconnectError(err)).toBe(true);
    }
  });

  it("matches the disconnect-style error messages", () => {
    expect(isDisconnectError(new Error("Database is disconnecting"))).toBe(true);
    expect(isDisconnectError(new Error("the database connection is closing"))).toBe(true);
    expect(isDisconnectError(new Error("connection was closed"))).toBe(true);
  });

  it("rejects unrelated errors so they bubble up to the caller", () => {
    expect(isDisconnectError(null)).toBe(false);
    expect(isDisconnectError(new Error("anything else"))).toBe(false);
    const err = new Error("boom");
    err.name = "DataError";
    expect(isDisconnectError(err)).toBe(false);
    err.name = "ConstraintError";
    expect(isDisconnectError(err)).toBe(false);
  });
});

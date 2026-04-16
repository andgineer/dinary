import { describe, it, expect, beforeEach } from "vitest";
import * as allure from "allure-js-commons";
import { enqueue, getAll, remove, count } from "../../static/js/offline-queue.js";

beforeEach(async () => {
  const items = await getAll();
  for (const item of items) {
    await remove(item.id);
  }
});

describe("offline-queue", () => {
  beforeEach(async () => {
    await allure.epic("Data Safety");
    await allure.feature("Offline Queue");
  });

  it("enqueue persists expense in IndexedDB", async () => {
    await enqueue({ amount: 500, category: "Food", date: "2026-04-15" });
    const items = await getAll();
    expect(items).toHaveLength(1);
    expect(items[0].amount).toBe(500);
    expect(items[0].category).toBe("Food");
    expect(items[0].queued_at).toBeTypeOf("number");
  });

  it("enqueue preserves all fields", async () => {
    await enqueue({
      amount: 100,
      currency: "RSD",
      category: "кафе",
      group: "путешествия",
      comment: "test",
      date: "2026-05-01",
    });
    const [item] = await getAll();
    expect(item.currency).toBe("RSD");
    expect(item.group).toBe("путешествия");
    expect(item.comment).toBe("test");
  });

  it("multiple enqueues accumulate", async () => {
    await enqueue({ amount: 1, category: "A", date: "2026-01-01" });
    await enqueue({ amount: 2, category: "B", date: "2026-01-02" });
    await enqueue({ amount: 3, category: "C", date: "2026-01-03" });
    expect(await count()).toBe(3);
  });

  it("remove only deletes the specified item", async () => {
    await enqueue({ amount: 1, category: "A", date: "2026-01-01" });
    await enqueue({ amount: 2, category: "B", date: "2026-01-02" });
    const items = await getAll();
    await remove(items[0].id);
    const remaining = await getAll();
    expect(remaining).toHaveLength(1);
    expect(remaining[0].category).toBe("B");
  });

  it("count returns zero for empty queue", async () => {
    expect(await count()).toBe(0);
  });
});

describe("offline-queue: edge cases", () => {
  beforeEach(async () => {
    await allure.epic("Data Safety");
    await allure.feature("Offline Queue");
  });

  it("each item gets a unique auto-increment id", async () => {
    await enqueue({ amount: 1, category: "A", date: "2026-01-01" });
    await enqueue({ amount: 2, category: "B", date: "2026-01-01" });
    await enqueue({ amount: 3, category: "C", date: "2026-01-01" });

    const items = await getAll();
    const ids = items.map((i) => i.id);
    const uniqueIds = new Set(ids);
    expect(uniqueIds.size).toBe(3);
  });

  it("queued_at timestamp is recent", async () => {
    const before = Date.now();
    await enqueue({ amount: 100, category: "X", date: "2026-01-01" });
    const after = Date.now();

    const [item] = await getAll();
    expect(item.queued_at).toBeGreaterThanOrEqual(before);
    expect(item.queued_at).toBeLessThanOrEqual(after);
  });

  it("remove with non-existent id does not throw", async () => {
    await enqueue({ amount: 100, category: "X", date: "2026-01-01" });
    await remove(99999);
    expect(await count()).toBe(1);
  });

  it("getAll returns items in insertion order", async () => {
    for (let i = 1; i <= 5; i++) {
      await enqueue({ amount: i * 100, category: `Cat${i}`, date: "2026-01-01" });
    }

    const items = await getAll();
    const amounts = items.map((i) => i.amount);
    expect(amounts).toEqual([100, 200, 300, 400, 500]);
  });

  it("remove first item preserves remaining order", async () => {
    await enqueue({ amount: 100, category: "A", date: "2026-01-01" });
    await enqueue({ amount: 200, category: "B", date: "2026-01-01" });
    await enqueue({ amount: 300, category: "C", date: "2026-01-01" });

    const items = await getAll();
    await remove(items[0].id);

    const remaining = await getAll();
    expect(remaining.map((r) => r.amount)).toEqual([200, 300]);
  });

  it("remove middle item preserves others", async () => {
    await enqueue({ amount: 100, category: "A", date: "2026-01-01" });
    await enqueue({ amount: 200, category: "B", date: "2026-01-01" });
    await enqueue({ amount: 300, category: "C", date: "2026-01-01" });

    const items = await getAll();
    await remove(items[1].id);

    const remaining = await getAll();
    expect(remaining.map((r) => r.amount)).toEqual([100, 300]);
  });

  it("handles many items (stress test)", async () => {
    for (let i = 0; i < 50; i++) {
      await enqueue({ amount: i, category: `Cat${i}`, date: "2026-01-01" });
    }
    expect(await count()).toBe(50);

    const items = await getAll();
    for (let i = 0; i < 25; i++) {
      await remove(items[i].id);
    }
    expect(await count()).toBe(25);
  });

  it("special characters in category and comment", async () => {
    await enqueue({
      amount: 100,
      category: "еда&бытовые",
      group: "",
      comment: '<script>alert("xss")</script>',
      date: "2026-01-01",
    });

    const [item] = await getAll();
    expect(item.category).toBe("еда&бытовые");
    expect(item.comment).toBe('<script>alert("xss")</script>');
  });

  it("decimal amounts preserved exactly", async () => {
    await enqueue({ amount: 0.1, category: "X", date: "2026-01-01" });
    await enqueue({ amount: 99.99, category: "Y", date: "2026-01-01" });
    await enqueue({ amount: 1234.56, category: "Z", date: "2026-01-01" });

    const items = await getAll();
    expect(items[0].amount).toBe(0.1);
    expect(items[1].amount).toBe(99.99);
    expect(items[2].amount).toBe(1234.56);
  });
});

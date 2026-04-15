import { describe, it, expect, beforeEach } from "vitest";
import { enqueue, getAll, remove, count } from "../../static/js/offline-queue.js";

beforeEach(async () => {
  const items = await getAll();
  for (const item of items) {
    await remove(item.id);
  }
});

describe("offline-queue", () => {
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

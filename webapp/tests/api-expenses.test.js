import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { postExpense, parseQr } from "../src/api/expenses.js";

let originalFetch;

function mockFetch(impl) {
  globalThis.fetch = vi.fn(impl);
}

beforeEach(() => {
  originalFetch = globalThis.fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("postExpense", () => {
  it("POSTs the expense body and returns parsed JSON", async () => {
    mockFetch(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ catalog_version: 7, id: 42 }),
    }));

    const result = await postExpense({
      client_expense_id: "abc",
      amount: 100,
      currency: "RSD",
      category_id: 1,
      event_id: null,
      tag_ids: [2, 3],
      comment: "lunch",
      expense_datetime: "2026-05-04T15:30:00+02:00",
    });

    expect(result).toEqual({ catalog_version: 7, id: 42 });
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/expenses",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }),
    );
    const body = JSON.parse(globalThis.fetch.mock.calls[0][1].body);
    expect(body).toMatchObject({
      client_expense_id: "abc",
      amount: 100,
      currency: "RSD",
      category_id: 1,
      event_id: null,
      tag_ids: [2, 3],
      comment: "lunch",
      expense_datetime: "2026-05-04T15:30:00+02:00",
    });
  });

  it("defaults event_id to null and tag_ids to []", async () => {
    mockFetch(async () => ({
      ok: true,
      status: 200,
      json: async () => ({}),
    }));

    await postExpense({
      client_expense_id: "x",
      amount: 1,
      currency: "RSD",
      category_id: 1,
      comment: "",
      date: "2026-05-04",
    });

    const body = JSON.parse(globalThis.fetch.mock.calls[0][1].body);
    expect(body.event_id).toBeNull();
    expect(body.tag_ids).toEqual([]);
  });

  it("throws an Error with status when the server responds non-2xx", async () => {
    mockFetch(async () => ({
      ok: false,
      status: 422,
      json: async () => ({ detail: "amount must be positive" }),
    }));

    await expect(
      postExpense({
        client_expense_id: "x",
        amount: -1,
        currency: "RSD",
        category_id: 1,
        comment: "",
        date: "2026-05-04",
      }),
    ).rejects.toMatchObject({
      message: "amount must be positive",
      status: 422,
    });
  });
});

describe("parseQr", () => {
  it("POSTs the URL and returns parsed body", async () => {
    mockFetch(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ amount: 350, items: [] }),
    }));

    const out = await parseQr("https://example/receipt");

    expect(out).toEqual({ amount: 350, items: [] });
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/qr/parse",
      expect.objectContaining({ method: "POST" }),
    );
  });
});

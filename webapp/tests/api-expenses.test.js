import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { postExpense, parseQr, deleteExpense } from "../src/api/expenses.js";

beforeEach(async () => {
  await allure.epic("Expenses");
  await allure.feature("API client");
});

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

describe("deleteExpense", () => {
  it("sends DELETE to /api/expenses/:id and returns null on 204", async () => {
    mockFetch(async () => ({
      ok: true,
      status: 204,
      headers: { get: () => "0" },
      json: async () => { throw new Error("no body"); },
    }));

    const result = await deleteExpense(42);

    expect(result).toBeNull();
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/expenses/42",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("throws an Error with status 404 when expense not found", async () => {
    mockFetch(async () => ({
      ok: false,
      status: 404,
      json: async () => ({ detail: "Expense not found" }),
    }));

    await expect(deleteExpense(99)).rejects.toMatchObject({
      message: "Expense not found",
      status: 404,
    });
  });

  it("throws an Error with status 409 when expense is receipt-backed", async () => {
    mockFetch(async () => ({
      ok: false,
      status: 409,
      json: async () => ({ detail: "Receipt-backed expenses must be deleted via DELETE /api/receipts/:id" }),
    }));

    await expect(deleteExpense(10)).rejects.toMatchObject({ status: 409 });
  });
});

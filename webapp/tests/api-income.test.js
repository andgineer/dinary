import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { listIncomes, createIncome, updateIncome, deleteIncome } from "../src/api/income.js";

let originalFetch;

function okJson(body = {}) {
  return {
    ok: true,
    status: 200,
    headers: { get: () => null },
    json: async () => body,
  };
}

beforeEach(() => {
  originalFetch = globalThis.fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("income API URLs", () => {
  it("listIncomes GETs /api/incomes with page params", async () => {
    globalThis.fetch = vi.fn(async () => okJson({ items: [], has_more: false }));
    await listIncomes({ page: 2, pageSize: 10 });
    expect(globalThis.fetch.mock.calls[0][0]).toBe("/api/incomes?page=2&page_size=10");
    expect(globalThis.fetch.mock.calls[0][1].method).toBe("GET");
  });

  it("listIncomes defaults to page=1 page_size=20", async () => {
    globalThis.fetch = vi.fn(async () => okJson({ items: [] }));
    await listIncomes();
    expect(globalThis.fetch.mock.calls[0][0]).toBe("/api/incomes?page=1&page_size=20");
  });

  it("createIncome POSTs to /api/incomes with body", async () => {
    globalThis.fetch = vi.fn(async () => ({ ...okJson({ year: 2026, month: 5, amount: 540 }), status: 201 }));
    await createIncome({ year: 2026, month: 5, amount_original: 540, currency_original: "EUR" });
    const [url, opts] = globalThis.fetch.mock.calls[0];
    expect(url).toBe("/api/incomes");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body)).toEqual({ year: 2026, month: 5, amount_original: 540, currency_original: "EUR" });
  });

  it("updateIncome PATCHes /api/incomes/{year}/{month} with body", async () => {
    globalThis.fetch = vi.fn(async () => okJson({ year: 2026, month: 5, amount: 600 }));
    await updateIncome(2026, 5, { amount_original: 600, currency_original: "EUR" });
    const [url, opts] = globalThis.fetch.mock.calls[0];
    expect(url).toBe("/api/incomes/2026/5");
    expect(opts.method).toBe("PATCH");
    expect(JSON.parse(opts.body)).toEqual({ amount_original: 600, currency_original: "EUR" });
  });

  it("deleteIncome DELETEs /api/incomes/{year}/{month}", async () => {
    globalThis.fetch = vi.fn(async () => ({ ok: true, status: 204, headers: { get: () => null } }));
    await deleteIncome(2026, 5);
    const [url, opts] = globalThis.fetch.mock.calls[0];
    expect(url).toBe("/api/incomes/2026/5");
    expect(opts.method).toBe("DELETE");
  });
});

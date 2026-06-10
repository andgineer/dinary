import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { postReceipt, getReceipt, deleteReceipt, getReceiptQueue, resolveReceipt } from "../src/api/receipts.js";

beforeEach(async () => {
  await allure.epic("Receipts");
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

describe("postReceipt", () => {
  it("POSTs client_receipt_id and url to /api/receipts and returns JSON", async () => {
    mockFetch(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ status: "ok", receipt_id: 7 }),
    }));

    const result = await postReceipt({
      client_receipt_id: "crid-abc",
      url: "https://suf.purs.gov.rs/v/?vl=AAAA",
    });

    expect(result).toEqual({ status: "ok", receipt_id: 7 });
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/receipts",
      expect.objectContaining({ method: "POST" }),
    );
    const body = JSON.parse(globalThis.fetch.mock.calls[0][1].body);
    expect(body).toEqual({
      client_receipt_id: "crid-abc",
      url: "https://suf.purs.gov.rs/v/?vl=AAAA",
    });
  });

  it("returns status=duplicate when the server echoes an existing receipt", async () => {
    mockFetch(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ status: "duplicate", receipt_id: 3 }),
    }));

    const result = await postReceipt({ client_receipt_id: "crid-dup", url: "https://x" });
    expect(result.status).toBe("duplicate");
  });

  it("throws an Error with status=409 on conflict", async () => {
    mockFetch(async () => ({
      ok: false,
      status: 409,
      json: async () => ({ detail: "already exists with different URL" }),
    }));

    await expect(
      postReceipt({ client_receipt_id: "crid-conflict", url: "https://new-url" }),
    ).rejects.toMatchObject({ message: "already exists with different URL", status: 409 });
  });

  it("throws with HTTP status message when error body has no detail", async () => {
    mockFetch(async () => ({
      ok: false,
      status: 500,
      json: async () => ({}),
    }));

    await expect(
      postReceipt({ client_receipt_id: "x", url: "https://x" }),
    ).rejects.toMatchObject({ message: "HTTP 500", status: 500 });
  });

  it("falls back to generic HTTP message when response body is not JSON", async () => {
    mockFetch(async () => ({
      ok: false,
      status: 503,
      json: async () => { throw new SyntaxError("not json"); },
    }));

    await expect(
      postReceipt({ client_receipt_id: "x", url: "https://x" }),
    ).rejects.toMatchObject({ message: "HTTP 503", status: 503 });
  });
});

describe("getReceipt", () => {
  it("GETs /api/receipts/:id without include param", async () => {
    mockFetch(async () => ({
      ok: true,
      status: 200,
      headers: { get: () => null },
      json: async () => ({ id: 3, merchant: "Maxi", captured_at: "2026-05-10T12:00:00" }),
    }));

    const result = await getReceipt(3);

    expect(result).toMatchObject({ id: 3, merchant: "Maxi" });
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/receipts/3",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("GETs /api/receipts/:id?include=expenses when include is provided", async () => {
    mockFetch(async () => ({
      ok: true,
      status: 200,
      headers: { get: () => null },
      json: async () => ({ id: 5, expenses: [] }),
    }));

    await getReceipt(5, { include: "expenses" });

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/receipts/5?include=expenses",
      expect.anything(),
    );
  });

  it("throws with status 404 when receipt not found", async () => {
    mockFetch(async () => ({
      ok: false,
      status: 404,
      json: async () => ({ detail: "Receipt not found" }),
    }));

    await expect(getReceipt(99)).rejects.toMatchObject({ status: 404 });
  });
});

describe("deleteReceipt", () => {
  it("sends DELETE to /api/receipts/:id and returns null on 204", async () => {
    mockFetch(async () => ({
      ok: true,
      status: 204,
      headers: { get: () => "0" },
      json: async () => { throw new Error("no body"); },
    }));

    const result = await deleteReceipt(7);

    expect(result).toBeNull();
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/receipts/7",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("throws with status 404 when receipt not found", async () => {
    mockFetch(async () => ({
      ok: false,
      status: 404,
      json: async () => ({ detail: "Receipt not found" }),
    }));

    await expect(deleteReceipt(99)).rejects.toMatchObject({ status: 404 });
  });
});

describe("getReceiptQueue", () => {
  it("GETs /api/receipts/queue with default pagination", async () => {
    mockFetch(async () => ({
      ok: true,
      status: 200,
      headers: { get: () => null },
      json: async () => ({ items: [], has_more: false }),
    }));

    const result = await getReceiptQueue();

    expect(result).toEqual({ items: [], has_more: false });
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/receipts/queue?page=1&page_size=20",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("GETs /api/receipts/queue with the given page and pageSize", async () => {
    mockFetch(async () => ({
      ok: true,
      status: 200,
      headers: { get: () => null },
      json: async () => ({ items: [], has_more: true }),
    }));

    await getReceiptQueue({ page: 2, pageSize: 5 });

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/receipts/queue?page=2&page_size=5",
      expect.anything(),
    );
  });
});

describe("resolveReceipt", () => {
  it("POSTs category_id, tag_ids, event_id and comment to /api/receipts/:id/resolve", async () => {
    mockFetch(async () => ({
      ok: true,
      status: 200,
      headers: { get: () => null },
      json: async () => ({ status: "ok", expense_id: 11 }),
    }));

    const result = await resolveReceipt(7, { categoryId: 3, tagIds: [1, 2], eventId: 5, comment: "manual" });

    expect(result).toEqual({ status: "ok", expense_id: 11 });
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/receipts/7/resolve",
      expect.objectContaining({ method: "POST" }),
    );
    const body = JSON.parse(globalThis.fetch.mock.calls[0][1].body);
    expect(body).toEqual({ category_id: 3, tag_ids: [1, 2], event_id: 5, comment: "manual" });
  });

  it("defaults tag_ids/event_id/comment when omitted", async () => {
    mockFetch(async () => ({
      ok: true,
      status: 200,
      headers: { get: () => null },
      json: async () => ({ status: "ok", expense_id: 12 }),
    }));

    await resolveReceipt(8, { categoryId: 4 });

    const body = JSON.parse(globalThis.fetch.mock.calls[0][1].body);
    expect(body).toEqual({ category_id: 4, tag_ids: [], event_id: null, comment: "" });
  });

  it("throws with status 409 when the receipt was already resolved", async () => {
    mockFetch(async () => ({
      ok: false,
      status: 409,
      json: async () => ({ detail: "Receipt already resolved" }),
    }));

    await expect(resolveReceipt(9, { categoryId: 1 })).rejects.toMatchObject({ status: 409 });
  });
});

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { postReceipt } from "../src/api/receipts.js";

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

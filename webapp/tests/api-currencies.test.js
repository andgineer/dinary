import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  fetchCurrencies,
  addCurrency,
  deleteCurrency,
} from "../src/api/currencies.js";

beforeEach(() => {
  globalThis.fetch = vi.fn();
});

afterEach(() => {
  vi.restoreAllMocks();
});

function okJson(body) {
  return { ok: true, status: 200, json: async () => body };
}

function errJson(status, body) {
  return { ok: false, status, json: async () => body };
}

describe("fetchCurrencies", () => {
  it("GETs /api/currencies and returns the parsed body", async () => {
    globalThis.fetch.mockResolvedValueOnce(
      okJson({ codes: ["RSD"], default_code: "RSD" }),
    );
    const body = await fetchCurrencies();
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/currencies",
      expect.objectContaining({ method: "GET" }),
    );
    expect(body).toEqual({ codes: ["RSD"], default_code: "RSD" });
  });

  it("throws an Error with detail when the server returns 5xx", async () => {
    globalThis.fetch.mockResolvedValueOnce(errJson(500, { detail: "kaput" }));
    await expect(fetchCurrencies()).rejects.toThrow("kaput");
  });
});

describe("addCurrency", () => {
  it("POSTs the uppercased code", async () => {
    globalThis.fetch.mockResolvedValueOnce(
      okJson({ codes: ["RSD", "USD"], default_code: "RSD" }),
    );
    await addCurrency("usd");
    const [, init] = globalThis.fetch.mock.calls[0];
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ code: "USD" }));
  });

  it("rejects empty / non-string codes before calling fetch", async () => {
    await expect(addCurrency("")).rejects.toThrow();
    await expect(addCurrency(null)).rejects.toThrow();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});

describe("deleteCurrency", () => {
  it("DELETEs the URL-encoded uppercased code", async () => {
    globalThis.fetch.mockResolvedValueOnce(
      okJson({ codes: ["RSD"], default_code: "RSD" }),
    );
    await deleteCurrency("usd");
    const [url, init] = globalThis.fetch.mock.calls[0];
    expect(url).toBe("/api/currencies/USD");
    expect(init.method).toBe("DELETE");
  });

  it("propagates the 409 error from the server", async () => {
    globalThis.fetch.mockResolvedValueOnce(
      errJson(409, { detail: "Cannot delete the default currency 'RSD'" }),
    );
    await expect(deleteCurrency("RSD")).rejects.toThrow(
      /Cannot delete the default currency/,
    );
  });
});

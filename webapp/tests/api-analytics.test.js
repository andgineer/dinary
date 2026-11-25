import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { fetchTrendDetail } from "../src/api/analytics.js";

beforeEach(async () => {
  await allure.epic("Analytics");
  await allure.feature("API client");
});

let originalFetch;

beforeEach(() => {
  originalFetch = globalThis.fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("analytics API: trend detail", () => {
  it("GETs the basket by kind and id and returns the body", async () => {
    globalThis.fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ id: 3 }) }));
    expect(await fetchTrendDetail("tag", 3)).toEqual({ id: 3 });
    expect(globalThis.fetch).toHaveBeenCalledWith("/api/analytics/trends/tag/3");
  });

  it("throws on a non-OK response", async () => {
    globalThis.fetch = vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}) }));
    await expect(fetchTrendDetail("group", 9)).rejects.toThrow("analytics trend 404");
  });
});

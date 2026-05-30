import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { getReviewFeed, confirmAllRules } from "../src/api/review.js";

beforeEach(async () => {
  await allure.epic("Review & Rules");
  await allure.feature("API client");
});

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

describe("review API URLs", () => {
  it("getReviewFeed GETs /api/rules/feed with page params and doubtful_only", async () => {
    globalThis.fetch = vi.fn(async () => okJson({ items: [], has_more: false }));
    await getReviewFeed({ page: 2, pageSize: 10 });
    const url = globalThis.fetch.mock.calls[0][0];
    expect(url).toBe("/api/rules/feed?page=2&page_size=10&doubtful_only=true");
    expect(globalThis.fetch.mock.calls[0][1].method).toBe("GET");
  });

  it("getReviewFeed defaults to page=1 page_size=20 doubtful_only=true", async () => {
    globalThis.fetch = vi.fn(async () => okJson({ items: [] }));
    await getReviewFeed();
    expect(globalThis.fetch.mock.calls[0][0]).toBe("/api/rules/feed?page=1&page_size=20&doubtful_only=true");
  });

  it("confirmAllRules POSTs to /api/rules/confirm-all with rule_ids", async () => {
    globalThis.fetch = vi.fn(async () => okJson({ confirmed: 3 }));
    await confirmAllRules([1, 2, 3]);
    const [url, opts] = globalThis.fetch.mock.calls[0];
    expect(url).toBe("/api/rules/confirm-all");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body)).toEqual({ rule_ids: [1, 2, 3] });
  });
});

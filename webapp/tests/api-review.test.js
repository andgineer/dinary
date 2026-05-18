import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { getReviewFeed, getReviewCounts } from "../src/api/review.js";

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
  it("getReviewFeed GETs /api/rules/feed with page params", async () => {
    globalThis.fetch = vi.fn(async () => okJson({ items: [], has_more: false }));
    await getReviewFeed({ page: 2, pageSize: 10 });
    const url = globalThis.fetch.mock.calls[0][0];
    expect(url).toBe("/api/rules/feed?page=2&page_size=10");
    expect(globalThis.fetch.mock.calls[0][1].method).toBe("GET");
  });

  it("getReviewFeed defaults to page=1 page_size=20", async () => {
    globalThis.fetch = vi.fn(async () => okJson({ items: [] }));
    await getReviewFeed();
    expect(globalThis.fetch.mock.calls[0][0]).toBe("/api/rules/feed?page=1&page_size=20");
  });

  it("getReviewCounts GETs /api/rules/counts", async () => {
    globalThis.fetch = vi.fn(async () => okJson({ doubtful_rules: 3 }));
    await getReviewCounts();
    expect(globalThis.fetch.mock.calls[0][0]).toBe("/api/rules/counts");
    expect(globalThis.fetch.mock.calls[0][1].method).toBe("GET");
  });
});

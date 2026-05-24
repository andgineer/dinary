import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  createProvider,
  updateProvider,
  deleteProvider,
  getStatus,
} from "../src/api/adminLlm.js";

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

describe("LLM provider API URLs", () => {
  it("createProvider POSTs to /api/llm/providers", async () => {
    globalThis.fetch = vi.fn(async () => okJson({ id: 1 }));
    await createProvider({ label: "x", base_url: "u", api_key: "k", model: "m" });
    expect(globalThis.fetch.mock.calls[0][0]).toBe("/api/llm/providers");
    expect(globalThis.fetch.mock.calls[0][1].method).toBe("POST");
  });

  it("updateProvider PATCHes /api/llm/providers/{id}", async () => {
    globalThis.fetch = vi.fn(async () => okJson());
    await updateProvider(7, { label: "y" });
    expect(globalThis.fetch.mock.calls[0][0]).toBe("/api/llm/providers/7");
    expect(globalThis.fetch.mock.calls[0][1].method).toBe("PATCH");
  });

  it("deleteProvider DELETEs /api/llm/providers/{id}", async () => {
    globalThis.fetch = vi.fn(async () => okJson());
    await deleteProvider(3);
    expect(globalThis.fetch.mock.calls[0][0]).toBe("/api/llm/providers/3");
    expect(globalThis.fetch.mock.calls[0][1].method).toBe("DELETE");
  });

  it("getStatus GETs /api/llm/status", async () => {
    globalThis.fetch = vi.fn(async () => okJson({ health: {} }));
    await getStatus();
    expect(globalThis.fetch.mock.calls[0][0]).toBe("/api/llm/status");
    expect(globalThis.fetch.mock.calls[0][1].method).toBe("GET");
  });
});

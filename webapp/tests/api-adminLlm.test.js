import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { getStatus, disableProvider, enableProvider } from "../src/api/adminLlm.js";

beforeEach(async () => {
  await allure.epic("Infrastructure");
  await allure.feature("LLM providers");
  await allure.story("API client");
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

describe("LLM provider API URLs", () => {
  it("getStatus GETs /api/llm/status", async () => {
    globalThis.fetch = vi.fn(async () => okJson({ health: {} }));
    await getStatus();
    expect(globalThis.fetch.mock.calls[0][0]).toBe("/api/llm/status");
    expect(globalThis.fetch.mock.calls[0][1].method).toBe("GET");
  });

  it("disableProvider POSTs to /api/llm/providers/{name}/disable", async () => {
    globalThis.fetch = vi.fn(async () => okJson());
    await disableProvider("groq-llama");
    expect(globalThis.fetch.mock.calls[0][0]).toBe("/api/llm/providers/groq-llama/disable");
    expect(globalThis.fetch.mock.calls[0][1].method).toBe("POST");
  });

  it("enableProvider POSTs to /api/llm/providers/{name}/enable", async () => {
    globalThis.fetch = vi.fn(async () => okJson());
    await enableProvider("groq-llama");
    expect(globalThis.fetch.mock.calls[0][0]).toBe("/api/llm/providers/groq-llama/enable");
    expect(globalThis.fetch.mock.calls[0][1].method).toBe("POST");
  });

  it("encodes provider names with special characters", async () => {
    globalThis.fetch = vi.fn(async () => okJson());
    await disableProvider("a/b name");
    expect(globalThis.fetch.mock.calls[0][0]).toBe("/api/llm/providers/a%2Fb%20name/disable");
  });

  it("createProvider/updateProvider/deleteProvider are no longer exported", async () => {
    const mod = await import("../src/api/adminLlm.js");
    expect(mod.createProvider).toBeUndefined();
    expect(mod.updateProvider).toBeUndefined();
    expect(mod.deleteProvider).toBeUndefined();
  });
});

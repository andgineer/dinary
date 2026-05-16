import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useLlmStore } from "../src/stores/llm.js";
import * as llmApi from "../src/api/adminLlm.js";

function mockOnLine(value) {
  const ownBefore = Object.getOwnPropertyDescriptor(navigator, "onLine");
  Object.defineProperty(navigator, "onLine", { configurable: true, get: () => value });
  return () => {
    if (ownBefore) {
      Object.defineProperty(navigator, "onLine", ownBefore);
    } else {
      delete navigator.onLine;
    }
  };
}

const SAMPLE_STATUS = {
  health: { healthy: 1, total: 1, strategy: null, last_switch: null },
  providers: [
    {
      id: 1,
      label: "Groq",
      base_url: "https://api.groq.com/openai/v1",
      model: "llama-3.3-70b-versatile",
      priority: 0,
      is_enabled: true,
      rate_limited_until: null,
      created_at: "2026-05-10T11:30:00+00:00",
      used_today: 7,
      ok_calls: 7,
      last_status: "ok",
    },
  ],
  meta: { llm_last_provider_idx: "0" },
};

beforeEach(() => {
  setActivePinia(createPinia());
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("llm store: refresh() offline", () => {
  it("suppresses error toast when offline and API fails", async () => {
    vi.spyOn(llmApi, "getStatus").mockRejectedValueOnce(new Error("Network error"));
    const restore = mockOnLine(false);
    try {
      const store = useLlmStore();
      const { useToastStore } = await import("../src/stores/toast.js");
      const toast = useToastStore();
      const showSpy = vi.spyOn(toast, "show");
      await store.refresh();
      expect(showSpy).not.toHaveBeenCalled();
    } finally {
      restore();
    }
  });

  it("shows error toast when online and API fails", async () => {
    vi.spyOn(llmApi, "getStatus").mockRejectedValueOnce(new Error("LLM error"));
    const store = useLlmStore();
    const { useToastStore } = await import("../src/stores/toast.js");
    const toast = useToastStore();
    const showSpy = vi.spyOn(toast, "show");
    await store.refresh();
    expect(showSpy).toHaveBeenCalledWith(expect.stringContaining("LLM error"), "error");
  });
});

describe("llm store: refresh()", () => {
  it("sets providers from status.providers", async () => {
    vi.spyOn(llmApi, "getStatus").mockResolvedValueOnce(SAMPLE_STATUS);
    const store = useLlmStore();
    await store.refresh();
    expect(store.providers).toHaveLength(1);
    expect(store.providers[0].label).toBe("Groq");
  });

  it("sets health from status.health", async () => {
    vi.spyOn(llmApi, "getStatus").mockResolvedValueOnce(SAMPLE_STATUS);
    const store = useLlmStore();
    await store.refresh();
    expect(store.health).toEqual(SAMPLE_STATUS.health);
  });

  it("makes exactly one API call (no listProviders)", async () => {
    const spy = vi.spyOn(llmApi, "getStatus").mockResolvedValueOnce(SAMPLE_STATUS);
    const store = useLlmStore();
    await store.refresh();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("listProviders is not exported from adminLlm", () => {
    expect(llmApi.listProviders).toBeUndefined();
  });
});

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useLlmStore } from "../src/stores/llm.js";
import * as llmApi from "../src/api/adminLlm.js";

beforeEach(async () => {
  await allure.epic("Infrastructure");
  await allure.feature("LLM providers");
  await allure.story("LLM store");
});

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
  pending_receipts: 0,
};

beforeEach(() => {
  localStorage.clear();
  setActivePinia(createPinia());
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
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

describe("llm store: markDirty()", () => {
  it("sets dirtyFlag to true and persists to localStorage", () => {
    const store = useLlmStore();
    expect(store.dirtyFlag).toBe(false);
    store.markDirty();
    expect(store.dirtyFlag).toBe(true);
    expect(localStorage.getItem("dinary:llm:dirty")).toBe("1");
  });
});

describe("llm store: loadIfNeeded()", () => {
  it("fetches when dirtyFlag is set", async () => {
    const spy = vi.spyOn(llmApi, "getStatus").mockResolvedValue(SAMPLE_STATUS);
    const store = useLlmStore();
    store.markDirty();
    await store.loadIfNeeded();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("fetches when no lastFetchedAt (never loaded)", async () => {
    const spy = vi.spyOn(llmApi, "getStatus").mockResolvedValue(SAMPLE_STATUS);
    const store = useLlmStore();
    expect(store.lastFetchedAt).toBeNull();
    await store.loadIfNeeded();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("fetches when data is older than 24h", async () => {
    const old = Date.now() - 25 * 60 * 60 * 1000;
    localStorage.setItem("dinary:llm:fetchedAt", String(old));
    const spy = vi.spyOn(llmApi, "getStatus").mockResolvedValue(SAMPLE_STATUS);
    setActivePinia(createPinia());
    const store = useLlmStore();
    await store.loadIfNeeded();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("skips fetch when clean and data is recent", async () => {
    localStorage.setItem("dinary:llm:fetchedAt", String(Date.now() - 60_000));
    const spy = vi.spyOn(llmApi, "getStatus").mockResolvedValue(SAMPLE_STATUS);
    setActivePinia(createPinia());
    const store = useLlmStore();
    expect(store.dirtyFlag).toBe(false);
    await store.loadIfNeeded();
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("llm store: refresh() clears dirty flag when pending_receipts is 0", () => {
  it("clears dirtyFlag and localStorage when pending_receipts === 0", async () => {
    vi.spyOn(llmApi, "getStatus").mockResolvedValue({ ...SAMPLE_STATUS, pending_receipts: 0 });
    const store = useLlmStore();
    store.markDirty();
    await store.refresh();
    expect(store.dirtyFlag).toBe(false);
    expect(localStorage.getItem("dinary:llm:dirty")).toBeNull();
  });

  it("keeps dirtyFlag when pending_receipts > 0", async () => {
    vi.spyOn(llmApi, "getStatus").mockResolvedValue({ ...SAMPLE_STATUS, pending_receipts: 3 });
    const store = useLlmStore();
    store.markDirty();
    await store.refresh();
    expect(store.dirtyFlag).toBe(true);
  });

  it("sets lastFetchedAt after successful refresh", async () => {
    vi.spyOn(llmApi, "getStatus").mockResolvedValue(SAMPLE_STATUS);
    const store = useLlmStore();
    const before = Date.now();
    await store.refresh();
    expect(store.lastFetchedAt).toBeGreaterThanOrEqual(before);
  });
});

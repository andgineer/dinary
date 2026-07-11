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
  health: { healthy: 1, total: 2, strategy: "failover" },
  providers: [
    {
      name: "groq-llama",
      model: "llama-3.3-70b-versatile",
      base_url: "https://api.groq.com/openai/v1",
      disabled: false,
      has_key: true,
      cooldown_until: null,
      status: "available",
      call_count: 7,
      last_status: "ok",
      last_at: "2026-05-10T11:30:00+00:00",
      demoted: false,
      quality_bound: null,
      help: null,
    },
    {
      name: "openrouter",
      model: "gpt-oss-120b",
      base_url: "https://openrouter.ai/api/v1",
      disabled: false,
      has_key: false,
      cooldown_until: null,
      status: "no_key",
      call_count: 0,
      last_status: null,
      last_at: null,
      demoted: false,
      quality_bound: null,
      help: "Create a key at openrouter.ai/keys.",
    },
  ],
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
    expect(store.providers).toHaveLength(2);
    expect(store.providers[0].name).toBe("groq-llama");
  });

  it("sets health from status.health", async () => {
    vi.spyOn(llmApi, "getStatus").mockResolvedValueOnce(SAMPLE_STATUS);
    const store = useLlmStore();
    await store.refresh();
    expect(store.health).toEqual(SAMPLE_STATUS.health);
  });

  it("makes exactly one API call", async () => {
    const spy = vi.spyOn(llmApi, "getStatus").mockResolvedValueOnce(SAMPLE_STATUS);
    const store = useLlmStore();
    await store.refresh();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("no provider-CRUD methods are exposed", () => {
    const store = useLlmStore();
    expect(store.save).toBeUndefined();
    expect(store.remove).toBeUndefined();
    expect(store.move).toBeUndefined();
  });
});

describe("llm store: toggleDisabled()", () => {
  it("disables an enabled provider and refreshes", async () => {
    vi.spyOn(llmApi, "getStatus").mockResolvedValue(SAMPLE_STATUS);
    const disableSpy = vi.spyOn(llmApi, "disableProvider").mockResolvedValue({});
    const store = useLlmStore();
    await store.refresh();
    await store.toggleDisabled("groq-llama");
    expect(disableSpy).toHaveBeenCalledWith("groq-llama");
  });

  it("enables a disabled provider", async () => {
    const disabledStatus = {
      ...SAMPLE_STATUS,
      providers: [{ ...SAMPLE_STATUS.providers[0], disabled: true, status: "disabled" }],
    };
    vi.spyOn(llmApi, "getStatus").mockResolvedValue(disabledStatus);
    const enableSpy = vi.spyOn(llmApi, "enableProvider").mockResolvedValue({});
    const store = useLlmStore();
    await store.refresh();
    await store.toggleDisabled("groq-llama");
    expect(enableSpy).toHaveBeenCalledWith("groq-llama");
  });

  it("reverts optimistic flip and toasts on failure", async () => {
    vi.spyOn(llmApi, "getStatus").mockResolvedValue(SAMPLE_STATUS);
    vi.spyOn(llmApi, "disableProvider").mockRejectedValue(new Error("boom"));
    const store = useLlmStore();
    await store.refresh();
    const { useToastStore } = await import("../src/stores/toast.js");
    const showSpy = vi.spyOn(useToastStore(), "show");
    await store.toggleDisabled("groq-llama");
    expect(store.providers.find((p) => p.name === "groq-llama").disabled).toBe(false);
    expect(showSpy).toHaveBeenCalled();
  });

  it("is a no-op for an unknown provider name", async () => {
    const disableSpy = vi.spyOn(llmApi, "disableProvider").mockResolvedValue({});
    const store = useLlmStore();
    await store.toggleDisabled("ghost");
    expect(disableSpy).not.toHaveBeenCalled();
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

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import LLMView from "../src/views/LLMView.vue";
import * as llmApi from "../src/api/adminLlm.js";

beforeEach(async () => {
  await allure.epic("Infrastructure");
  await allure.feature("LLM providers");
  await allure.story("LLMView");
});

vi.mock("../src/api/adminLlm.js", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    getStatus: vi.fn(async () => ({ health: null, providers: [] })),
  };
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

function provider(overrides) {
  return {
    name: "groq-llama",
    model: "llama-3.3-70b-versatile",
    base_url: "https://api.groq.com/openai/v1",
    disabled: false,
    has_key: true,
    cooldown_until: null,
    status: "available",
    recent_calls: 12,
    recent_failures: 0,
    recent_window_days: 30,
    demoted: false,
    quality_bound: null,
    help: null,
    ...overrides,
  };
}

function mountView() {
  const pinia = createPinia();
  setActivePinia(pinia);
  return mount(LLMView, { global: { plugins: [pinia] } });
}

let restoreOnLine = null;

beforeEach(() => {
  localStorage.clear();
  restoreOnLine = mockOnLine(true);
});

afterEach(() => {
  restoreOnLine?.();
  localStorage.clear();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("LLMView — pool header", () => {
  it("names no pool source in the header", async () => {
    llmApi.getStatus.mockResolvedValue({
      health: { healthy: 1, total: 1, strategy: null },
      providers: [provider()],
    });

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find(".pool-header").text()).toBe("PROVIDER POOL");
    expect(wrapper.find(".pool-hint").exists()).toBe(false);
    expect(wrapper.text()).not.toContain("curated");

    wrapper.unmount();
  });

  it("still says where the pool comes from when it is empty", async () => {
    llmApi.getStatus.mockResolvedValue({
      health: { healthy: 0, total: 0, strategy: null },
      providers: [],
    });

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find(".empty-state").text()).toContain("model list");

    wrapper.unmount();
  });
});

describe("LLMView — refetch while a provider is cooling", () => {
  it("refetches on the next tick once a cooldown deadline has passed", async () => {
    vi.useFakeTimers();
    llmApi.getStatus.mockResolvedValue({
      health: { healthy: 0, total: 1, strategy: null },
      providers: [
        provider({
          status: "cooling",
          cooldown_until: new Date(Date.now() - 60_000).toISOString(),
        }),
      ],
    });

    const wrapper = mountView();
    await flushPromises();
    expect(llmApi.getStatus).toHaveBeenCalledTimes(1);

    // refresh() stamped the cache fresh, so the dirty flag alone would never fire.
    llmApi.getStatus.mockClear();
    vi.advanceTimersByTime(30_000);
    await flushPromises();

    expect(llmApi.getStatus).toHaveBeenCalledTimes(1);

    wrapper.unmount();
  });

  it("does not refetch while the cooldown still has time to run", async () => {
    vi.useFakeTimers();
    llmApi.getStatus.mockResolvedValue({
      health: { healthy: 0, total: 1, strategy: null },
      providers: [
        provider({
          status: "cooling",
          cooldown_until: new Date(Date.now() + 60 * 60_000).toISOString(),
        }),
      ],
    });

    const wrapper = mountView();
    await flushPromises();
    llmApi.getStatus.mockClear();

    vi.advanceTimersByTime(30_000);
    await flushPromises();

    expect(llmApi.getStatus).not.toHaveBeenCalled();

    wrapper.unmount();
  });

  it("does not refetch an available pool", async () => {
    vi.useFakeTimers();
    llmApi.getStatus.mockResolvedValue({
      health: { healthy: 1, total: 1, strategy: null },
      providers: [provider()],
    });

    const wrapper = mountView();
    await flushPromises();
    llmApi.getStatus.mockClear();

    vi.advanceTimersByTime(30_000);
    await flushPromises();

    expect(llmApi.getStatus).not.toHaveBeenCalled();

    wrapper.unmount();
  });
});

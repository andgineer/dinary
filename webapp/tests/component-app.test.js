import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia } from "pinia";
import App from "../src/App.vue";
import * as catalogApi from "../src/api/catalog.js";
import { _resetForTest } from "../src/stores/queue.js";

async function resetQueueDb() {
  await _resetForTest();
  await new Promise((resolve) => {
    const del = indexedDB.deleteDatabase("dinary-v2");
    del.onsuccess = del.onerror = del.onblocked = () => resolve();
    setTimeout(resolve, 1000);
  });
}

let originalFetch;

beforeEach(async () => {
  await resetQueueDb();
  originalFetch = globalThis.fetch;
  globalThis.fetch = vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => ({ version: "test" }),
  }));
  vi.spyOn(catalogApi, "fetchCatalog").mockResolvedValue({
    catalog_version: 0,
    category_groups: [],
    categories: [],
    events: [],
    tags: [],
  });
});

afterEach(async () => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
  await resetQueueDb();
});

describe("App shell", () => {
  it("renders header, expense form, action bar, and toast region", async () => {
    const wrapper = mount(App, { global: { plugins: [createPinia()] } });
    await flushPromises();
    expect(wrapper.find(".app-header").exists()).toBe(true);
    expect(wrapper.find('[data-testid="expense-form"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="qr-btn"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="save-btn"]').exists()).toBe(true);
    expect(wrapper.find('.toast').exists()).toBe(true);
  });

  it("hides the queue badge when the queue is empty", async () => {
    const wrapper = mount(App, { global: { plugins: [createPinia()] } });
    await flushPromises();
    expect(wrapper.find('[data-testid="queue-badge"]').exists()).toBe(false);
  });

  it("shows the offline hint when navigator.onLine is false", async () => {
    const original = Object.getOwnPropertyDescriptor(
      Object.getPrototypeOf(navigator),
      "onLine",
    );
    Object.defineProperty(navigator, "onLine", { configurable: true, get: () => false });
    try {
      const wrapper = mount(App, { global: { plugins: [createPinia()] } });
      await flushPromises();
      expect(wrapper.text()).toContain("Offline");
    } finally {
      if (original) {
        Object.defineProperty(Object.getPrototypeOf(navigator), "onLine", original);
      } else {
        Object.defineProperty(navigator, "onLine", { configurable: true, get: () => true });
      }
    }
  });

  it("opens the queue modal when the badge is clicked", async () => {
    // Seed an item directly via the queue store before mount so the badge appears.
    const { useQueueStore } = await import("../src/stores/queue.js");
    const pinia = createPinia();
    const wrapper = mount(App, { global: { plugins: [pinia] } });
    const queue = useQueueStore(pinia);
    await queue.enqueue({
      amount: 5,
      currency: "RSD",
      category_id: 1,
      category_name: "x",
      date: "2026-05-04",
    });
    await flushPromises();
    expect(wrapper.find('[data-testid="queue-badge"]').exists()).toBe(true);
    await wrapper.find('[data-testid="queue-badge"]').trigger("click");
    await flushPromises();
    expect(wrapper.find(".modal").exists()).toBe(true);
  });
});

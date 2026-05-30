import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia } from "pinia";
import App from "../src/App.vue";
import * as catalogApi from "../src/api/catalog.js";
import * as flushReceiptQueueModule from "../src/composables/flushReceiptQueue.js";
import { _resetForTest as resetFlushReceiptQueue } from "../src/composables/flushReceiptQueue.js";
import { _resetForTest } from "../src/stores/queue.js";
import {
  _resetForTest as resetReceiptQueueHandle,
  useReceiptQueueStore,
} from "../src/stores/receiptQueue.js";


beforeEach(async () => {
  await allure.epic("Infrastructure");
  await allure.feature("Frontend");
  await allure.story("App");
});

async function resetQueueDb() {
  await _resetForTest();
  await new Promise((resolve) => {
    const del = indexedDB.deleteDatabase("dinary-v2");
    del.onsuccess = del.onerror = del.onblocked = () => resolve();
    setTimeout(resolve, 1000);
  });
}

async function resetReceiptDb() {
  await resetReceiptQueueHandle();
  await new Promise((resolve) => {
    const del = indexedDB.deleteDatabase("dinary-receipts");
    del.onsuccess = del.onerror = del.onblocked = () => resolve();
    setTimeout(resolve, 200);
  });
}

// fake-indexeddb uses setImmediate for each IDB task; one flushPromises() call schedules
// its own setImmediate BEFORE those tasks are queued, so it resolves too early.
// Drain multiple rounds to let the full IDB → promise chain complete.
async function drainAsync(rounds = 20) {
  for (let i = 0; i < rounds; i++) await flushPromises();
}

let _origFetch;

beforeEach(async () => {
  await resetQueueDb();
  _origFetch = globalThis.fetch;
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
  globalThis.fetch = _origFetch;
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

describe("App onScan — receipt flow", () => {
  it("queues receipt and shows 'Receipt queued' toast without prefilling the expense form", async () => {
    vi.spyOn(flushReceiptQueueModule, "flushReceiptQueue").mockResolvedValue();

    const { useReceiptQueueStore } = await import("../src/stores/receiptQueue.js");
    const pinia = createPinia();
    const wrapper = mount(App, { global: { plugins: [pinia] } });
    await flushPromises();

    const receiptStore = useReceiptQueueStore(pinia);
    const enqueueSpy = vi.spyOn(receiptStore, "enqueue").mockResolvedValue();

    const scanner = wrapper.findComponent({ name: "QrScanner" });
    await scanner.vm.$emit("scan", "https://suf.purs.gov.rs/v/?vl=TESTRECEIPT");
    await flushPromises();

    expect(enqueueSpy).toHaveBeenCalledWith("https://suf.purs.gov.rs/v/?vl=TESTRECEIPT");
    expect(wrapper.text()).toContain("Receipt queued");
    // Expense form amount must remain empty — receipt data must NOT be prefilled.
    expect(wrapper.find("#amount").element.value).toBe("");
  });

  it("does not dispatch dinary:receipt-parsed when scanning a valid receipt", async () => {
    vi.spyOn(flushReceiptQueueModule, "flushReceiptQueue").mockResolvedValue();

    const { useReceiptQueueStore } = await import("../src/stores/receiptQueue.js");
    const pinia = createPinia();
    const wrapper = mount(App, { global: { plugins: [pinia] } });
    await flushPromises();

    const receiptStore = useReceiptQueueStore(pinia);
    vi.spyOn(receiptStore, "enqueue").mockResolvedValue();

    const received = [];
    window.addEventListener("dinary:receipt-parsed", (e) => received.push(e));

    const scanner = wrapper.findComponent({ name: "QrScanner" });
    await scanner.vm.$emit("scan", "https://suf.purs.gov.rs/v/?vl=TESTRECEIPT");
    await flushPromises();

    expect(received).toHaveLength(0);
    window.removeEventListener("dinary:receipt-parsed", (e) => received.push(e));
  });
});

describe("App flush triggers — receipt queue", () => {
  beforeEach(async () => {
    resetFlushReceiptQueue();
    await resetReceiptDb();
  });

  afterEach(async () => {
    await resetReceiptDb();
  });

  it("startup: mounts with queued receipt and is online → receipt delivered", async () => {
    // happy-dom defaults navigator.onLine to false; force it true so init() flushes.
    const onlineDesc = Object.getOwnPropertyDescriptor(
      Object.getPrototypeOf(navigator),
      "onLine",
    );
    Object.defineProperty(navigator, "onLine", { configurable: true, get: () => true });
    try {
      const pinia = createPinia();
      const receiptStore = useReceiptQueueStore(pinia);
      await receiptStore.enqueue("https://suf.purs.gov.rs/v/?vl=STARTUP1");

      mount(App, { global: { plugins: [pinia] } });
      await drainAsync();

      expect(receiptStore.items).toHaveLength(0);
    } finally {
      if (onlineDesc) {
        Object.defineProperty(Object.getPrototypeOf(navigator), "onLine", onlineDesc);
      } else {
        Object.defineProperty(navigator, "onLine", { configurable: true, get: () => false });
      }
    }
  });

  it("online event → queued receipt delivered on reconnect", async () => {
    const onlineDesc = Object.getOwnPropertyDescriptor(
      Object.getPrototypeOf(navigator),
      "onLine",
    );
    Object.defineProperty(navigator, "onLine", { configurable: true, get: () => false });
    try {
      const pinia = createPinia();
      mount(App, { global: { plugins: [pinia] } });
      await drainAsync();

      const receiptStore = useReceiptQueueStore(pinia);
      await receiptStore.enqueue("https://suf.purs.gov.rs/v/?vl=ONLINE1");
      expect(receiptStore.items).toHaveLength(1);

      Object.defineProperty(navigator, "onLine", { configurable: true, get: () => true });
      window.dispatchEvent(new Event("online"));
      await drainAsync();

      expect(receiptStore.items).toHaveLength(0);
    } finally {
      if (onlineDesc) {
        Object.defineProperty(Object.getPrototypeOf(navigator), "onLine", onlineDesc);
      } else {
        Object.defineProperty(navigator, "onLine", { configurable: true, get: () => true });
      }
    }
  });

  it("real enqueue: scan with no enqueue spy → enqueue called with scanned URL", async () => {
    const onlineDesc = Object.getOwnPropertyDescriptor(
      Object.getPrototypeOf(navigator),
      "onLine",
    );
    Object.defineProperty(navigator, "onLine", { configurable: true, get: () => false });
    try {
      const pinia = createPinia();
      const wrapper = mount(App, { global: { plugins: [pinia] } });
      await drainAsync();

      const receiptStore = useReceiptQueueStore(pinia);
      const enqueueSpy = vi.spyOn(receiptStore, "enqueue");

      const scanner = wrapper.findComponent({ name: "QrScanner" });
      await scanner.vm.$emit("scan", "https://suf.purs.gov.rs/v/?vl=REALENQUEUE");
      await flushPromises();

      expect(enqueueSpy).toHaveBeenCalledWith("https://suf.purs.gov.rs/v/?vl=REALENQUEUE");
    } finally {
      if (onlineDesc) {
        Object.defineProperty(Object.getPrototypeOf(navigator), "onLine", onlineDesc);
      } else {
        Object.defineProperty(navigator, "onLine", { configurable: true, get: () => true });
      }
    }
  });
});

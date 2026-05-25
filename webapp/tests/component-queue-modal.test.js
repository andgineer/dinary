import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import QueueModal from "../src/components/QueueModal.vue";
import { useQueueStore, _resetForTest } from "../src/stores/queue.js";
import {
  useReceiptQueueStore,
  _resetForTest as resetReceiptQueueStore,
} from "../src/stores/receiptQueue.js";

beforeEach(async () => {
  await allure.epic("Expenses");
  await allure.feature("Frontend");
  await allure.story("QueueModal");
});

async function resetQueueDb() {
  await _resetForTest();
  await resetReceiptQueueStore();
  await Promise.all([
    new Promise((resolve) => {
      const del = indexedDB.deleteDatabase("dinary-v2");
      del.onsuccess = del.onerror = del.onblocked = () => resolve();
      setTimeout(resolve, 1000);
    }),
    new Promise((resolve) => {
      const del = indexedDB.deleteDatabase("dinary-receipts");
      del.onsuccess = del.onerror = del.onblocked = () => resolve();
      setTimeout(resolve, 1000);
    }),
  ]);
}

let originalFetch;

beforeEach(async () => {
  setActivePinia(createPinia());
  await resetQueueDb();
  originalFetch = globalThis.fetch;
  globalThis.fetch = vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => ({ version: "test" }),
  }));
});

afterEach(async () => {
  globalThis.fetch = originalFetch;
  await resetQueueDb();
});

describe("QueueModal", () => {
  it("renders nothing when open=false", () => {
    const wrapper = mount(QueueModal, { props: { open: false } });
    expect(wrapper.find(".modal").exists()).toBe(false);
  });

  it("renders an empty state when there are no queued items", async () => {
    const wrapper = mount(QueueModal, { props: { open: true } });
    await flushPromises();
    expect(wrapper.text()).toContain("Queue is empty");
  });

  it("renders one row per queued item", async () => {
    const queue = useQueueStore();
    await queue.enqueue({
      amount: 100,
      currency: "RSD",
      category_id: 7,
      category_name: "cafe",
      date: "2026-05-04",
      comment: "lunch",
    });
    const wrapper = mount(QueueModal, { props: { open: true } });
    await flushPromises();
    expect(wrapper.findAll('[data-testid="queue-item"]')).toHaveLength(1);
    expect(wrapper.text()).toContain("100");
    expect(wrapper.text()).toContain("cafe");
    expect(wrapper.text()).toContain("lunch");
    expect(wrapper.text()).toContain("2026-05-04");
  });

  it("renders receipt queue items alongside expense items", async () => {
    const receiptQueue = useReceiptQueueStore();
    await receiptQueue.enqueue("https://suf.purs.gov.rs/v/?vl=TESTRECEIPT");
    const wrapper = mount(QueueModal, { props: { open: true } });
    await flushPromises();
    expect(wrapper.findAll('[data-testid="queue-item"]')).toHaveLength(1);
    expect(wrapper.text()).toContain("QR receipt");
    expect(wrapper.text()).toContain("suf.purs.gov.rs");
  });

  it("emits 'close' when the header × is clicked", async () => {
    const wrapper = mount(QueueModal, { props: { open: true } });
    await flushPromises();
    await wrapper.find(".modal-close").trigger("click");
    expect(wrapper.emitted("close")).toBeTruthy();
  });

  it("emits 'close' when the backdrop is clicked but not when content is clicked", async () => {
    const wrapper = mount(QueueModal, { props: { open: true } });
    await flushPromises();
    await wrapper.find(".modal").trigger("click");
    expect(wrapper.emitted("close")).toBeTruthy();
    wrapper.emitted("close").length = 0;
    await wrapper.find(".modal-content").trigger("click");
    // .self modifier blocks bubbling; no new close event from content click.
    expect(wrapper.emitted("close")?.length ?? 0).toBe(0);
  });
});

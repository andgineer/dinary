import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import QueueModal from "../src/components/QueueModal.vue";
import { useQueueStore, _resetForTest } from "../src/stores/queue.js";
import {
  useReceiptQueueStore,
  _resetForTest as resetReceiptQueueStore,
} from "../src/stores/receiptQueue.js";

vi.mock("../src/api/_request.js", () => ({
  apiRequest: vi.fn(async () => ({ version: "test" })),
}));

import { apiRequest } from "../src/api/_request.js";
import { ServerUnreachable } from "../src/api/serverReach.js";

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

beforeEach(async () => {
  setActivePinia(createPinia());
  await resetQueueDb();
});

afterEach(async () => {
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

  it("renders receipt queue items with parsed amount and date", async () => {
    function buildVlPayload(amountUnits, ms) {
      const buf = new Uint8Array(64);
      const view = new DataView(buf.buffer);
      view.setBigUint64(25, BigInt(amountUnits), true);
      view.setUint32(33, Math.floor(ms / 0x100000000), false);
      view.setUint32(37, ms % 0x100000000, false);
      let bin = "";
      for (const b of buf) bin += String.fromCharCode(b);
      return btoa(bin);
    }
    const ms = Date.UTC(2026, 0, 15, 12, 0, 0);
    const vl = buildVlPayload(1234500, ms);
    const receiptQueue = useReceiptQueueStore();
    await receiptQueue.enqueue(`https://suf.purs.gov.rs/v/?vl=${vl}`);
    const wrapper = mount(QueueModal, { props: { open: true } });
    await flushPromises();
    expect(wrapper.findAll('[data-testid="queue-item"]')).toHaveLength(1);
    expect(wrapper.text()).toContain("QR receipt");
    expect(wrapper.text()).toContain("123.45");
    expect(wrapper.text()).toContain("2026-01-15");
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

  describe("when the version check fails", () => {
    afterEach(() => {
      apiRequest.mockResolvedValue({ version: "test" });
    });

    it("asks for the server version with a time limit", async () => {
      apiRequest.mockClear();
      mount(QueueModal, { props: { open: true } });

      await vi.waitFor(() =>
        expect(apiRequest).toHaveBeenCalledWith("/api/version", { timeoutMs: 10_000 }),
      );
    });

    it("explains a server that does not answer instead of a failed version check", async () => {
      apiRequest.mockRejectedValue(new ServerUnreachable("no-answer"));
      const wrapper = mount(QueueModal, { props: { open: true } });

      await vi.waitFor(() => expect(wrapper.find('[data-testid="server-reach"]').exists()).toBe(true));
      expect(wrapper.get('[data-testid="server-reach"] h3').text()).toBe("The server isn't answering.");
      expect(wrapper.text()).not.toContain("server version check failed");
    });

    it("keeps reporting any other version-check failure as before", async () => {
      apiRequest.mockRejectedValue(Object.assign(new Error("boom"), { status: 500 }));
      const wrapper = mount(QueueModal, { props: { open: true } });

      await vi.waitFor(() => expect(wrapper.text()).toContain("server version check failed"));
      expect(wrapper.find('[data-testid="server-reach"]').exists()).toBe(false);
    });
  });
});

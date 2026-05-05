import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import ExpenseForm from "../src/components/ExpenseForm.vue";
import { useCatalogStore } from "../src/stores/catalog.js";
import { useQueueStore, _resetForTest as resetQueueStore } from "../src/stores/queue.js";
import { useCurrencyStore } from "../src/stores/currency.js";
import * as flushQueueModule from "../src/composables/flushQueue.js";

const SAMPLE = {
  catalog_version: 1,
  category_groups: [
    { id: 1, name: "еда", is_active: true },
    { id: 2, name: "транспорт", is_active: true },
  ],
  categories: [
    { id: 10, group_id: 1, name: "еда", is_active: true },
    { id: 11, group_id: 1, name: "кафе", is_active: true },
    { id: 12, group_id: 2, name: "такси", is_active: true },
  ],
  events: [
    {
      id: 100,
      name: "trip",
      date_from: "2026-04-01",
      date_to: "2026-12-31",
      auto_attach_enabled: false,
      is_active: true,
    },
  ],
  tags: [
    { id: 200, name: "vacation", is_active: true },
    { id: 201, name: "work", is_active: true },
  ],
};

async function resetQueueDb() {
  await resetQueueStore();
  await new Promise((resolve) => {
    const del = indexedDB.deleteDatabase("dinary-v2");
    del.onsuccess = del.onerror = del.onblocked = () => resolve();
    setTimeout(resolve, 1000);
  });
}

let pinia;

beforeEach(async () => {
  pinia = createPinia();
  setActivePinia(pinia);
  await resetQueueDb();
  vi.spyOn(flushQueueModule, "flushQueue").mockResolvedValue();
  // Stub fetch so catalog.load()'s conditional GET resolves cleanly.
  // Currency endpoints get a happy default; individual tests below
  // override the currency store directly when they need a specific
  // saved-list / preferred code.
  globalThis.fetch = vi.fn(async (url) => {
    const u = String(url);
    if (u.startsWith("/api/currencies")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ codes: ["RSD"], default_code: "RSD" }),
      };
    }
    return { ok: false, status: 304, json: async () => ({}) };
  });
});

afterEach(async () => {
  vi.restoreAllMocks();
  await resetQueueDb();
});

function seedCatalog() {
  const catalog = useCatalogStore();
  catalog.replaceSnapshot(SAMPLE);
}

function mountForm() {
  return mount(ExpenseForm, {
    global: { plugins: [pinia] },
  });
}

describe("ExpenseForm: defaults and selectors", () => {
  it("auto-selects the еда group + еда category on first paint", async () => {
    seedCatalog();
    const wrapper = mountForm();
    await flushPromises();
    expect(wrapper.find("#group").element.value).toBe("1");
    expect(wrapper.find("#category").element.value).toBe("10");
  });

  it("clears category when group changes to one that does not contain it", async () => {
    seedCatalog();
    const wrapper = mountForm();
    await flushPromises();
    await wrapper.find("#group").setValue("2");
    await flushPromises();
    expect(wrapper.find("#category").element.value).toBe("");
  });

  it("populates category options for the selected group", async () => {
    seedCatalog();
    const wrapper = mountForm();
    await flushPromises();
    const opts = wrapper.findAll("#category option").map((o) => o.element.value);
    expect(opts).toContain("10");
    expect(opts).toContain("11");
    expect(opts).not.toContain("12");
  });
});

describe("ExpenseForm: save flow", () => {
  it("rejects an invalid amount with a toast and does not enqueue", async () => {
    seedCatalog();
    const queue = useQueueStore();
    const wrapper = mountForm();
    await flushPromises();
    await wrapper.vm.save();
    await flushPromises();
    expect(queue.items).toHaveLength(0);
  });

  it("enqueues a valid expense", async () => {
    seedCatalog();
    const queue = useQueueStore();
    const wrapper = mountForm();
    await flushPromises();
    await wrapper.find("#amount").setValue("123.45");
    await wrapper.find("#date").setValue("2026-05-04");
    await wrapper.vm.save();
    await flushPromises();
    expect(queue.items).toHaveLength(1);
    expect(queue.items[0]).toMatchObject({
      amount: 123.45,
      currency: "RSD",
      category_id: 10,
      date: "2026-05-04",
    });
  });

  it("uses the currency store's selected code in the enqueued payload", async () => {
    seedCatalog();
    const queue = useQueueStore();
    const currency = useCurrencyStore();
    // Make the GET /api/currencies stub return EUR alongside RSD
    // and lock lastUsed to EUR so preferredCode() picks it once
    // init() merges the server snapshot.
    globalThis.fetch = vi.fn(async (url) => {
      const u = String(url);
      if (u.startsWith("/api/currencies")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            codes: ["RSD", "EUR"],
            default_code: "RSD",
          }),
        };
      }
      return { ok: false, status: 304, json: async () => ({}) };
    });
    currency.setLastUsed("EUR");
    const wrapper = mountForm();
    await flushPromises();
    await wrapper.find("#amount").setValue("10");
    await wrapper.vm.save();
    await flushPromises();
    expect(queue.items[0].currency).toBe("EUR");
  });
});

describe("ExpenseForm: + New buttons", () => {
  it("emits 'request-add' with the kind for each + New click", async () => {
    seedCatalog();
    const wrapper = mountForm();
    await flushPromises();
    const buttons = wrapper.findAll("button.btn-inline").filter((b) => b.text() === "+ New");
    expect(buttons.length).toBeGreaterThanOrEqual(4);
    await buttons[0].trigger("click");
    const emits = wrapper.emitted("request-add");
    expect(emits).toBeTruthy();
    expect(emits[0][0].kind).toBe("group");
  });

  it("disables + New on the category row until a group is chosen", async () => {
    const catalog = useCatalogStore();
    catalog.replaceSnapshot({ ...SAMPLE, category_groups: [] });
    const wrapper = mountForm();
    await flushPromises();
    const newCategoryBtn = wrapper
      .findAll("button.btn-inline")
      .filter((b) => b.text() === "+ New")[1];
    expect(newCategoryBtn.attributes("disabled")).toBeDefined();
  });
});

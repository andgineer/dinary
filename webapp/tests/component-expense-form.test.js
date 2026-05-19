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
  localStorage.clear();
  await resetQueueDb();
  vi.spyOn(flushQueueModule, "flushQueue").mockResolvedValue();
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

function seedCatalog({ defaults = true } = {}) {
  const catalog = useCatalogStore();
  catalog.replaceSnapshot(SAMPLE);
  if (defaults) {
    catalog.applyExpenseDefaults({
      default_group_id: 1,
      default_category_ids: { "1": 10, "2": 12 },
    });
  }
}

function mountForm() {
  return mount(ExpenseForm, {
    global: { plugins: [pinia] },
  });
}

// Helper: get the catalog trigger for a given kind
function getCatalogTrigger(wrapper, kind) {
  return wrapper.find(`[data-testid="catalog-trigger-${kind}"]`);
}

// Helper: open picker for a kind and click an option matching text
async function selectOption(wrapper, kind, text) {
  await getCatalogTrigger(wrapper, kind).trigger("click");
  const opts = wrapper.findAll(".catalog-picker-option");
  const opt = opts.find((o) => o.text().includes(text));
  if (!opt) throw new Error(`Option "${text}" not found in ${kind} picker`);
  await opt.trigger("click");
}

describe("ExpenseForm: defaults and selectors", () => {
  it("shows category name in pick-btn after default category is set", async () => {
    seedCatalog();
    const wrapper = mountForm();
    await flushPromises();
    expect(wrapper.find('[data-testid="category-pick-btn"]').text()).toContain("еда");
  });

  it("shows placeholder in pick-btn when no default category", async () => {
    seedCatalog({ defaults: false });
    const wrapper = mountForm();
    await flushPromises();
    expect(wrapper.find('[data-testid="category-pick-btn"]').text()).toContain("Select category");
  });

  it("category-pick-btn shows category set via CategorySheet select", async () => {
    seedCatalog();
    const wrapper = mountForm();
    await flushPromises();
    await wrapper.find('[data-testid="category-pick-btn"]').trigger("click");
    await flushPromises();
    const sheet = wrapper.findComponent({ name: "CategorySheet" });
    sheet.vm.$emit("select", 11);
    await flushPromises();
    expect(wrapper.find('[data-testid="category-pick-btn"]').text()).toContain("кафе");
  });

  it("group/category dropdown block is absent from DOM", async () => {
    seedCatalog();
    const wrapper = mountForm();
    await flushPromises();
    expect(wrapper.find(".group-category-block").exists()).toBe(false);
    expect(wrapper.find('[data-testid="catalog-trigger-group"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="catalog-trigger-category"]').exists()).toBe(false);
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

describe("ExpenseForm: + New buttons open inline create rows", () => {
  it("opens inline event form when the event + New button is clicked", async () => {
    seedCatalog();
    const wrapper = mountForm();
    await flushPromises();
    const newBtns = wrapper.findAll('[aria-label="New"]');
    const eventBtn = newBtns[0];
    await eventBtn.trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="inline-create-event"]').exists()).toBe(true);
  });

  it("opens inline create row when the + New tag button is clicked", async () => {
    seedCatalog();
    const wrapper = mountForm();
    await flushPromises();
    await wrapper.find('[aria-label="New tag"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="inline-create-row"]').exists()).toBe(true);
  });

  it("category-pick-btn sets categorySheetOpen to true when clicked", async () => {
    seedCatalog();
    const wrapper = mountForm();
    await flushPromises();
    const btn = wrapper.find('[data-testid="category-pick-btn"]');
    expect(btn.exists()).toBe(true);
    await btn.trigger("click");
    await flushPromises();
    const sheet = wrapper.findComponent({ name: "CategorySheet" });
    expect(sheet.props("open")).toBe(true);
  });
});

describe("ExpenseForm: receipt-parsed event is ignored", () => {
  it("does not change amount or date when dinary:receipt-parsed is dispatched", async () => {
    seedCatalog();
    const wrapper = mountForm();
    await flushPromises();

    window.dispatchEvent(
      new CustomEvent("dinary:receipt-parsed", {
        detail: { amount: 999, date: "2099-01-01" },
      }),
    );
    await flushPromises();

    expect(wrapper.find("#amount").element.value).toBe("");
    // Date should not have been overwritten with the event's date
    expect(wrapper.find("#date").element.value).not.toBe("2099-01-01");
  });
});

describe("ExpenseForm: offline init", () => {
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

  it("skips catalog and currency fetches when offline", async () => {
    const restore = mockOnLine(false);
    try {
      const fetchSpy = vi.fn();
      globalThis.fetch = fetchSpy;
      const wrapper = mountForm();
      await flushPromises();
      expect(fetchSpy).not.toHaveBeenCalled();
      wrapper.unmount();
    } finally {
      restore();
    }
  });

  it("sets selectedCurrency from preferredCode when offline", async () => {
    const restore = mockOnLine(false);
    try {
      localStorage.setItem("dinary.currency.lastUsed", "EUR");
      const currency = useCurrencyStore();
      currency.codes = ["EUR", "RSD"];
      const wrapper = mountForm();
      await flushPromises();
      expect(wrapper.find('[data-testid="currency-pill"]').text()).toContain("EUR");
      wrapper.unmount();
    } finally {
      restore();
    }
  });

  it("shows no error toast when offline", async () => {
    const restore = mockOnLine(false);
    try {
      const { useToastStore } = await import("../src/stores/toast.js");
      const toast = useToastStore();
      const showSpy = vi.spyOn(toast, "show");
      const wrapper = mountForm();
      await flushPromises();
      const errorCalls = showSpy.mock.calls.filter(([, type]) => type === "error");
      expect(errorCalls).toHaveLength(0);
      wrapper.unmount();
    } finally {
      restore();
    }
  });
});

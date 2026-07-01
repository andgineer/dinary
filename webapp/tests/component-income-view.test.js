import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import IncomeView from "../src/views/IncomeView.vue";
import { useIncomeStore } from "../src/stores/income.js";

beforeEach(async () => {
  await allure.epic("Income");
  await allure.feature("Frontend");
  await allure.story("IncomeView");
});

vi.mock("../src/api/income.js", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    listIncomes: vi.fn(async () => ({ items: [], has_more: false })),
  };
});

vi.mock("../src/api/currencies.js", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    fetchCurrencies: vi.fn(async () => ({ codes: ["RSD", "EUR"], default_code: "RSD" })),
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

function mountView(pinia) {
  return mount(IncomeView, { global: { plugins: [pinia] } });
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("IncomeView — online/offline button state", () => {
  it("enables Save and Refresh while online (regression: isOnline.value in template)", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const restore = mockOnLine(true);
    try {
      const income = useIncomeStore(pinia);
      vi.spyOn(income, "loadIfNeeded").mockResolvedValue();
      const wrapper = mountView(pinia);
      await flushPromises();

      expect(wrapper.find(".btn-save-income").attributes("disabled")).toBeUndefined();
      expect(wrapper.find('[aria-label="Refresh"]').attributes("disabled")).toBeUndefined();
      wrapper.unmount();
    } finally {
      restore();
    }
  });

  it("keeps Save and Refresh enabled while offline (user-initiated actions always proceed, per specs/reference/pwa-offline.md)", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const restore = mockOnLine(false);
    try {
      const income = useIncomeStore(pinia);
      vi.spyOn(income, "loadIfNeeded").mockResolvedValue();
      const wrapper = mountView(pinia);
      await flushPromises();

      expect(wrapper.find(".btn-save-income").attributes("disabled")).toBeUndefined();
      expect(wrapper.find('[aria-label="Refresh"]').attributes("disabled")).toBeUndefined();
      wrapper.unmount();
    } finally {
      restore();
    }
  });

  it("disables Refresh while a load is already in flight", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const restore = mockOnLine(true);
    try {
      const income = useIncomeStore(pinia);
      vi.spyOn(income, "loadIfNeeded").mockResolvedValue();
      const wrapper = mountView(pinia);
      await flushPromises();

      income.loading = true;
      await flushPromises();

      expect(wrapper.find('[aria-label="Refresh"]').attributes("disabled")).toBeDefined();
      wrapper.unmount();
    } finally {
      restore();
    }
  });
});

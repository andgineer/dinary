import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import CorrectionSheet from "../src/components/CorrectionSheet.vue";
import { useCatalogStore } from "../src/stores/catalog.js";
import { useReviewStore } from "../src/stores/review.js";

beforeEach(async () => {
  await allure.epic("Components");
  await allure.feature("CorrectionSheet");
});

const CATALOG = {
  catalog_version: 1,
  category_groups: [
    { id: 1, name: "Food", is_active: true },
    { id: 2, name: "Transport", is_active: true },
  ],
  categories: [
    { id: 10, group_id: 1, name: "groceries", is_active: true },
    { id: 11, group_id: 1, name: "cafe", is_active: true },
    { id: 20, group_id: 2, name: "taxi", is_active: true },
  ],
  events: [],
  tags: [],
};

const DOUBTFUL_ITEM = {
  id: 42,
  is_doubtful: true,
  name: "Chocolate",
  store: "Lidl",
  total: 250,
  currency: "RSD",
  count: 2,
  category_id: 10,
  suggested_category_id: 11,
};

const CERTAIN_ITEM = {
  id: 5,
  is_doubtful: false,
  name: "mleko",
  store: "Maxi",
  total: 200,
  currency: "RSD",
  count: 1,
  category_id: 10,
};

const TELEPORT_STUB = { props: ["to", "disabled"], template: "<div><slot /></div>" };

function mountSheet(pinia, props = {}) {
  return mount(CorrectionSheet, {
    props: { open: true, item: DOUBTFUL_ITEM, ...props },
    global: { plugins: [pinia], stubs: { Teleport: TELEPORT_STUB } },
  });
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("CorrectionSheet", () => {
  it("renders the sheet when open", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const wrapper = mountSheet(pinia);
    await flushPromises();
    expect(wrapper.find('[data-testid="correction-sheet"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it("does not render when closed", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const wrapper = mountSheet(pinia, { open: false });
    await flushPromises();
    expect(wrapper.find('[data-testid="correction-sheet"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("shows item name and store in header", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const wrapper = mountSheet(pinia);
    await flushPromises();
    expect(wrapper.text()).toContain("Chocolate");
    expect(wrapper.text()).toContain("Lidl");
    wrapper.unmount();
  });

  it("renders category buttons from the catalog", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const wrapper = mountSheet(pinia);
    await flushPromises();
    const catBtns = wrapper.findAll(".cat-btn");
    const names = catBtns.map((b) => b.text());
    expect(names.some((n) => n.includes("groceries"))).toBe(true);
    expect(names.some((n) => n.includes("cafe"))).toBe(true);
    wrapper.unmount();
  });

  it("shows footer text and marks category selected after clicking a category", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const wrapper = mountSheet(pinia, { item: { ...DOUBTFUL_ITEM, category_id: null } });
    await flushPromises();
    expect(wrapper.find(".footer-text").exists()).toBe(false);
    await wrapper.findAll(".cat-btn")[0].trigger("click");
    await flushPromises();
    expect(wrapper.findAll(".cat-btn.is-selected")).toHaveLength(1);
    expect(wrapper.find(".footer-text").exists()).toBe(true);
    wrapper.unmount();
  });

  it("calls reviewStore.correct and emits close on confirm", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const review = useReviewStore(pinia);
    const correctSpy = vi.spyOn(review, "correct").mockResolvedValue();
    const wrapper = mountSheet(pinia);
    await flushPromises();
    const catBtns = wrapper.findAll(".cat-btn");
    await catBtns[0].trigger("click");
    await wrapper.find(".confirm-btn").trigger("click");
    await flushPromises();
    expect(correctSpy).toHaveBeenCalled();
    expect(wrapper.emitted("close")).toBeTruthy();
    wrapper.unmount();
  });

  it("emits close when the × button is clicked", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const wrapper = mountSheet(pinia);
    await flushPromises();
    await wrapper.find('[aria-label="Close"]').trigger("click");
    expect(wrapper.emitted("close")).toBeTruthy();
    wrapper.unmount();
  });
});

describe("CorrectionSheet — scope selector", () => {
  it("scope selector is hidden for doubtful items", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const wrapper = mountSheet(pinia, { item: DOUBTFUL_ITEM });
    await flushPromises();
    expect(wrapper.find('[data-testid="scope-selector"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("scope selector is visible for certain items", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const wrapper = mountSheet(pinia, { item: CERTAIN_ITEM });
    await flushPromises();
    expect(wrapper.find('[data-testid="scope-selector"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it("passes scope=all for doubtful items regardless of selector state", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const review = useReviewStore(pinia);
    const correctSpy = vi.spyOn(review, "correct").mockResolvedValue();
    const wrapper = mountSheet(pinia, { item: DOUBTFUL_ITEM });
    await flushPromises();
    await wrapper.findAll(".cat-btn")[0].trigger("click");
    await wrapper.find(".confirm-btn").trigger("click");
    await flushPromises();
    expect(correctSpy).toHaveBeenCalledWith(DOUBTFUL_ITEM, expect.any(Number), "all");
    wrapper.unmount();
  });

  it("passes selected scope for certain items", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const review = useReviewStore(pinia);
    const correctSpy = vi.spyOn(review, "correct").mockResolvedValue();
    const wrapper = mountSheet(pinia, { item: CERTAIN_ITEM });
    await flushPromises();
    // Select "All history" radio
    const radios = wrapper.findAll('input[type="radio"]');
    const allHistoryRadio = radios.find((r) => r.element.value === "all");
    await allHistoryRadio.setValue("all");
    await wrapper.findAll(".cat-btn")[0].trigger("click");
    await wrapper.find(".confirm-btn").trigger("click");
    await flushPromises();
    expect(correctSpy).toHaveBeenCalledWith(CERTAIN_ITEM, expect.any(Number), "all");
    wrapper.unmount();
  });

  it("resets scope to 'single' when sheet is reopened", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const wrapper = mount(CorrectionSheet, {
      props: { open: true, item: CERTAIN_ITEM },
      global: { plugins: [pinia], stubs: { Teleport: TELEPORT_STUB } },
    });
    await flushPromises();
    // Change scope away from default
    const radios = wrapper.findAll('input[type="radio"]');
    const allHistoryRadio = radios.find((r) => r.element.value === "all");
    await allHistoryRadio.setValue("all");
    // Close and reopen
    await wrapper.setProps({ open: false });
    await wrapper.setProps({ open: true });
    await flushPromises();
    const singleRadio = wrapper.findAll('input[type="radio"]').find((r) => r.element.value === "single");
    expect(singleRadio.element.checked).toBe(true);
    wrapper.unmount();
  });
});

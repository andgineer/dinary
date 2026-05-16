import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import CorrectionSheet from "../src/components/CorrectionSheet.vue";
import { useCatalogStore } from "../src/stores/catalog.js";
import { useReviewStore } from "../src/stores/review.js";

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
  name: "Chocolate",
  store: "Lidl",
  total: 250,
  currency: "RSD",
  count: 2,
  current_category_id: 10,
  suggested_category_id: 11,
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
    const wrapper = mountSheet(pinia, { item: { ...DOUBTFUL_ITEM, current_category_id: null } });
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

import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import CertainRow from "../src/components/CertainRow.vue";
import { useCatalogStore } from "../src/stores/catalog.js";

const CATALOG = {
  catalog_version: 1,
  category_groups: [{ id: 1, name: "Food", is_active: true }],
  categories: [{ id: 10, group_id: 1, name: "groceries", is_active: true }],
  events: [],
  tags: [],
};

function makeItem(overrides = {}) {
  return {
    id: 2,
    is_doubtful: false,
    store: "Maxi",
    items_count: 19,
    total: 4870,
    currency: "RSD",
    datetime: "2026-05-08T14:32:00",
    top_categories: [{ id: 10, n: 14 }],
    ...overrides,
  };
}

beforeEach(() => {
  const pinia = createPinia();
  setActivePinia(pinia);
  useCatalogStore(pinia).replaceSnapshot(CATALOG);
});

describe("CertainRow — content", () => {
  it("shows store name, item count and currency", () => {
    const w = mount(CertainRow, { props: { item: makeItem() } });
    expect(w.text()).toContain("Maxi");
    expect(w.text()).toContain("19 items");
    expect(w.text()).toContain("RSD");
  });

  it("formats date as DD.MM", () => {
    const w = mount(CertainRow, { props: { item: makeItem() } });
    expect(w.text()).toContain("08.05");
  });

  it("resolves dominant group name from top_categories", () => {
    const w = mount(CertainRow, { props: { item: makeItem() } });
    expect(w.text()).toContain("Food");
  });

  it("omits group name when top_categories is empty", () => {
    const w = mount(CertainRow, {
      props: { item: makeItem({ top_categories: [] }) },
    });
    expect(w.text()).not.toContain("Food");
  });

  it("omits date when datetime is missing", () => {
    const w = mount(CertainRow, {
      props: { item: makeItem({ datetime: null }) },
    });
    expect(w.find(".row-date").exists()).toBe(false);
  });
});

describe("CertainRow — interaction", () => {
  it("emits tap on click", async () => {
    const w = mount(CertainRow, { props: { item: makeItem() } });
    await w.find('[data-testid="certain-row"]').trigger("click");
    expect(w.emitted("tap")).toBeTruthy();
  });

  it("emits tap on Enter keydown", async () => {
    const w = mount(CertainRow, { props: { item: makeItem() } });
    await w.find('[data-testid="certain-row"]').trigger("keydown.enter");
    expect(w.emitted("tap")).toBeTruthy();
  });
});

import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import DoubtfulRow from "../src/components/DoubtfulRow.vue";
import { useCatalogStore } from "../src/stores/catalog.js";

const CATALOG = {
  catalog_version: 1,
  category_groups: [
    { id: 1, name: "Food", is_active: true },
    { id: 2, name: "Transport", is_active: true },
  ],
  categories: [
    { id: 10, group_id: 1, name: "groceries", is_active: true },
    { id: 11, group_id: 1, name: "cafe", is_active: true },
  ],
  events: [],
  tags: [],
};

function makeItem(overrides = {}) {
  return {
    id: 1,
    is_doubtful: true,
    name: "Chocolate bar",
    store: "Lidl",
    total: 1340,
    currency: "RSD",
    count: 6,
    confidence_level: 3,
    current_category_id: 10,
    suggested_category_id: 10,
    ...overrides,
  };
}

beforeEach(() => {
  const pinia = createPinia();
  setActivePinia(pinia);
  useCatalogStore(pinia).replaceSnapshot(CATALOG);
});

describe("DoubtfulRow — content", () => {
  it("shows item name, store and count", () => {
    const w = mount(DoubtfulRow, { props: { item: makeItem() } });
    expect(w.text()).toContain("Chocolate bar");
    expect(w.text()).toContain("Lidl");
    expect(w.text()).toContain("×6");
  });

  it("shows the currency", () => {
    const w = mount(DoubtfulRow, { props: { item: makeItem() } });
    expect(w.text()).toContain("RSD");
  });
});

describe("DoubtfulRow — confidence pill", () => {
  it.each([
    [1, "no match", "pill-danger"],
    [2, "guess", "pill-warn"],
    [3, "maybe", "pill-warn"],
  ])("level %i → label '%s' with class '%s'", (level, label, cls) => {
    const w = mount(DoubtfulRow, {
      props: { item: makeItem({ confidence_level: level }) },
    });
    const pill = w.find(".confidence-pill");
    expect(pill.text()).toBe(label);
    expect(pill.classes()).toContain(cls);
  });
});

describe("DoubtfulRow — suggestion display", () => {
  it("hides arrow and suggested chip when current === suggested", () => {
    const w = mount(DoubtfulRow, {
      props: { item: makeItem({ current_category_id: 10, suggested_category_id: 10 }) },
    });
    expect(w.find(".row-arrow").exists()).toBe(false);
    expect(w.find(".suggested-pill").exists()).toBe(false);
  });

  it("shows arrow and suggested chip when current !== suggested", () => {
    const w = mount(DoubtfulRow, {
      props: { item: makeItem({ current_category_id: 10, suggested_category_id: 11 }) },
    });
    expect(w.find(".row-arrow").exists()).toBe(true);
    expect(w.find(".suggested-pill").exists()).toBe(true);
    expect(w.find(".suggested-pill").text()).toContain("cafe");
  });
});

describe("DoubtfulRow — interaction", () => {
  it("emits tap on click", async () => {
    const w = mount(DoubtfulRow, { props: { item: makeItem() } });
    await w.find('[data-testid="doubtful-row"]').trigger("click");
    expect(w.emitted("tap")).toBeTruthy();
  });

  it("emits tap on Enter keydown", async () => {
    const w = mount(DoubtfulRow, { props: { item: makeItem() } });
    await w.find('[data-testid="doubtful-row"]').trigger("keydown.enter");
    expect(w.emitted("tap")).toBeTruthy();
  });
});

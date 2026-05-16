import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import ExpenseRow from "../src/components/ExpenseRow.vue";
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

function makeDoubtful(overrides = {}) {
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

function makeCertain(overrides = {}) {
  return {
    id: 2,
    is_doubtful: false,
    name: "mleko",
    store: "Maxi",
    total: 200,
    currency: "RSD",
    datetime: "2026-05-08T14:32:00",
    category_id: 10,
    category_name: "groceries",
    confidence_level: 4,
    ...overrides,
  };
}

beforeEach(() => {
  const pinia = createPinia();
  setActivePinia(pinia);
  useCatalogStore(pinia).replaceSnapshot(CATALOG);
});

describe("ExpenseRow — doubtful item", () => {
  it("shows item name, store and count", () => {
    const w = mount(ExpenseRow, { props: { item: makeDoubtful() } });
    expect(w.text()).toContain("Chocolate bar");
    expect(w.text()).toContain("Lidl");
    expect(w.text()).toContain("×6");
  });

  it("shows the currency", () => {
    const w = mount(ExpenseRow, { props: { item: makeDoubtful() } });
    expect(w.text()).toContain("RSD");
  });

  it("has the doubtful CSS modifier class", () => {
    const w = mount(ExpenseRow, { props: { item: makeDoubtful() } });
    expect(w.find(".expense-row--doubtful").exists()).toBe(true);
  });

  it("uses testid doubtful-row", () => {
    const w = mount(ExpenseRow, { props: { item: makeDoubtful() } });
    expect(w.find('[data-testid="doubtful-row"]').exists()).toBe(true);
  });
});

describe("ExpenseRow — confidence pill", () => {
  it.each([
    [1, "no match", "pill-danger"],
    [2, "guess", "pill-warn"],
    [3, "maybe", "pill-warn"],
  ])("level %i → label '%s' with class '%s'", (level, label, cls) => {
    const w = mount(ExpenseRow, {
      props: { item: makeDoubtful({ confidence_level: level }) },
    });
    const pill = w.find(".confidence-pill");
    expect(pill.text()).toBe(label);
    expect(pill.classes()).toContain(cls);
  });
});

describe("ExpenseRow — suggestion display", () => {
  it("hides arrow and suggested chip when current === suggested", () => {
    const w = mount(ExpenseRow, {
      props: { item: makeDoubtful({ current_category_id: 10, suggested_category_id: 10 }) },
    });
    expect(w.find(".row-arrow").exists()).toBe(false);
    expect(w.find(".suggested-pill").exists()).toBe(false);
  });

  it("shows arrow and suggested chip when current !== suggested", () => {
    const w = mount(ExpenseRow, {
      props: { item: makeDoubtful({ current_category_id: 10, suggested_category_id: 11 }) },
    });
    expect(w.find(".row-arrow").exists()).toBe(true);
    expect(w.find(".suggested-pill").exists()).toBe(true);
    expect(w.find(".suggested-pill").text()).toContain("cafe");
  });
});

describe("ExpenseRow — certain item", () => {
  it("shows item name, store, amount and currency", () => {
    const w = mount(ExpenseRow, { props: { item: makeCertain() } });
    expect(w.text()).toContain("mleko");
    expect(w.text()).toContain("Maxi");
    expect(w.text()).toContain("RSD");
  });

  it("formats date as DD.MM", () => {
    const w = mount(ExpenseRow, { props: { item: makeCertain() } });
    expect(w.text()).toContain("08.05");
  });

  it("resolves category name via catalog", () => {
    const w = mount(ExpenseRow, { props: { item: makeCertain() } });
    expect(w.text()).toContain("Food");
    expect(w.text()).toContain("groceries");
  });

  it("falls back to category_name string when catalog has no match", () => {
    const w = mount(ExpenseRow, {
      props: { item: makeCertain({ category_id: 99, category_name: "misc" }) },
    });
    expect(w.text()).toContain("misc");
  });

  it("omits date when datetime is missing", () => {
    const w = mount(ExpenseRow, {
      props: { item: makeCertain({ datetime: null }) },
    });
    expect(w.find(".row-date").exists()).toBe(false);
  });

  it("falls back to store name when item name is null", () => {
    const w = mount(ExpenseRow, {
      props: { item: makeCertain({ name: null }) },
    });
    expect(w.find(".row-name").text()).toBe("Maxi");
  });

  it("does not have the doubtful CSS modifier class", () => {
    const w = mount(ExpenseRow, { props: { item: makeCertain() } });
    expect(w.find(".expense-row--doubtful").exists()).toBe(false);
  });

  it("uses testid certain-row", () => {
    const w = mount(ExpenseRow, { props: { item: makeCertain() } });
    expect(w.find('[data-testid="certain-row"]').exists()).toBe(true);
  });

  it("hides confidence pill for certain items", () => {
    const w = mount(ExpenseRow, { props: { item: makeCertain() } });
    expect(w.find(".confidence-pill").exists()).toBe(false);
  });
});

describe("ExpenseRow — interaction", () => {
  it("emits tap on click for doubtful", async () => {
    const w = mount(ExpenseRow, { props: { item: makeDoubtful() } });
    await w.find('[data-testid="doubtful-row"]').trigger("click");
    expect(w.emitted("tap")).toBeTruthy();
  });

  it("emits tap on Enter for doubtful", async () => {
    const w = mount(ExpenseRow, { props: { item: makeDoubtful() } });
    await w.find('[data-testid="doubtful-row"]').trigger("keydown.enter");
    expect(w.emitted("tap")).toBeTruthy();
  });

  it("emits tap on click for certain", async () => {
    const w = mount(ExpenseRow, { props: { item: makeCertain() } });
    await w.find('[data-testid="certain-row"]').trigger("click");
    expect(w.emitted("tap")).toBeTruthy();
  });

  it("emits tap on Enter for certain", async () => {
    const w = mount(ExpenseRow, { props: { item: makeCertain() } });
    await w.find('[data-testid="certain-row"]').trigger("keydown.enter");
    expect(w.emitted("tap")).toBeTruthy();
  });
});

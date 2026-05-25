import { describe, it, expect, beforeEach } from "vitest";
import { nextTick } from "vue";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import CategorySheet from "../src/components/CategorySheet.vue";
import { useCatalogStore } from "../src/stores/catalog.js";

beforeEach(async () => {
  await allure.epic("Expenses");
  await allure.feature("Frontend");
  await allure.story("CategorySheet");
});

const TELEPORT_STUB = { props: ["to"], template: "<div><slot /></div>" };

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

function mountSheet(props = {}) {
  return mount(CategorySheet, {
    props: { open: true, suggestions: [], ...props },
    global: { plugins: [createPinia()], stubs: { Teleport: TELEPORT_STUB } },
  });
}

beforeEach(() => {
  const pinia = createPinia();
  setActivePinia(pinia);
  useCatalogStore(pinia).replaceSnapshot(CATALOG);
});

describe("CategorySheet — search", () => {
  it("shows category groups when query is empty", () => {
    const w = mountSheet();
    expect(w.find('[data-testid="category-group"]').exists()).toBe(true);
    expect(w.find('[data-testid="flat-results"]').exists()).toBe(false);
  });

  it("shows flat results and hides groups when query is non-empty", async () => {
    const w = mountSheet();
    await w.find(".search-input").setValue("gro");
    expect(w.find('[data-testid="flat-results"]').exists()).toBe(true);
    expect(w.find('[data-testid="category-group"]').exists()).toBe(false);
  });

  it("filters flat results by category name (case-insensitive)", async () => {
    const w = mountSheet();
    await w.find(".search-input").setValue("cafe");
    const items = w.findAll(".flat-item");
    expect(items.length).toBe(1);
    expect(items[0].text()).toContain("cafe");
  });

  it("clear button resets query and shows groups again", async () => {
    const w = mountSheet();
    await w.find(".search-input").setValue("taxi");
    expect(w.find(".clear-btn").exists()).toBe(true);
    await w.find(".clear-btn").trigger("click");
    expect(w.find('[data-testid="flat-results"]').exists()).toBe(false);
    expect(w.find('[data-testid="category-group"]').exists()).toBe(true);
  });
});

describe("CategorySheet — suggestion tap emits select", () => {
  it("emits select with suggestion id when pill is clicked", async () => {
    const sug = [{ id: 10, name: "groceries" }];
    const w = mountSheet({ suggestions: sug });
    const pill = w.find('[data-testid="suggestion-pills"] .cat-btn');
    await pill.trigger("click");
    expect(w.emitted("select")?.[0]).toEqual([10]);
    expect(w.emitted("close")).toBeTruthy();
  });
});

describe("CategorySheet — grid tap emits select", () => {
  it("emits select with category id when grid button is clicked", async () => {
    const w = mountSheet();
    const btn = w.find('[data-testid="category-group"] .cat-btn');
    await btn.trigger("click");
    expect(w.emitted("select")).toBeTruthy();
    expect(w.emitted("close")).toBeTruthy();
  });
});

describe("CategorySheet — flat result tap emits select", () => {
  it("emits select when flat result is clicked", async () => {
    const w = mountSheet();
    await w.find(".search-input").setValue("taxi");
    const item = w.find(".flat-item");
    await item.trigger("click");
    expect(w.emitted("select")?.[0]).toEqual([20]);
  });
});

describe("CategorySheet — sticky search bar", () => {
  it("search-wrap is outside sheet-body (not inside scrollable area)", () => {
    const w = mountSheet();
    const sheetBody = w.find(".sheet-body");
    expect(sheetBody.find(".search-wrap").exists()).toBe(false);
    expect(w.find(".sheet .search-wrap").exists()).toBe(true);
  });

  it("search-wrap renders as a direct child of sheet (outside scroll container)", () => {
    const w = mountSheet();
    const sheetEl = w.find(".sheet");
    const directChildren = sheetEl.element.children;
    const childClasses = Array.from(directChildren).map((el) => el.className);
    expect(childClasses.some((c) => c.includes("search-wrap"))).toBe(true);
  });

  it("resets sheet-body scrollTop to 0 when sheet is opened", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const w = mount(CategorySheet, {
      props: { open: false, suggestions: [] },
      global: { plugins: [pinia], stubs: { Teleport: TELEPORT_STUB } },
    });
    await w.setProps({ open: true });
    await nextTick();
    await nextTick();
    expect(w.find(".sheet-body").element.scrollTop).toBe(0);
  });
});

describe("CategorySheet — Escape key", () => {
  it("clears query when Escape pressed with non-empty query", async () => {
    const w = mountSheet();
    await w.find(".search-input").setValue("taxi");
    expect(w.find('[data-testid="flat-results"]').exists()).toBe(true);
    await w.find(".search-input").trigger("keydown", { key: "Escape" });
    expect(w.find('[data-testid="flat-results"]').exists()).toBe(false);
    expect(w.find('[data-testid="category-group"]').exists()).toBe(true);
  });

  it("emits close when Escape pressed with empty query", async () => {
    const w = mountSheet();
    await w.find(".search-input").trigger("keydown", { key: "Escape" });
    expect(w.emitted("close")).toBeTruthy();
  });
});

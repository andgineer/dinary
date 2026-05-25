import { beforeEach, describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import CategoryQuickPicks from "../src/components/CategoryQuickPicks.vue";

beforeEach(async () => {
  await allure.epic("Expenses");
  await allure.feature("Frontend");
  await allure.story("CategoryQuickPicks");
});

const CATS = [
  { id: 1, name: "groceries" },
  { id: 2, name: "cafe" },
];

describe("CategoryQuickPicks — pill tap emits select", () => {
  it("emits select with category id when pill is clicked", async () => {
    const w = mount(CategoryQuickPicks, { props: { categories: CATS } });
    const pills = w.findAll(".pick-pill");
    expect(pills.length).toBe(2);
    await pills[0].trigger("click");
    expect(w.emitted("select")?.[0]).toEqual([1]);
  });

  it("emits select with correct id for second pill", async () => {
    const w = mount(CategoryQuickPicks, { props: { categories: CATS } });
    await w.findAll(".pick-pill")[1].trigger("click");
    expect(w.emitted("select")?.[0]).toEqual([2]);
  });
});

describe("CategoryQuickPicks — no search button", () => {
  it("does not render a search icon button", () => {
    const w = mount(CategoryQuickPicks, { props: { categories: CATS } });
    expect(w.find(".pick-search").exists()).toBe(false);
  });

  it("does not define a search emit", () => {
    const w = mount(CategoryQuickPicks, { props: { categories: CATS } });
    expect(w.emitted("search")).toBeUndefined();
  });
});

describe("CategoryQuickPicks — selected highlight", () => {
  it("marks the matching pill is-selected when selectedCategoryId matches", () => {
    const w = mount(CategoryQuickPicks, {
      props: { categories: CATS, selectedCategoryId: 2 },
    });
    const pills = w.findAll(".pick-pill");
    expect(pills[0].classes()).not.toContain("is-selected");
    expect(pills[1].classes()).toContain("is-selected");
  });

  it("marks no pill when selectedCategoryId is null", () => {
    const w = mount(CategoryQuickPicks, {
      props: { categories: CATS, selectedCategoryId: null },
    });
    for (const pill of w.findAll(".pick-pill")) {
      expect(pill.classes()).not.toContain("is-selected");
    }
  });

  it("marks no pill when selectedCategoryId does not match any category", () => {
    const w = mount(CategoryQuickPicks, {
      props: { categories: CATS, selectedCategoryId: 99 },
    });
    for (const pill of w.findAll(".pick-pill")) {
      expect(pill.classes()).not.toContain("is-selected");
    }
  });

  it("updates the highlight when selectedCategoryId prop changes", async () => {
    const w = mount(CategoryQuickPicks, {
      props: { categories: CATS, selectedCategoryId: 1 },
    });
    expect(w.findAll(".pick-pill")[0].classes()).toContain("is-selected");
    await w.setProps({ selectedCategoryId: 2 });
    expect(w.findAll(".pick-pill")[0].classes()).not.toContain("is-selected");
    expect(w.findAll(".pick-pill")[1].classes()).toContain("is-selected");
  });
});

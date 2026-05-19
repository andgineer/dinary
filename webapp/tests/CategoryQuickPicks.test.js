import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import CategoryQuickPicks from "../src/components/CategoryQuickPicks.vue";

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

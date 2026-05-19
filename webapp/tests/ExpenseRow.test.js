import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import ExpenseRow from "../src/components/ExpenseRow.vue";

const BASE = {
  id: 1,
  date: "2026-05-18",
  store: "Lidl",
  category_name: "groceries",
  tags: [],
  confidence_level: 5,
};

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("ExpenseRow — renders fields", () => {
  it("renders formatted date", () => {
    const w = mount(ExpenseRow, { props: { expense: BASE } });
    expect(w.text()).toContain("18 May");
  });

  it("renders store name", () => {
    const w = mount(ExpenseRow, { props: { expense: BASE } });
    expect(w.text()).toContain("Lidl");
  });

  it("does not render amount (removed by design)", () => {
    const w = mount(ExpenseRow, { props: { expense: { ...BASE, amount: 250, currency_original: "RSD" } } });
    expect(w.text()).not.toContain("250");
  });

  it("renders category name prominently", () => {
    const w = mount(ExpenseRow, { props: { expense: BASE } });
    expect(w.text()).toContain("groceries");
  });

  it("renders tag chips", () => {
    const expense = {
      ...BASE,
      tags: [
        { id: 1, name: "собака", icon: "🐾" },
        { id: 2, name: "зож" },
      ],
    };
    const w = mount(ExpenseRow, { props: { expense } });
    expect(w.text()).toContain("собака");
    expect(w.text()).toContain("зож");
    expect(w.findAll(".tag-chip").length).toBe(2);
  });
});

describe("ExpenseRow — tap emits tap", () => {
  it("emits tap on click (zero movement = tap)", async () => {
    const w = mount(ExpenseRow, { props: { expense: BASE } });
    await w.find(".row-slider").trigger("click");
    expect(w.emitted("tap")).toBeTruthy();
  });
});

describe("ExpenseRow — swipe reveals Edit button", () => {
  it("Edit button is rendered in the panel", () => {
    const w = mount(ExpenseRow, { props: { expense: BASE } });
    expect(w.find(".panel-btn").text()).toContain("Edit");
  });
});

describe("ExpenseRow — item_name primary display", () => {
  it("renders item_name as primary text when present", () => {
    const w = mount(ExpenseRow, {
      props: { expense: { ...BASE, item_name: "Hleb beli 500g", store: "Lidl" } },
    });
    expect(w.find(".row-primary").text()).toBe("Hleb beli 500g");
    expect(w.find(".row-store").text()).toContain("Lidl");
  });

  it("renders store as primary when item_name is null", () => {
    const w = mount(ExpenseRow, {
      props: { expense: { ...BASE, item_name: null, store: "Maxi" } },
    });
    expect(w.find(".row-primary").text()).toBe("Maxi");
  });

  it("does not show row-store when item_name is null", () => {
    const w = mount(ExpenseRow, {
      props: { expense: { ...BASE, item_name: null, store: "Maxi" } },
    });
    expect(w.find(".row-store").exists()).toBe(false);
  });
});

describe("ExpenseRow — warning border", () => {
  it("adds warning class when confidence_level < 4", () => {
    const w = mount(ExpenseRow, { props: { expense: { ...BASE, confidence_level: 3 } } });
    expect(w.find(".row-wrap--warning").exists()).toBe(true);
  });

  it("no warning class when confidence_level >= 4", () => {
    const w = mount(ExpenseRow, { props: { expense: { ...BASE, confidence_level: 4 } } });
    expect(w.find(".row-wrap--warning").exists()).toBe(false);
  });

  it("no warning class when confidence_level is absent", () => {
    const expense = { ...BASE };
    delete expense.confidence_level;
    const w = mount(ExpenseRow, { props: { expense } });
    expect(w.find(".row-wrap--warning").exists()).toBe(false);
  });
});

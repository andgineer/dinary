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

  it("renders amount_original with currency in bottom right", () => {
    const w = mount(ExpenseRow, { props: { expense: { ...BASE, amount_original: 250, currency_original: "RSD" } } });
    expect(w.find(".row-amount").text()).toContain("250");
    expect(w.find(".row-amount").text()).toContain("RSD");
  });

  it("hides amount when amount_original is absent", () => {
    const w = mount(ExpenseRow, { props: { expense: BASE } });
    expect(w.find(".row-amount").exists()).toBe(false);
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

describe("ExpenseRow — manual entry compact layout", () => {
  it("hides row-top when no name fields are present", () => {
    const w = mount(ExpenseRow, {
      props: { expense: { ...BASE, store: undefined, item_name: null, store_name: null, merchant: null } },
    });
    expect(w.find(".row-top").exists()).toBe(false);
  });

  it("shows row-top when store name is available", () => {
    const w = mount(ExpenseRow, { props: { expense: BASE } });
    expect(w.find(".row-top").exists()).toBe(true);
  });

  it("shows row-top when item_name is present", () => {
    const w = mount(ExpenseRow, {
      props: { expense: { ...BASE, store: undefined, item_name: "Hleb", store_name: null } },
    });
    expect(w.find(".row-top").exists()).toBe(true);
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

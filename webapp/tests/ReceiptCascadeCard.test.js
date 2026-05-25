import { beforeEach, describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import ReceiptCascadeCard from "../src/components/ReceiptCascadeCard.vue";

beforeEach(async () => {
  await allure.epic("Receipts");
  await allure.feature("Frontend");
  await allure.story("ReceiptCascadeCard");
});

const CASCADE = {
  merchant: "Maxi",
  captured_at: "2026-05-10T12:00:00",
  expenses: [
    { id: 1, item_name: "hleb", amount: 100, currency: "RSD" },
    { id: 2, item_name: "mleko", amount: 80, currency: "RSD" },
  ],
  total: { amount: 180, currency: "RSD" },
};

describe("ReceiptCascadeCard — loading state", () => {
  it("shows loading text when loading is true", () => {
    const wrapper = mount(ReceiptCascadeCard, { props: { loading: true, cascade: null } });
    expect(wrapper.text()).toContain("Loading");
  });

  it("hides cascade content while loading", () => {
    const wrapper = mount(ReceiptCascadeCard, { props: { loading: true, cascade: CASCADE } });
    expect(wrapper.find(".cascade-header").exists()).toBe(false);
  });
});

describe("ReceiptCascadeCard — cascade data", () => {
  it("shows merchant name", () => {
    const wrapper = mount(ReceiptCascadeCard, { props: { loading: false, cascade: CASCADE } });
    expect(wrapper.find(".cascade-merchant").text()).toBe("Maxi");
  });

  it("renders one row per expense", () => {
    const wrapper = mount(ReceiptCascadeCard, { props: { loading: false, cascade: CASCADE } });
    expect(wrapper.findAll(".cascade-row")).toHaveLength(2);
  });

  it("shows item names in rows", () => {
    const wrapper = mount(ReceiptCascadeCard, { props: { loading: false, cascade: CASCADE } });
    const names = wrapper.findAll(".cascade-item-name").map((el) => el.text());
    expect(names).toContain("hleb");
    expect(names).toContain("mleko");
  });

  it("shows formatted total", () => {
    const wrapper = mount(ReceiptCascadeCard, { props: { loading: false, cascade: CASCADE } });
    expect(wrapper.find(".cascade-total-amount").text()).toContain("180.00");
  });

  it("falls back to 'Receipt' when merchant is empty", () => {
    const wrapper = mount(ReceiptCascadeCard, {
      props: { loading: false, cascade: { ...CASCADE, merchant: "" } },
    });
    expect(wrapper.find(".cascade-merchant").text()).toBe("Receipt");
  });
});

describe("ReceiptCascadeCard — no data", () => {
  it("renders the card container even with no cascade and no loading", () => {
    const wrapper = mount(ReceiptCascadeCard, { props: { loading: false, cascade: null } });
    expect(wrapper.find("[data-testid='cascade-card']").exists()).toBe(true);
    expect(wrapper.find(".cascade-header").exists()).toBe(false);
  });
});

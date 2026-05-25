import { beforeEach, describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import CurrencyAmountRow from "../src/components/CurrencyAmountRow.vue";

beforeEach(async () => {
  await allure.epic("Currencies");
  await allure.feature("Frontend");
  await allure.story("CurrencyAmountRow");
});

vi.mock("../src/components/CurrencyPicker.vue", () => ({
  default: {
    name: "CurrencyPicker",
    props: ["modelValue"],
    emits: ["update:modelValue", "close"],
    template: "<div data-testid='currency-picker-mock' />",
  },
}));

function mountRow(props = {}) {
  return mount(CurrencyAmountRow, {
    props: { amount: "100", currency: "RSD", ...props },
  });
}

describe("CurrencyAmountRow — rendering", () => {
  it("shows the currency code on the pill", () => {
    const wrapper = mountRow({ currency: "EUR" });
    expect(wrapper.find("[data-testid='currency-pill']").text()).toBe("EUR");
  });

  it("falls back to RSD when currency is empty", () => {
    const wrapper = mountRow({ currency: "" });
    expect(wrapper.find("[data-testid='currency-pill']").text()).toBe("RSD");
  });

  it("shows the amount value in the input", () => {
    const wrapper = mountRow({ amount: "250" });
    expect(wrapper.find("[data-testid='amount-input']").element.value).toBe("250");
  });

  it("does not render the picker by default", () => {
    const wrapper = mountRow();
    expect(wrapper.find("[data-testid='currency-picker-mock']").exists()).toBe(false);
  });
});

describe("CurrencyAmountRow — interactions", () => {
  it("opens the picker when pill is clicked", async () => {
    const wrapper = mountRow();
    await wrapper.find("[data-testid='currency-pill']").trigger("click");
    expect(wrapper.find("[data-testid='currency-picker-mock']").exists()).toBe(true);
  });

  it("closes the picker on second pill click", async () => {
    const wrapper = mountRow();
    await wrapper.find("[data-testid='currency-pill']").trigger("click");
    await wrapper.find("[data-testid='currency-pill']").trigger("click");
    expect(wrapper.find("[data-testid='currency-picker-mock']").exists()).toBe(false);
  });

  it("emits update:amount when the input changes", async () => {
    const wrapper = mountRow();
    const input = wrapper.find("[data-testid='amount-input']");
    await input.setValue("500");
    expect(wrapper.emitted("update:amount")).toBeTruthy();
    expect(wrapper.emitted("update:amount")[0][0]).toBe("500");
  });
});

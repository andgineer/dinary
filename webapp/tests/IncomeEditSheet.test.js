import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import IncomeEditSheet from "../src/components/IncomeEditSheet.vue";
import { useCurrencyStore } from "../src/stores/currency.js";
import { currencyColor } from "../src/data/currency-colors.js";

beforeEach(async () => {
  await allure.epic("Income");
  await allure.feature("Frontend");
  await allure.story("IncomeEditSheet");
});

const TELEPORT_STUB = { props: ["to"], template: "<div><slot /></div>" };

const INCOME = {
  id: 7,
  year: 2026,
  month: 5,
  amount_original: 1000,
  currency_original: "USD",
  income_date: "2026-05-17",
  comment: "",
};

function mountSheet(props = {}) {
  const pinia = createPinia();
  setActivePinia(pinia);
  const currency = useCurrencyStore(pinia);
  currency.codes = ["EUR", "RSD", "USD"];
  return mount(IncomeEditSheet, {
    props: { open: true, income: INCOME, ...props },
    global: { plugins: [pinia], stubs: { Teleport: TELEPORT_STUB } },
  });
}

describe("IncomeEditSheet — currency pill color", () => {
  it("fills the pill with the income's currency color", () => {
    const wrapper = mountSheet();
    const pill = wrapper.find(".currency-pill");
    expect(pill.text()).toBe("USD");
    expect(pill.element.style.background).toBe(currencyColor("USD"));
  });

  it("recolors the pill when another currency is picked", async () => {
    const wrapper = mountSheet();
    const pill = wrapper.find(".currency-pill");
    await pill.trigger("click");
    const eurChip = wrapper.findAll(".currency-chip").find((c) => c.text() === "EUR");
    await eurChip.trigger("click");
    expect(pill.text()).toBe("EUR");
    expect(pill.element.style.background).toBe(currencyColor("EUR"));
  });
});

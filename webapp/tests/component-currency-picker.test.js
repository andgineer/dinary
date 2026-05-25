import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import CurrencyPicker from "../src/components/CurrencyPicker.vue";
import { useCurrencyStore } from "../src/stores/currency.js";
import * as currenciesApi from "../src/api/currencies.js";

beforeEach(async () => {
  await allure.epic("Currencies");
  await allure.feature("Frontend");
  await allure.story("CurrencyPicker");
});

let pinia;

beforeEach(() => {
  pinia = createPinia();
  setActivePinia(pinia);
  try {
    localStorage.clear();
  } catch {
    /* ignored */
  }
});

afterEach(() => {
  vi.restoreAllMocks();
});

function mountPicker(props = {}) {
  const store = useCurrencyStore();
  store.codes = ["RSD", "USD"];
  store.defaultCode = "RSD";
  return mount(CurrencyPicker, {
    props: { modelValue: "RSD", ...props },
    global: { plugins: [pinia] },
    attachTo: document.body,
  });
}

describe("CurrencyPicker — saved chips", () => {
  it("renders a chip per saved code", () => {
    const wrapper = mountPicker();
    const chips = wrapper.findAll(".currency-chip");
    expect(chips).toHaveLength(2);
    expect(chips[0].text()).toContain("RSD");
    expect(chips[1].text()).toContain("USD");
  });

  it("highlights the chip matching modelValue", () => {
    const wrapper = mountPicker({ modelValue: "USD" });
    const usdChip = wrapper.findAll(".currency-chip")[1];
    expect(usdChip.classes()).toContain("currency-chip-selected");
  });

  it("emits update:modelValue and persists last-used on click", async () => {
    const wrapper = mountPicker();
    const usdChip = wrapper.findAll(".currency-chip")[1];
    await usdChip.trigger("click");
    expect(wrapper.emitted("update:modelValue")[0][0]).toBe("USD");
    expect(localStorage.getItem("dinary.currency.lastUsed")).toBe("USD");
  });
});

describe("CurrencyPicker — manage / search", () => {
  it("toggles the manage panel and exposes a search input", async () => {
    const wrapper = mountPicker();
    expect(wrapper.find('input[aria-label="Currency search"]').exists()).toBe(
      false,
    );
    await wrapper.find(".currency-manage").trigger("click");
    expect(wrapper.find('input[aria-label="Currency search"]').exists()).toBe(
      true,
    );
  });

  it("filters world currencies by ISO code prefix and hides already-saved", async () => {
    const wrapper = mountPicker();
    await wrapper.find(".currency-manage").trigger("click");
    await wrapper.find('input[aria-label="Currency search"]').setValue("eu");
    const rows = wrapper.findAll(".currency-world-row");
    const codes = rows.map((r) => r.find(".currency-code").text());
    // 'EUR' matches; 'USD' (saved) is filtered out.
    expect(codes).toContain("EUR");
    expect(codes).not.toContain("USD");
  });

  it("finds currencies by their common symbol (e.g. 'KM' for BAM)", async () => {
    const wrapper = mountPicker();
    await wrapper.find(".currency-manage").trigger("click");
    await wrapper.find('input[aria-label="Currency search"]').setValue("km");
    const codes = wrapper
      .findAll(".currency-world-row")
      .map((r) => r.find(".currency-code").text());
    expect(codes).toContain("BAM");
  });

  it("matches Nordic 'kr' to all four krone-using currencies", async () => {
    const wrapper = mountPicker();
    await wrapper.find(".currency-manage").trigger("click");
    await wrapper.find('input[aria-label="Currency search"]').setValue("kr");
    const codes = wrapper
      .findAll(".currency-world-row")
      .map((r) => r.find(".currency-code").text());
    expect(codes).toEqual(expect.arrayContaining(["DKK", "ISK", "NOK", "SEK"]));
  });

  it("matches Polish 'zł' to PLN", async () => {
    const wrapper = mountPicker();
    await wrapper.find(".currency-manage").trigger("click");
    await wrapper.find('input[aria-label="Currency search"]').setValue("zł");
    const codes = wrapper
      .findAll(".currency-world-row")
      .map((r) => r.find(".currency-code").text());
    expect(codes).toContain("PLN");
  });

  it("calls addCurrency and selects the new code", async () => {
    const addSpy = vi
      .spyOn(currenciesApi, "addCurrency")
      .mockResolvedValue({
        codes: ["RSD", "USD", "CHF"],
        default_code: "RSD",
      });
    const wrapper = mountPicker();
    await wrapper.find(".currency-manage").trigger("click");
    await wrapper.find('input[aria-label="Currency search"]').setValue("CHF");
    await flushPromises();
    const row = wrapper.findAll(".currency-world-row")[0];
    expect(row.find(".currency-code").text()).toBe("CHF");
    await row.trigger("click");
    await flushPromises();
    expect(addSpy).toHaveBeenCalledWith("CHF");
    expect(wrapper.emitted("update:modelValue").at(-1)[0]).toBe("CHF");
  });
});

describe("CurrencyPicker — remove (manage mode)", () => {
  it("calls deleteCurrency on confirmed remove", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const delSpy = vi.spyOn(currenciesApi, "deleteCurrency").mockResolvedValue({
      codes: ["RSD"],
      default_code: "RSD",
    });
    const wrapper = mountPicker();
    await wrapper.find(".currency-manage").trigger("click");
    const removeBtns = wrapper.findAll(".currency-remove");
    // There is one row per saved code; the second row is USD,
    // which is removable (RSD is the default and is disabled).
    expect(removeBtns.length).toBe(2);
    expect(removeBtns[0].attributes("disabled")).toBeDefined();
    await removeBtns[1].trigger("click");
    await flushPromises();
    expect(delSpy).toHaveBeenCalledWith("USD");
  });

  it("does not delete the default currency", async () => {
    const delSpy = vi.spyOn(currenciesApi, "deleteCurrency").mockResolvedValue({
      codes: ["RSD"],
      default_code: "RSD",
    });
    const wrapper = mountPicker();
    await wrapper.find(".currency-manage").trigger("click");
    const removeBtns = wrapper.findAll(".currency-remove");
    // The first chip is RSD (default) and is button-disabled.
    expect(removeBtns[0].attributes("disabled")).toBeDefined();
    await removeBtns[0].trigger("click");
    expect(delSpy).not.toHaveBeenCalled();
  });
});

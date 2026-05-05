import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useCurrencyStore } from "../src/stores/currency.js";
import * as currenciesApi from "../src/api/currencies.js";

beforeEach(() => {
  setActivePinia(createPinia());
  try {
    localStorage.clear();
  } catch {
    /* happy-dom rare quota error */
  }
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useCurrencyStore — saved list", () => {
  it("load() populates codes and defaultCode from the API", async () => {
    vi.spyOn(currenciesApi, "fetchCurrencies").mockResolvedValue({
      codes: ["RSD", "USD"],
      default_code: "RSD",
    });
    const store = useCurrencyStore();
    await store.load();
    expect(store.codes).toEqual(["RSD", "USD"]);
    expect(store.defaultCode).toBe("RSD");
    expect(store.lastListError).toBeNull();
  });

  it("load() surfaces API errors via lastListError", async () => {
    const err = new Error("boom");
    vi.spyOn(currenciesApi, "fetchCurrencies").mockRejectedValue(err);
    const store = useCurrencyStore();
    await expect(store.load()).rejects.toThrow("boom");
    expect(store.lastListError).toBe(err);
  });

  it("addCurrency replaces codes with the server response", async () => {
    vi.spyOn(currenciesApi, "addCurrency").mockResolvedValue({
      codes: ["RSD", "USD"],
      default_code: "RSD",
    });
    const store = useCurrencyStore();
    await store.addCurrency("usd");
    expect(currenciesApi.addCurrency).toHaveBeenCalledWith("usd");
    expect(store.codes).toEqual(["RSD", "USD"]);
  });

  it("removeCurrency falls back to default when removing the last-used", async () => {
    vi.spyOn(currenciesApi, "deleteCurrency").mockResolvedValue({
      codes: ["RSD"],
      default_code: "RSD",
    });
    const store = useCurrencyStore();
    store.codes = ["RSD", "USD"];
    store.defaultCode = "RSD";
    store.setLastUsed("USD");
    await store.removeCurrency("USD");
    expect(store.codes).toEqual(["RSD"]);
    expect(store.lastUsed).toBe("RSD");
  });
});

describe("useCurrencyStore — preferredCode", () => {
  it("returns the lastUsed code when it is in the saved list", () => {
    const store = useCurrencyStore();
    store.codes = ["RSD", "EUR"];
    store.defaultCode = "RSD";
    store.setLastUsed("EUR");
    expect(store.preferredCode).toBe("EUR");
  });

  it("falls back to defaultCode when lastUsed is unknown to the picker", () => {
    const store = useCurrencyStore();
    store.codes = ["RSD"];
    store.defaultCode = "RSD";
    store.setLastUsed("EUR");
    expect(store.preferredCode).toBe("RSD");
  });

  it("falls back to RSD when nothing has been loaded", () => {
    const store = useCurrencyStore();
    store.codes = [];
    store.defaultCode = "";
    expect(store.preferredCode).toBe("RSD");
  });
});

describe("useCurrencyStore — lastUsed persistence", () => {
  it("setLastUsed writes the code to localStorage", () => {
    const store = useCurrencyStore();
    store.setLastUsed("usd");
    expect(localStorage.getItem("dinary.currency.lastUsed")).toBe("USD");
  });

  it("setLastUsed(null) clears the persisted value", () => {
    localStorage.setItem("dinary.currency.lastUsed", "USD");
    const store = useCurrencyStore();
    store.setLastUsed(null);
    expect(localStorage.getItem("dinary.currency.lastUsed")).toBeNull();
  });
});

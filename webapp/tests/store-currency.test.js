import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useCurrencyStore } from "../src/stores/currency.js";
import * as currenciesApi from "../src/api/currencies.js";

beforeEach(async () => {
  await allure.epic("Stores");
  await allure.feature("Currency store");
});

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

describe("useCurrencyStore — loadIfNeeded", () => {
  it("fetches from API when no prior timestamp exists", async () => {
    vi.spyOn(currenciesApi, "fetchCurrencies").mockResolvedValue({
      codes: ["RSD", "USD"],
      default_code: "RSD",
    });
    const store = useCurrencyStore();
    await store.loadIfNeeded();
    expect(currenciesApi.fetchCurrencies).toHaveBeenCalledOnce();
    expect(store.codes).toEqual(["RSD", "USD"]);
    expect(store.defaultCode).toBe("RSD");
    expect(store.lastListError).toBeNull();
  });

  it("skips the API call when data is fresh and codes are already loaded", async () => {
    localStorage.setItem("dinary:currency:fetchedAt", String(Date.now()));
    vi.spyOn(currenciesApi, "fetchCurrencies").mockResolvedValue({
      codes: ["RSD"],
      default_code: "RSD",
    });
    const store = useCurrencyStore();
    store.codes = ["RSD", "EUR"];
    await store.loadIfNeeded();
    expect(currenciesApi.fetchCurrencies).not.toHaveBeenCalled();
  });

  it("fetches even when timestamp is fresh if codes are empty (page reload scenario)", async () => {
    localStorage.setItem("dinary:currency:fetchedAt", String(Date.now()));
    vi.spyOn(currenciesApi, "fetchCurrencies").mockResolvedValue({
      codes: ["RSD"],
      default_code: "RSD",
    });
    const store = useCurrencyStore();
    // codes is [] — simulates a fresh in-memory store after page reload
    await store.loadIfNeeded();
    expect(currenciesApi.fetchCurrencies).toHaveBeenCalledOnce();
  });

  it("re-fetches when the TTL has expired", async () => {
    const yesterday = Date.now() - 25 * 60 * 60 * 1000;
    localStorage.setItem("dinary:currency:fetchedAt", String(yesterday));
    vi.spyOn(currenciesApi, "fetchCurrencies").mockResolvedValue({
      codes: ["RSD"],
      default_code: "RSD",
    });
    const store = useCurrencyStore();
    await store.loadIfNeeded();
    expect(currenciesApi.fetchCurrencies).toHaveBeenCalledOnce();
  });

  it("re-fetches when the dirty flag is set even if data is fresh", async () => {
    localStorage.setItem("dinary:currency:fetchedAt", String(Date.now()));
    localStorage.setItem("dinary:currency:dirty", "1");
    vi.spyOn(currenciesApi, "fetchCurrencies").mockResolvedValue({
      codes: ["RSD"],
      default_code: "RSD",
    });
    const store = useCurrencyStore();
    await store.loadIfNeeded();
    expect(currenciesApi.fetchCurrencies).toHaveBeenCalledOnce();
    expect(store.dirtyFlag).toBe(false);
  });

  it("stamps lastFetchedAt and clears dirtyFlag on success", async () => {
    vi.spyOn(currenciesApi, "fetchCurrencies").mockResolvedValue({
      codes: ["RSD"],
      default_code: "RSD",
    });
    const store = useCurrencyStore();
    const before = Date.now();
    await store.loadIfNeeded();
    expect(store.lastFetchedAt).toBeGreaterThanOrEqual(before);
    expect(store.dirtyFlag).toBe(false);
    expect(localStorage.getItem("dinary:currency:dirty")).toBeNull();
  });

  it("surfaces API errors via lastListError", async () => {
    const err = new Error("boom");
    vi.spyOn(currenciesApi, "fetchCurrencies").mockRejectedValue(err);
    const store = useCurrencyStore();
    await expect(store.loadIfNeeded()).rejects.toThrow("boom");
    expect(store.lastListError).toBe(err);
  });
});

describe("useCurrencyStore — addCurrency / removeCurrency", () => {
  it("addCurrency replaces codes with the server response and stamps timestamp", async () => {
    vi.spyOn(currenciesApi, "addCurrency").mockResolvedValue({
      codes: ["RSD", "USD"],
      default_code: "RSD",
    });
    const store = useCurrencyStore();
    const before = Date.now();
    await store.addCurrency("usd");
    expect(currenciesApi.addCurrency).toHaveBeenCalledWith("usd");
    expect(store.codes).toEqual(["RSD", "USD"]);
    expect(store.lastFetchedAt).toBeGreaterThanOrEqual(before);
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

  it("removeCurrency stamps timestamp", async () => {
    vi.spyOn(currenciesApi, "deleteCurrency").mockResolvedValue({
      codes: ["RSD"],
      default_code: "RSD",
    });
    const store = useCurrencyStore();
    store.codes = ["RSD", "EUR"];
    const before = Date.now();
    await store.removeCurrency("EUR");
    expect(store.lastFetchedAt).toBeGreaterThanOrEqual(before);
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

describe("useCurrencyStore — markDirty", () => {
  it("sets dirtyFlag and persists to localStorage", () => {
    const store = useCurrencyStore();
    store.markDirty();
    expect(store.dirtyFlag).toBe(true);
    expect(localStorage.getItem("dinary:currency:dirty")).toBe("1");
  });

  it("dirty flag read from localStorage on store init", () => {
    localStorage.setItem("dinary:currency:dirty", "1");
    const store = useCurrencyStore();
    expect(store.dirtyFlag).toBe(true);
  });
});

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useIncomeStore } from "../src/stores/income.js";
import * as incomeApi from "../src/api/income.js";
import { useToastStore } from "../src/stores/toast.js";

beforeEach(async () => {
  await allure.epic("Income");
  await allure.feature("Frontend");
});

beforeEach(() => {
  localStorage.clear();
  setActivePinia(createPinia());
});

afterEach(() => {
  vi.restoreAllMocks();
});

const ITEM_A = { id: 1, year: 2026, month: 5, income_date: "2026-05-15", amount: 540, currency: "EUR", amount_original: 540, currency_original: "EUR", comment: null };
const ITEM_B = { id: 2, year: 2026, month: 4, income_date: "2026-04-10", amount: 400, currency: "EUR", amount_original: 400, currency_original: "EUR", comment: null };

describe("income store: loadNextPage()", () => {
  it("appends items and advances page", async () => {
    vi.spyOn(incomeApi, "listIncomes").mockResolvedValueOnce({ items: [ITEM_A], has_more: false });
    const store = useIncomeStore();
    await store.loadNextPage();
    expect(store.items).toEqual([ITEM_A]);
    expect(store.page).toBe(1);
    expect(store.hasMore).toBe(false);
  });

  it("deduplicates by id across pages", async () => {
    vi.spyOn(incomeApi, "listIncomes")
      .mockResolvedValueOnce({ items: [ITEM_A], has_more: true })
      .mockResolvedValueOnce({ items: [ITEM_A, ITEM_B], has_more: false });
    const store = useIncomeStore();
    await store.loadNextPage();
    await store.loadNextPage();
    expect(store.items).toHaveLength(2);
  });

  it("does not fetch when loading is already in progress", async () => {
    const spy = vi.spyOn(incomeApi, "listIncomes").mockResolvedValue({ items: [], has_more: false });
    const store = useIncomeStore();
    store.loading = true;
    await store.loadNextPage();
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("income store: add()", () => {
  it("resets and reloads on success", async () => {
    vi.spyOn(incomeApi, "createIncome").mockResolvedValueOnce(ITEM_A);
    vi.spyOn(incomeApi, "listIncomes").mockResolvedValueOnce({ items: [ITEM_A], has_more: false });
    const store = useIncomeStore();
    store.items = [ITEM_B];
    store.page = 1;
    await store.add({ income_date: "2026-05-15", amount_original: 540, currency_original: "EUR" });
    expect(store.items).toEqual([ITEM_A]);
    expect(store.page).toBe(1);
  });

  it("shows error toast and rethrows on failure", async () => {
    vi.spyOn(incomeApi, "createIncome").mockRejectedValueOnce(new Error("network error"));
    const store = useIncomeStore();
    const toast = useToastStore();
    await expect(store.add({})).rejects.toThrow();
    expect(toast.visible).toBe(true);
  });
});

describe("income store: patch()", () => {
  it("resets and reloads on success", async () => {
    vi.spyOn(incomeApi, "updateIncome").mockResolvedValueOnce({ ...ITEM_A, amount: 600 });
    vi.spyOn(incomeApi, "listIncomes").mockResolvedValueOnce({ items: [{ ...ITEM_A, amount: 600 }], has_more: false });
    const store = useIncomeStore();
    await store.patch(1, { amount_original: 600, currency_original: "EUR" });
    expect(store.items[0].amount).toBe(600);
  });
});

describe("income store: remove()", () => {
  it("resets and reloads on success", async () => {
    vi.spyOn(incomeApi, "deleteIncome").mockResolvedValueOnce(undefined);
    vi.spyOn(incomeApi, "listIncomes").mockResolvedValueOnce({ items: [], has_more: false });
    const store = useIncomeStore();
    store.items = [ITEM_A];
    await store.remove(1);
    expect(store.items).toEqual([]);
  });
});

describe("income store: reset()", () => {
  it("clears items, page, and hasMore", () => {
    const store = useIncomeStore();
    store.items = [ITEM_A];
    store.page = 3;
    store.hasMore = false;
    store.reset();
    expect(store.items).toEqual([]);
    expect(store.page).toBe(0);
    expect(store.hasMore).toBe(true);
  });
});

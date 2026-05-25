import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useIncomeStore } from "../src/stores/income.js";
import * as incomeApi from "../src/api/income.js";
import { useToastStore } from "../src/stores/toast.js";

beforeEach(() => {
  localStorage.clear();
  setActivePinia(createPinia());
});

afterEach(() => {
  vi.restoreAllMocks();
});

const ITEM_A = { year: 2026, month: 5, amount: 540, currency: "EUR" };
const ITEM_B = { year: 2026, month: 4, amount: 400, currency: "EUR" };

describe("income store: loadNextPage()", () => {
  it("appends items and advances page", async () => {
    vi.spyOn(incomeApi, "listIncomes").mockResolvedValueOnce({ items: [ITEM_A], has_more: false });
    const store = useIncomeStore();
    await store.loadNextPage();
    expect(store.items).toEqual([ITEM_A]);
    expect(store.page).toBe(1);
    expect(store.hasMore).toBe(false);
  });

  it("deduplicates by year-month key across pages", async () => {
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
    await store.add({ year: 2026, month: 5, amount_original: 540, currency_original: "EUR" });
    expect(store.items).toEqual([ITEM_A]);
    expect(store.page).toBe(1);
  });

  it("shows 409 toast and rethrows on duplicate month", async () => {
    const err = Object.assign(new Error("conflict"), { status: 409 });
    vi.spyOn(incomeApi, "createIncome").mockRejectedValueOnce(err);
    const store = useIncomeStore();
    const toast = useToastStore();
    await expect(store.add({ year: 2026, month: 5, amount_original: 540, currency_original: "EUR" })).rejects.toThrow();
    expect(toast.message).toContain("already exists");
  });

  it("shows generic error toast on non-409 failure", async () => {
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
    await store.patch(2026, 5, { amount_original: 600, currency_original: "EUR" });
    expect(store.items[0].amount).toBe(600);
  });
});

describe("income store: remove()", () => {
  it("resets and reloads on success", async () => {
    vi.spyOn(incomeApi, "deleteIncome").mockResolvedValueOnce(undefined);
    vi.spyOn(incomeApi, "listIncomes").mockResolvedValueOnce({ items: [], has_more: false });
    const store = useIncomeStore();
    store.items = [ITEM_A];
    await store.remove(2026, 5);
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

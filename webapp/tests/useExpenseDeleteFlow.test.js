import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { ref, computed } from "vue";
import { createPinia, setActivePinia } from "pinia";
import { useExpenseDeleteFlow } from "../src/composables/useExpenseDeleteFlow.js";
import { useReviewStore } from "../src/stores/review.js";
import * as expensesApi from "../src/api/expenses.js";
import * as receiptsApi from "../src/api/receipts.js";

beforeEach(async () => {
  await allure.epic("Composables");
  await allure.feature("useExpenseDeleteFlow");
});

vi.mock("../src/composables/useOnline.js", () => ({
  useOnline: () => ({ isOnline: ref(true) }),
}));

function makeFlow(overrides = {}) {
  const expense = ref({ id: 42, receipt_id: null });
  const isManual = computed(() => expense.value?.receipt_id == null);
  const isReceiptBacked = computed(() => expense.value?.receipt_id != null);
  const onClose = vi.fn();

  const flow = useExpenseDeleteFlow({
    getExpense: () => expense.value,
    isManual,
    isReceiptBacked,
    onClose,
    ...overrides,
  });
  return { flow, expense, onClose };
}

beforeEach(() => {
  setActivePinia(createPinia());
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useExpenseDeleteFlow — openDeleteConfirm", () => {
  it("sets confirmingDelete to true", () => {
    const { flow } = makeFlow();
    flow.openDeleteConfirm();
    expect(flow.confirmingDelete.value).toBe(true);
  });

  it("fetches cascade when receipt-backed and cascade is null", async () => {
    vi.spyOn(receiptsApi, "getReceipt").mockResolvedValue({
      id: 7,
      merchant: "Maxi",
      captured_at: "2026-05-10T12:00:00",
      expenses: [],
      total: { amount: 0, currency: "RSD" },
    });

    const { flow, expense } = makeFlow();
    expense.value = { id: 10, receipt_id: 7 };

    flow.openDeleteConfirm();

    expect(receiptsApi.getReceipt).toHaveBeenCalledWith(7, { include: "expenses" });
  });

  it("does not re-fetch cascade when already loaded", () => {
    vi.spyOn(receiptsApi, "getReceipt").mockResolvedValue({});
    const { flow, expense } = makeFlow();
    expense.value = { id: 10, receipt_id: 7 };
    flow.cascade.value = { expenses: [] };

    flow.openDeleteConfirm();

    expect(receiptsApi.getReceipt).not.toHaveBeenCalled();
  });
});

describe("useExpenseDeleteFlow — confirmDelete (manual)", () => {
  it("calls deleteExpense and invokes onClose", async () => {
    vi.spyOn(expensesApi, "deleteExpense").mockResolvedValue(null);
    const reviewStore = useReviewStore();
    vi.spyOn(reviewStore, "deleteExpense").mockResolvedValue();

    const { flow, onClose } = makeFlow();
    flow.openDeleteConfirm();
    await flow.confirmDelete();

    expect(reviewStore.deleteExpense).toHaveBeenCalledWith(42);
    expect(onClose).toHaveBeenCalled();
  });

  it("clears confirmingDelete after delete", async () => {
    const reviewStore = useReviewStore();
    vi.spyOn(reviewStore, "deleteExpense").mockResolvedValue();

    const { flow } = makeFlow();
    flow.openDeleteConfirm();
    await flow.confirmDelete();

    expect(flow.confirmingDelete.value).toBe(false);
  });
});

describe("useExpenseDeleteFlow — confirmDelete (receipt-backed)", () => {
  it("calls deleteReceipt and invokes onClose", async () => {
    const reviewStore = useReviewStore();
    vi.spyOn(reviewStore, "deleteReceipt").mockResolvedValue();

    const { flow, expense } = makeFlow();
    expense.value = { id: 10, receipt_id: 7 };
    flow.cascade.value = { expenses: [{ id: 1 }, { id: 2 }] };

    flow.openDeleteConfirm();
    await flow.confirmDelete();

    expect(reviewStore.deleteReceipt).toHaveBeenCalledWith(7);
    expect(flow.onClose ?? flow).toBeTruthy();
  });
});

describe("useExpenseDeleteFlow — cancelDelete", () => {
  it("sets confirmingDelete to false", () => {
    const { flow } = makeFlow();
    flow.openDeleteConfirm();
    expect(flow.confirmingDelete.value).toBe(true);
    flow.cancelDelete();
    expect(flow.confirmingDelete.value).toBe(false);
  });
});

describe("useExpenseDeleteFlow — resetDeleteState", () => {
  it("clears confirmingDelete, cascade, and cascadeLoading", async () => {
    vi.spyOn(receiptsApi, "getReceipt").mockResolvedValue({
      id: 7,
      merchant: "Maxi",
      captured_at: "2026-05-10T12:00:00",
      expenses: [],
      total: { amount: 0, currency: "RSD" },
    });

    const { flow, expense } = makeFlow();
    expense.value = { id: 10, receipt_id: 7 };
    flow.openDeleteConfirm();
    flow.cascade.value = { expenses: [] };
    flow.confirmingDelete.value = true;

    flow.resetDeleteState();

    expect(flow.confirmingDelete.value).toBe(false);
    expect(flow.cascade.value).toBeNull();
    expect(flow.cascadeLoading.value).toBe(false);
  });
});

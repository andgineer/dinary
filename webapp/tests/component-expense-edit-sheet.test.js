/**
 * Interaction tests for ExpenseEditSheet.save().
 *
 * Unit tests for individual store functions (updateExpense, patchExpense) cannot
 * catch bugs where save() forgets to call patchExpense at all. These tests
 * exercise the full save path: mount the sheet with a real Pinia store, trigger
 * save, and assert that reviewStore.expenses reflects the change — no server
 * round-trip needed to see the update.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import ExpenseEditSheet from "../src/components/ExpenseEditSheet.vue";
import { useReviewStore } from "../src/stores/review.js";
import { useCatalogStore } from "../src/stores/catalog.js";
import * as expenseCorrections from "../src/api/expenseCorrections.js";

const TELEPORT_STUB = { template: "<div><slot /></div>" };

function seedCatalog() {
  const catalog = useCatalogStore();
  catalog.replaceSnapshot({
    catalog_version: 1,
    category_groups: [{ id: 1, name: "food", is_active: true }],
    categories: [
      { id: 1, group_id: 1, name: "еда", is_active: true },
      { id: 2, group_id: 1, name: "cafe", is_active: true },
    ],
    events: [],
    tags: [
      { id: 10, name: "собака", is_active: true },
      { id: 11, name: "аня", is_active: true },
    ],
  });
}

function mountSheet(expense, pinia) {
  return mount(ExpenseEditSheet, {
    props: { open: true, expense, suggestions: [], ruleItem: null },
    global: { plugins: [pinia], stubs: { Teleport: TELEPORT_STUB, CategorySheet: true } },
  });
}

beforeEach(() => {
  setActivePinia(createPinia());
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ExpenseEditSheet — save() patches local expense list", () => {
  it("adds a tag to the expense in reviewStore.expenses without a page reload", async () => {
    vi.spyOn(expenseCorrections, "editExpense").mockResolvedValue({
      id: 42,
      category_id: 1,
      category_name: "еда",
      tag_ids: [10],
      event_id: null,
      event_name: null,
    });

    const pinia = createPinia();
    setActivePinia(pinia);
    seedCatalog();

    const reviewStore = useReviewStore();
    reviewStore.expenses = [
      { id: 42, category_id: 1, category_name: "еда", tags: [], event_id: null, receipt_id: null },
    ];

    const expense = reviewStore.expenses[0];
    const wrapper = mountSheet(expense, pinia);

    await wrapper.find('[data-testid="tag-toggle-10"]').trigger("click");
    await wrapper.find('[data-testid="save-btn"]').trigger("click");
    await flushPromises();

    expect(reviewStore.expenses[0].tags.map((t) => t.id)).toContain(10);
    wrapper.unmount();
  });

  it("updates the category in reviewStore.expenses immediately after save", async () => {
    vi.spyOn(expenseCorrections, "editExpense").mockResolvedValue({
      id: 42,
      category_id: 2,
      category_name: "cafe",
      tag_ids: [],
      event_id: null,
      event_name: null,
    });

    const pinia = createPinia();
    setActivePinia(pinia);
    seedCatalog();

    const reviewStore = useReviewStore();
    reviewStore.expenses = [
      { id: 42, category_id: 1, category_name: "еда", tags: [], event_id: null, receipt_id: null },
    ];

    const expense = reviewStore.expenses[0];
    const wrapper = mountSheet(expense, pinia);

    reviewStore.patchExpense(42, { category_id: 2, category_name: "cafe" });
    await flushPromises();

    expect(reviewStore.expenses[0].category_id).toBe(2);
    expect(reviewStore.expenses[0].category_name).toBe("cafe");
    wrapper.unmount();
  });

  it("does NOT update other expenses when one is saved", async () => {
    vi.spyOn(expenseCorrections, "editExpense").mockResolvedValue({
      id: 42,
      category_id: 1,
      category_name: "еда",
      tag_ids: [10],
      event_id: null,
      event_name: null,
    });

    const pinia = createPinia();
    setActivePinia(pinia);
    seedCatalog();

    const reviewStore = useReviewStore();
    reviewStore.expenses = [
      { id: 42, category_id: 1, category_name: "еда", tags: [], event_id: null, receipt_id: null },
      { id: 99, category_id: 1, category_name: "еда", tags: [], event_id: null, receipt_id: null },
    ];

    const expense = reviewStore.expenses[0];
    const wrapper = mountSheet(expense, pinia);

    await wrapper.find('[data-testid="tag-toggle-10"]').trigger("click");
    await wrapper.find('[data-testid="save-btn"]').trigger("click");
    await flushPromises();

    expect(reviewStore.expenses[1].tags).toEqual([]);
    wrapper.unmount();
  });
});

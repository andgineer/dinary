import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { nextTick } from "vue";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import ExpenseEditSheet from "../src/components/ExpenseEditSheet.vue";
import { useCatalogStore } from "../src/stores/catalog.js";
import { useReviewStore } from "../src/stores/review.js";

beforeEach(async () => {
  await allure.epic("Expenses");
  await allure.feature("Frontend");
  await allure.story("ExpenseEditSheet");
});

const TELEPORT_STUB = { props: ["to"], template: "<div><slot /></div>" };

const CATALOG = {
  catalog_version: 1,
  category_groups: [{ id: 1, name: "Food", is_active: true }],
  categories: [
    { id: 10, group_id: 1, name: "groceries", is_active: true },
    { id: 11, group_id: 1, name: "cafe", is_active: true },
  ],
  events: [
    {
      id: 5,
      name: "Trip",
      date_from: "2026-01-01",
      date_to: "2026-12-31",
      auto_attach_enabled: false,
      is_active: true,
      auto_tags: [99],
    },
    {
      id: 6,
      name: "OldEvent",
      date_from: "2025-01-01",
      date_to: "2025-12-31",
      auto_attach_enabled: false,
      is_active: false,
      auto_tags: [],
    },
  ],
  tags: [
    { id: 1, name: "food", is_active: true },
    { id: 2, name: "health", is_active: true },
    { id: 3, name: "archived", is_active: false },
  ],
};

const EXPENSE = {
  id: 42,
  category_id: 10,
  tags: [{ id: 1, name: "food" }],
  event_id: null,
  receipt_id: null,
  has_rule: false,
  amount_original: 100,
  currency_original: "RSD",
};

function mountSheet(props = {}) {
  const pinia = createPinia();
  setActivePinia(pinia);
  useCatalogStore(pinia).replaceSnapshot(CATALOG);
  return {
    pinia,
    wrapper: mount(ExpenseEditSheet, {
      props: { open: true, expense: EXPENSE, suggestions: [], ...props },
      global: { plugins: [pinia], stubs: { Teleport: TELEPORT_STUB, CategorySheet: true } },
    }),
  };
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ExpenseEditSheet — pre-fill", () => {
  it("pre-fills category from expense.category_id", () => {
    const { wrapper } = mountSheet();
    const chip = wrapper.find('[data-testid="category-chip"]');
    expect(chip.text()).toContain("groceries");
  });

  it("pre-fills active tags from expense.tags", () => {
    const { wrapper } = mountSheet();
    const tagToggle = wrapper.find('[data-testid="tag-toggle-1"]');
    expect(tagToggle.classes()).toContain("is-on");
    const tagToggle2 = wrapper.find('[data-testid="tag-toggle-2"]');
    expect(tagToggle2.classes()).not.toContain("is-on");
  });

  it("pre-fills comment from expense.comment", () => {
    const { wrapper } = mountSheet({ expense: { ...EXPENSE, comment: "business trip" } });
    const input = wrapper.find('[data-testid="comment-input"]');
    expect(input.element.value).toBe("business trip");
  });

  it("leaves comment empty when expense.comment is absent", () => {
    const { wrapper } = mountSheet();
    const input = wrapper.find('[data-testid="comment-input"]');
    expect(input.element.value).toBe("");
  });
});

describe("ExpenseEditSheet — tag toggle", () => {
  it("toggles tag on when clicked off", async () => {
    const { wrapper } = mountSheet();
    const toggle = wrapper.find('[data-testid="tag-toggle-2"]');
    expect(toggle.classes()).not.toContain("is-on");
    await toggle.trigger("click");
    expect(toggle.classes()).toContain("is-on");
  });

  it("removes tag when clicked on", async () => {
    const { wrapper } = mountSheet();
    const toggle = wrapper.find('[data-testid="tag-toggle-1"]');
    expect(toggle.classes()).toContain("is-on");
    await toggle.trigger("click");
    expect(toggle.classes()).not.toContain("is-on");
  });
});

describe("ExpenseEditSheet — event selection merges auto_tags", () => {
  it("adds event auto_tags to selectedTagIds when event is selected", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const reviewStore = useReviewStore(pinia);
    const spy = vi.spyOn(reviewStore, "updateExpense").mockResolvedValue();

    const wrapper = mount(ExpenseEditSheet, {
      props: { open: true, expense: EXPENSE, suggestions: [] },
      global: { plugins: [pinia], stubs: { Teleport: TELEPORT_STUB, CategorySheet: true } },
    });

    const select = wrapper.find('[data-testid="event-select"]');
    await select.setValue("5");
    await select.trigger("change");
    await wrapper.find('[data-testid="save-btn"]').trigger("click");
    await flushPromises();

    expect(spy).toHaveBeenCalledTimes(1);
    const payload = spy.mock.calls[0][1];
    expect(payload.tag_ids).toContain(99);
  });
});

describe("ExpenseEditSheet — inactive items visibility", () => {
  it("shows inactive tag in toggle row when it is attached to the expense", () => {
    const { wrapper } = mountSheet({
      expense: { ...EXPENSE, tags: [{ id: 3, name: "archived" }] },
    });
    expect(wrapper.find('[data-testid="tag-toggle-3"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="tag-toggle-3"]').classes()).toContain("is-on");
  });

  it("does not show inactive tag when it is not attached to the expense", () => {
    const { wrapper } = mountSheet();
    expect(wrapper.find('[data-testid="tag-toggle-3"]').exists()).toBe(false);
  });

  it("shows inactive event option when it is attached to the expense", () => {
    const { wrapper } = mountSheet({
      expense: { ...EXPENSE, event_id: 6, event_name: "OldEvent" },
    });
    const options = wrapper.find('[data-testid="event-select"]').findAll("option");
    expect(options.some((o) => o.element.value === "6")).toBe(true);
  });

  it("does not show inactive event option when it is not attached to the expense", () => {
    const { wrapper } = mountSheet();
    const options = wrapper.find('[data-testid="event-select"]').findAll("option");
    expect(options.some((o) => o.element.value === "6")).toBe(false);
  });
});

describe("ExpenseEditSheet — event removal clears auto_tags", () => {
  async function mountWithSpy() {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const reviewStore = useReviewStore(pinia);
    const spy = vi.spyOn(reviewStore, "updateExpense").mockResolvedValue();
    const wrapper = mount(ExpenseEditSheet, {
      props: { open: true, expense: EXPENSE, suggestions: [] },
      global: { plugins: [pinia], stubs: { Teleport: TELEPORT_STUB, CategorySheet: true } },
    });
    return { wrapper, spy };
  }

  it("removes event auto_tags when event is cleared", async () => {
    const { wrapper, spy } = await mountWithSpy();
    const select = wrapper.find('[data-testid="event-select"]');
    await select.setValue("5");
    await select.trigger("change");
    await select.setValue("");
    await select.trigger("change");
    await wrapper.find('[data-testid="save-btn"]').trigger("click");
    await flushPromises();
    expect(spy.mock.calls[0][1].tag_ids).not.toContain(99);
  });

  it("removes previous event auto_tags when switching to another event", async () => {
    const CATALOG_TWO_EVENTS = {
      ...CATALOG,
      events: [
        { id: 5, name: "Trip", date_from: "2026-01-01", date_to: "2026-12-31", is_active: true, auto_tags: [99] },
        { id: 7, name: "Other", date_from: "2026-01-01", date_to: "2026-12-31", is_active: true, auto_tags: [88] },
      ],
      tags: [...CATALOG.tags, { id: 88, name: "other-tag", is_active: true }],
    };
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG_TWO_EVENTS);
    const reviewStore = useReviewStore(pinia);
    const spy = vi.spyOn(reviewStore, "updateExpense").mockResolvedValue();
    const wrapper = mount(ExpenseEditSheet, {
      props: { open: true, expense: EXPENSE, suggestions: [] },
      global: { plugins: [pinia], stubs: { Teleport: TELEPORT_STUB, CategorySheet: true } },
    });
    const select = wrapper.find('[data-testid="event-select"]');
    await select.setValue("5");
    await select.trigger("change");
    await select.setValue("7");
    await select.trigger("change");
    await wrapper.find('[data-testid="save-btn"]').trigger("click");
    await flushPromises();
    const tag_ids = spy.mock.calls[0][1].tag_ids;
    expect(tag_ids).not.toContain(99);
    expect(tag_ids).toContain(88);
  });
});

describe("ExpenseEditSheet — event selector reactivity after catalog changes", () => {
  it("inactive event is not shown in selector when not attached to expense", () => {
    const { wrapper } = mountSheet();
    const options = wrapper.find('[data-testid="event-select"]').findAll("option");
    expect(options.some((o) => o.element.value === "6")).toBe(false);
  });

  it("reactivated event (outside ±30d window) appears in selector without page reload", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const catalog = useCatalogStore(pinia);
    catalog.replaceSnapshot(CATALOG);
    const wrapper = mount(ExpenseEditSheet, {
      props: { open: true, expense: EXPENSE, suggestions: [] },
      global: { plugins: [pinia], stubs: { Teleport: TELEPORT_STUB, CategorySheet: true } },
    });

    let options = wrapper.find('[data-testid="event-select"]').findAll("option");
    expect(options.some((o) => o.element.value === "6")).toBe(false);

    catalog.replaceSnapshot({
      ...CATALOG,
      events: [
        CATALOG.events.find((e) => e.id === 5),
        { id: 6, name: "OldEvent", date_from: "2025-01-01", date_to: "2025-12-31", auto_attach_enabled: false, is_active: true },
      ],
    });
    await nextTick();

    options = wrapper.find('[data-testid="event-select"]').findAll("option");
    expect(options.some((o) => o.element.value === "6")).toBe(true);
  });

  it("deactivated event disappears from selector without page reload", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const catalog = useCatalogStore(pinia);
    catalog.replaceSnapshot(CATALOG);
    const wrapper = mount(ExpenseEditSheet, {
      props: { open: true, expense: EXPENSE, suggestions: [] },
      global: { plugins: [pinia], stubs: { Teleport: TELEPORT_STUB, CategorySheet: true } },
    });

    let options = wrapper.find('[data-testid="event-select"]').findAll("option");
    expect(options.some((o) => o.element.value === "5")).toBe(true);

    catalog.replaceSnapshot({
      ...CATALOG,
      events: [
        { id: 5, name: "Trip", date_from: "2026-01-01", date_to: "2026-12-31", auto_attach_enabled: false, is_active: false, auto_tags: [99] },
        CATALOG.events.find((e) => e.id === 6),
      ],
    });
    await nextTick();

    options = wrapper.find('[data-testid="event-select"]').findAll("option");
    expect(options.some((o) => o.element.value === "5")).toBe(false);
  });
});

describe("ExpenseEditSheet — scope selector", () => {
  it("hides scope selector when receipt_id is null", () => {
    const { wrapper } = mountSheet({ expense: { ...EXPENSE, receipt_id: null } });
    expect(wrapper.find('[data-testid="scope-selector"]').exists()).toBe(false);
  });

  it("shows scope selector when receipt_id is set", () => {
    const { wrapper } = mountSheet({ expense: { ...EXPENSE, receipt_id: 7 } });
    expect(wrapper.find('[data-testid="scope-selector"]').exists()).toBe(true);
  });
});

describe("ExpenseEditSheet — update rule checkbox", () => {
  it("hides checkbox when has_rule is false", () => {
    const { wrapper } = mountSheet({ expense: { ...EXPENSE, has_rule: false } });
    expect(wrapper.find('[data-testid="update-rule-wrap"]').exists()).toBe(false);
  });

  it("shows checkbox when has_rule is true and receipt_id is set", () => {
    const { wrapper } = mountSheet({ expense: { ...EXPENSE, has_rule: true, receipt_id: 7 } });
    expect(wrapper.find('[data-testid="update-rule-wrap"]').exists()).toBe(true);
  });

  it("hides checkbox when has_rule is true but receipt_id is null (manual expense)", () => {
    const { wrapper } = mountSheet({ expense: { ...EXPENSE, has_rule: true, receipt_id: null } });
    expect(wrapper.find('[data-testid="update-rule-wrap"]').exists()).toBe(false);
  });
});

describe("ExpenseEditSheet — save (expense path)", () => {
  it("calls reviewStore.updateExpense with correct payload and emits close", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const reviewStore = useReviewStore(pinia);
    const spy = vi.spyOn(reviewStore, "updateExpense").mockResolvedValue();

    const wrapper = mount(ExpenseEditSheet, {
      props: { open: true, expense: EXPENSE, suggestions: [] },
      global: { plugins: [pinia], stubs: { Teleport: TELEPORT_STUB, CategorySheet: true } },
    });

    await wrapper.find('[data-testid="save-btn"]').trigger("click");
    await flushPromises();

    expect(spy).toHaveBeenCalledWith(42, expect.objectContaining({ category_id: 10 }));
    expect(wrapper.emitted("close")).toBeTruthy();
  });

  it("includes edited comment in the update payload", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const reviewStore = useReviewStore(pinia);
    const spy = vi.spyOn(reviewStore, "updateExpense").mockResolvedValue();

    const wrapper = mount(ExpenseEditSheet, {
      props: { open: true, expense: EXPENSE, suggestions: [] },
      global: { plugins: [pinia], stubs: { Teleport: TELEPORT_STUB, CategorySheet: true } },
    });

    await wrapper.find('[data-testid="comment-input"]').setValue("updated comment");
    await wrapper.find('[data-testid="save-btn"]').trigger("click");
    await flushPromises();

    expect(spy).toHaveBeenCalledWith(42, expect.objectContaining({ comment: "updated comment" }));
  });
});

describe("ExpenseEditSheet — save (rule item path)", () => {
  const RULE_ITEM = {
    id: 7,
    expense_id: 42,
    category_id: 11,
    tags: [{ id: 2, name: "health" }],
  };

  it("calls correct() then updateExpense() with tag_ids and update_rule, and emits close", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const reviewStore = useReviewStore(pinia);
    const correctSpy = vi.spyOn(reviewStore, "correct").mockResolvedValue();
    const updateSpy = vi.spyOn(reviewStore, "updateExpense").mockResolvedValue();

    const wrapper = mount(ExpenseEditSheet, {
      props: { open: true, expense: null, suggestions: [], ruleItem: RULE_ITEM },
      global: { plugins: [pinia], stubs: { Teleport: TELEPORT_STUB, CategorySheet: true } },
    });

    await wrapper.find('[data-testid="save-btn"]').trigger("click");
    await flushPromises();

    expect(correctSpy).toHaveBeenCalledWith(RULE_ITEM, 11, "all");
    expect(updateSpy).toHaveBeenCalledWith(
      42,
      expect.objectContaining({ tag_ids: expect.arrayContaining([2]), update_rule: true }),
    );
    expect(wrapper.emitted("close")).toBeTruthy();
  });

  it("uses ruleItem.id as expenseId when expense_id is absent", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const reviewStore = useReviewStore(pinia);
    vi.spyOn(reviewStore, "correct").mockResolvedValue();
    const updateSpy = vi.spyOn(reviewStore, "updateExpense").mockResolvedValue();

    const wrapper = mount(ExpenseEditSheet, {
      props: { open: true, expense: null, suggestions: [], ruleItem: { id: 99, category_id: 10, tags: [] } },
      global: { plugins: [pinia], stubs: { Teleport: TELEPORT_STUB, CategorySheet: true } },
    });

    await wrapper.find('[data-testid="save-btn"]').trigger("click");
    await flushPromises();

    expect(updateSpy).toHaveBeenCalledWith(99, expect.objectContaining({ update_rule: true }));
  });
});

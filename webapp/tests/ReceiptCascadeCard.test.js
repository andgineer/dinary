import { beforeEach, afterEach, describe, it, expect, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import ReceiptCascadeCard from "../src/components/ReceiptCascadeCard.vue";
import { useReviewStore } from "../src/stores/review.js";
import { useToastStore } from "../src/stores/toast.js";

beforeEach(async () => {
  await allure.epic("Receipts");
  await allure.feature("Frontend");
  await allure.story("ReceiptCascadeCard");
});

beforeEach(() => {
  setActivePinia(createPinia());
});

afterEach(() => {
  vi.restoreAllMocks();
});

const TELEPORT_STUB = { props: ["to"], template: "<div><slot /></div>" };

const CategorySheetStub = {
  name: "CategorySheet",
  props: ["open", "title", "suggestions"],
  emits: ["select", "close"],
  template:
    '<div v-if="open" data-testid="category-sheet-stub">' +
    '<button data-testid="pick-category" type="button" @click="$emit(\'select\', 5)">pick</button>' +
    "</div>",
};

function mountCard(props) {
  return mount(ReceiptCascadeCard, {
    props,
    global: { stubs: { Teleport: TELEPORT_STUB, CategorySheet: CategorySheetStub } },
  });
}

const CASCADE = {
  id: 7,
  merchant: "Maxi",
  captured_at: "2026-05-10T12:00:00",
  expenses: [
    { id: 1, item_name: "hleb", amount: 100, currency: "RSD" },
    { id: 2, item_name: "mleko", amount: 80, currency: "RSD" },
  ],
  total: { amount: 180, currency: "RSD" },
  job: null,
};

describe("ReceiptCascadeCard — loading state", () => {
  it("shows loading text when loading is true", () => {
    const wrapper = mountCard({ loading: true, cascade: null });
    expect(wrapper.text()).toContain("Loading");
  });

  it("hides cascade content while loading", () => {
    const wrapper = mountCard({ loading: true, cascade: CASCADE });
    expect(wrapper.find(".cascade-header").exists()).toBe(false);
  });
});

describe("ReceiptCascadeCard — cascade data", () => {
  it("shows merchant name", () => {
    const wrapper = mountCard({ loading: false, cascade: CASCADE });
    expect(wrapper.find(".cascade-merchant").text()).toBe("Maxi");
  });

  it("renders one row per expense", () => {
    const wrapper = mountCard({ loading: false, cascade: CASCADE });
    expect(wrapper.findAll(".cascade-row")).toHaveLength(2);
  });

  it("shows item names in rows", () => {
    const wrapper = mountCard({ loading: false, cascade: CASCADE });
    const names = wrapper.findAll(".cascade-item-name").map((el) => el.text());
    expect(names).toContain("hleb");
    expect(names).toContain("mleko");
  });

  it("shows formatted total", () => {
    const wrapper = mountCard({ loading: false, cascade: CASCADE });
    expect(wrapper.find(".cascade-total-amount").text()).toContain("180.00");
  });

  it("falls back to 'Receipt' when merchant is empty", () => {
    const wrapper = mountCard({ loading: false, cascade: { ...CASCADE, merchant: "" } });
    expect(wrapper.find(".cascade-merchant").text()).toBe("Receipt");
  });
});

describe("ReceiptCascadeCard — no data", () => {
  it("renders the card container even with no cascade and no loading", () => {
    const wrapper = mountCard({ loading: false, cascade: null });
    expect(wrapper.find("[data-testid='cascade-card']").exists()).toBe(true);
    expect(wrapper.find(".cascade-header").exists()).toBe(false);
  });
});

function sqliteTimestamp(date) {
  return date.toISOString().slice(0, 19).replace("T", " ");
}

describe("ReceiptCascadeCard — job error banner", () => {
  it("renders no banner when job is null", () => {
    const wrapper = mountCard({ loading: false, cascade: CASCADE });
    expect(wrapper.find('[data-testid="job-banner"]').exists()).toBe(false);
  });

  it("poisoned: shows red banner with full last_error and an active resolve button", () => {
    const lastError =
      "No items found via /specifications or journal for https://suf.purs.gov.rs/v/?vl=AAAA"
      + " — receipt may not be indexed by SUF yet";
    const wrapper = mountCard({
      loading: false,
      cascade: {
        ...CASCADE,
        job: {
          status: "poisoned",
          retry_count: 4,
          last_error: lastError,
          retry_after: null,
          last_attempted_at: sqliteTimestamp(new Date(Date.now() - 60_000)),
        },
      },
    });
    const banner = wrapper.find('[data-testid="job-banner"]');
    expect(banner.classes()).toContain("job-banner--error");
    expect(banner.text()).toContain("Automatic processing failed");
    expect(banner.text()).toContain(lastError);
    expect(banner.text()).toContain("Tried 4 times");
    expect(wrapper.find('[data-testid="job-resolve-btn"]').exists()).toBe(true);
  });

  it("pending: shows amber banner with retry_after and an active resolve button", () => {
    const wrapper = mountCard({
      loading: false,
      cascade: {
        ...CASCADE,
        job: {
          status: "pending",
          retry_count: 2,
          last_error: null,
          retry_after: "2026-06-10 12:00:00",
          last_attempted_at: sqliteTimestamp(new Date(Date.now() - 60_000)),
        },
      },
    });
    const banner = wrapper.find('[data-testid="job-banner"]');
    expect(banner.classes()).toContain("job-banner--warning");
    expect(banner.text()).toContain("Waiting to retry");
    expect(banner.text()).toContain("Tried 2 times");
    expect(wrapper.find('[data-testid="job-resolve-btn"]').exists()).toBe(true);
  });

  it("in_progress (recent): shows neutral spinner and no resolve button", () => {
    const wrapper = mountCard({
      loading: false,
      cascade: {
        ...CASCADE,
        job: {
          status: "in_progress",
          retry_count: 1,
          last_error: null,
          retry_after: null,
          last_attempted_at: sqliteTimestamp(new Date(Date.now() - 60_000)),
        },
      },
    });
    const banner = wrapper.find('[data-testid="job-banner"]');
    expect(banner.classes()).toContain("job-banner--neutral");
    expect(banner.text()).toContain("Processing…");
    expect(banner.find(".job-spinner").exists()).toBe(true);
    expect(wrapper.find('[data-testid="job-resolve-btn"]').exists()).toBe(false);
    expect(banner.text()).not.toContain("appears stuck");
  });

  it("in_progress (stuck > 5min): shows 'appears stuck' warning and an active resolve button", () => {
    const wrapper = mountCard({
      loading: false,
      cascade: {
        ...CASCADE,
        job: {
          status: "in_progress",
          retry_count: 3,
          last_error: null,
          retry_after: null,
          last_attempted_at: sqliteTimestamp(new Date(Date.now() - 6 * 60_000)),
        },
      },
    });
    const banner = wrapper.find('[data-testid="job-banner"]');
    expect(banner.text()).toContain("appears stuck");
    expect(wrapper.find('[data-testid="job-resolve-btn"]').exists()).toBe(true);
  });
});

describe("ReceiptCascadeCard — resolve flow", () => {
  function poisonedCascade() {
    return {
      ...CASCADE,
      job: {
        status: "poisoned",
        retry_count: 4,
        last_error: "boom",
        retry_after: null,
        last_attempted_at: sqliteTimestamp(new Date(Date.now() - 60_000)),
      },
    };
  }

  it("opens the category picker and resolves the receipt on select", async () => {
    const review = useReviewStore();
    const resolveSpy = vi.spyOn(review, "resolveStuckReceipt").mockResolvedValue();
    const wrapper = mountCard({ loading: false, cascade: poisonedCascade() });

    await wrapper.find('[data-testid="job-resolve-btn"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="category-sheet-stub"]').exists()).toBe(true);

    await wrapper.find('[data-testid="pick-category"]').trigger("click");
    await flushPromises();

    expect(resolveSpy).toHaveBeenCalledWith(7, { categoryId: 5 });
    expect(wrapper.emitted("resolved")).toBeTruthy();
  });

  it("shows a toast and emits resolved on 409 (worker finished first)", async () => {
    const review = useReviewStore();
    vi.spyOn(review, "resolveStuckReceipt").mockRejectedValue(
      Object.assign(new Error("Receipt already resolved"), { status: 409 }),
    );
    const toast = useToastStore();
    const showSpy = vi.spyOn(toast, "show");
    const wrapper = mountCard({ loading: false, cascade: poisonedCascade() });

    await wrapper.find('[data-testid="job-resolve-btn"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-testid="pick-category"]').trigger("click");
    await flushPromises();

    expect(showSpy).toHaveBeenCalledWith("Receipt was processed automatically", "info");
    expect(wrapper.emitted("resolved")).toBeTruthy();
  });
});

import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import ReviewView from "../src/views/ReviewView.vue";
import { useReviewStore } from "../src/stores/review.js";
import { useCatalogStore } from "../src/stores/catalog.js";

const FEED_PAGE_1 = {
  doubtful_count: 2,
  has_more: false,
  items: [
    {
      id: 1,
      is_doubtful: true,
      name: "Chocolate bar",
      store: "Lidl",
      total: 150,
      currency: "RSD",
      count: 3,
      confidence_level: 3,
      current_category_id: 10,
      suggested_category_id: 10,
      datetime: "2026-05-10T10:00:00",
    },
    {
      id: 2,
      is_doubtful: false,
      store: "Maxi",
      items_count: 8,
      total: 2000,
      currency: "RSD",
      datetime: "2026-05-09T14:00:00",
      top_categories: [{ id: 10, n: 5 }],
    },
  ],
};

const CATALOG = {
  catalog_version: 1,
  category_groups: [{ id: 1, name: "Food", is_active: true }],
  categories: [{ id: 10, group_id: 1, name: "groceries", is_active: true }],
  events: [],
  tags: [],
};

const TELEPORT_STUB = { props: ["to", "disabled"], template: "<div><slot /></div>" };

function mountView(pinia) {
  return mount(ReviewView, {
    global: { plugins: [pinia], stubs: { Teleport: TELEPORT_STUB } },
  });
}

let observerCallback = null;
let observerInstance = null;

beforeEach(() => {
  const pinia = createPinia();
  setActivePinia(pinia);

  observerCallback = null;
  observerInstance = { observe: vi.fn(), disconnect: vi.fn() };
  globalThis.IntersectionObserver = vi.fn((cb) => {
    observerCallback = cb;
    return observerInstance;
  });
});

afterEach(() => {
  delete globalThis.IntersectionObserver;
});

function mockOnLine(value) {
  const ownBefore = Object.getOwnPropertyDescriptor(navigator, "onLine");
  Object.defineProperty(navigator, "onLine", { configurable: true, get: () => value });
  return () => {
    if (ownBefore) {
      Object.defineProperty(navigator, "onLine", ownBefore);
    } else {
      delete navigator.onLine;
    }
  };
}

describe("ReviewView offline", () => {
  it("does not call loadNextPage when offline on mount", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const restore = mockOnLine(false);
    try {
      const review = useReviewStore(pinia);
      const spy = vi.spyOn(review, "loadNextPage").mockResolvedValue();
      const wrapper = mountView(pinia);
      await flushPromises();
      expect(spy).not.toHaveBeenCalled();
      wrapper.unmount();
    } finally {
      restore();
    }
  });

  it("does not trigger loadNextPage from IntersectionObserver when offline", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const restore = mockOnLine(false);
    try {
      const review = useReviewStore(pinia);
      const spy = vi.spyOn(review, "loadNextPage").mockResolvedValue();
      const wrapper = mountView(pinia);
      await flushPromises();

      review.hasMore = true;
      observerCallback?.([{ isIntersecting: true }]);
      await flushPromises();

      expect(spy).not.toHaveBeenCalled();
      wrapper.unmount();
    } finally {
      restore();
    }
  });
});

describe("ReviewView", () => {
  it("renders the view container", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const review = useReviewStore(pinia);
    vi.spyOn(review, "loadNextPage").mockResolvedValue();
    const wrapper = mountView(pinia);
    await flushPromises();
    expect(wrapper.find('[data-testid="review-view"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it("shows doubtful and certain rows after loading", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const catalog = useCatalogStore(pinia);
    catalog.replaceSnapshot(CATALOG);
    const review = useReviewStore(pinia);
    vi.spyOn(review, "loadNextPage").mockImplementation(async () => {
      review.items = FEED_PAGE_1.items;
      review.doubtfulCount = FEED_PAGE_1.doubtful_count;
      review.hasMore = FEED_PAGE_1.has_more;
      review.page = 1;
      review.totalLoaded = FEED_PAGE_1.items.length;
    });
    const wrapper = mountView(pinia);
    await flushPromises();
    expect(wrapper.find('[data-testid="doubtful-row"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="certain-row"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it("shows skeleton rows while loading", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const review = useReviewStore(pinia);
    vi.spyOn(review, "loadNextPage").mockImplementation(
      () => new Promise(() => {}),
    );
    review.loading = true;
    const wrapper = mountView(pinia);
    await flushPromises();
    expect(wrapper.find('[aria-label="Loading"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it("calls loadNextPage when sentinel intersects with more items available", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const review = useReviewStore(pinia);
    let callCount = 0;
    vi.spyOn(review, "loadNextPage").mockImplementation(async () => {
      callCount += 1;
      review.items = FEED_PAGE_1.items;
      review.doubtfulCount = FEED_PAGE_1.doubtful_count;
      review.hasMore = true;
      review.page = callCount;
      review.totalLoaded = FEED_PAGE_1.items.length;
    });
    const wrapper = mountView(pinia);
    await flushPromises();
    expect(callCount).toBe(1);

    review.loading = false;
    observerCallback?.([{ isIntersecting: true }]);
    await flushPromises();
    expect(callCount).toBe(2);
    wrapper.unmount();
  });

  it("does not call loadNextPage when sentinel intersects but hasMore is false", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    useCatalogStore(pinia).replaceSnapshot(CATALOG);
    const review = useReviewStore(pinia);
    let callCount = 0;
    vi.spyOn(review, "loadNextPage").mockImplementation(async () => {
      callCount += 1;
      review.items = FEED_PAGE_1.items;
      review.hasMore = false;
      review.page = 1;
      review.totalLoaded = FEED_PAGE_1.items.length;
    });
    const wrapper = mountView(pinia);
    await flushPromises();
    expect(callCount).toBe(1);

    review.loading = false;
    observerCallback?.([{ isIntersecting: true }]);
    await flushPromises();
    expect(callCount).toBe(1);
    wrapper.unmount();
  });

  it("shows end marker after all items are loaded", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const catalog = useCatalogStore(pinia);
    catalog.replaceSnapshot(CATALOG);
    const review = useReviewStore(pinia);
    vi.spyOn(review, "loadNextPage").mockImplementation(async () => {
      review.items = FEED_PAGE_1.items;
      review.doubtfulCount = FEED_PAGE_1.doubtful_count;
      review.hasMore = false;
      review.page = 1;
      review.totalLoaded = FEED_PAGE_1.items.length;
    });
    const wrapper = mountView(pinia);
    await flushPromises();
    expect(wrapper.text()).toContain("end ·");
    wrapper.unmount();
  });
});

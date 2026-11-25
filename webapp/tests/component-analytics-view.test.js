import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import AnalyticsView from "../src/views/AnalyticsView.vue";
import { useAnalyticsStore } from "../src/stores/analytics.js";
import * as analyticsApi from "../src/api/analytics.js";
import { useToastStore } from "../src/stores/toast.js";

beforeEach(async () => {
  await allure.epic("Analytics");
  await allure.feature("Frontend");
  await allure.story("AnalyticsView");
});

const SUMMARY = {
  this_month_total: "100",
  last_month_total: "90",
  ytd_total: "900",
  ytd_savings: "300",
  savings_rate: "25%",
  currency: "RSD",
};
const EVENTS = [
  { id: 1, name: "trip", date_range: "1–3 May 2026", total: "400", currency: "RSD", open: false },
  { id: 2, name: "future", date_range: "1–9 Dec 2026", total: "0", currency: "RSD", open: true },
];
const DETAIL = {
  ...EVENTS[0],
  categories: [
    { category_id: 1, category_name: "food", group_name: "Food", total: "300", share: 0.75, currency: "RSD" },
    { category_id: 9, category_name: "orphan", group_name: null, total: "100", share: 0.25, currency: "RSD" },
  ],
  days: [
    { date: "2026-05-03", date_label: "3 May", total: "150", currency: "RSD" },
    { date: "2026-05-01", date_label: "1 May", total: "250", currency: "RSD" },
  ],
};
const EMPTY_DETAIL = { ...EVENTS[1], categories: [], days: [] };

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

function seedFreshCache() {
  localStorage.setItem("dinary:analytics:fetchedAt", String(Date.now() - 60_000));
  localStorage.setItem(
    "dinary:analytics:v2",
    JSON.stringify({ summary: SUMMARY, events: EVENTS, trends: null }),
  );
}

async function mountView() {
  const pinia = createPinia();
  setActivePinia(pinia);
  const wrapper = mount(AnalyticsView, { global: { plugins: [pinia] } });
  await flushPromises();
  return wrapper;
}

function toggles(wrapper) {
  return wrapper.findAll("button.event-toggle");
}

let restoreOnLine = () => {};

beforeEach(() => {
  localStorage.clear();
  seedFreshCache();
  vi.spyOn(analyticsApi, "fetchAnalyticsSummary").mockRejectedValue(new Error("unexpected summary fetch"));
});

afterEach(() => {
  restoreOnLine();
  restoreOnLine = () => {};
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("AnalyticsView — event drill-down", () => {
  it("renders events collapsed and fetches nothing until expanded", async () => {
    restoreOnLine = mockOnLine(true);
    const spy = vi.spyOn(analyticsApi, "fetchEventDetail").mockResolvedValue(DETAIL);
    const wrapper = await mountView();
    expect(toggles(wrapper)).toHaveLength(2);
    for (const btn of toggles(wrapper)) expect(btn.attributes("aria-expanded")).toBe("false");
    expect(wrapper.find(".event-detail").exists()).toBe(false);
    expect(spy).not.toHaveBeenCalled();
  });

  it("expands a row, points aria-controls at the panel and renders both breakdowns", async () => {
    restoreOnLine = mockOnLine(true);
    vi.spyOn(analyticsApi, "fetchEventDetail").mockResolvedValue(DETAIL);
    const wrapper = await mountView();
    const btn = toggles(wrapper)[0];
    await btn.trigger("click");
    await flushPromises();

    expect(btn.attributes("aria-expanded")).toBe("true");
    const panel = wrapper.find(`#${btn.attributes("aria-controls")}`);
    expect(panel.exists()).toBe(true);
    expect(panel.attributes("data-state")).toBe("ready");
    expect(panel.text()).toContain("BY CATEGORY");
    expect(panel.text()).toContain("LAST 7 DAYS");
    expect(panel.text()).toContain("No group");
    expect(panel.text()).toContain("3 May");

    const bars = panel.findAll(".detail-bar");
    expect(bars[0].attributes("style")).toContain("width: 100%");
    expect(bars[1].attributes("style")).toContain("width: 33.3");
  });

  it("renders the day rows without bars", async () => {
    restoreOnLine = mockOnLine(true);
    vi.spyOn(analyticsApi, "fetchEventDetail").mockResolvedValue(DETAIL);
    const wrapper = await mountView();
    await toggles(wrapper)[0].trigger("click");
    await flushPromises();
    const days = wrapper.findAll(".event-detail .detail-day");
    expect(days.map((d) => d.text())).toEqual(["3 May 150 RSD", "1 May 250 RSD"]);
    expect(wrapper.findAll(".event-detail .detail-day .detail-bar-track")).toHaveLength(0);
  });

  it("fetches the detail once and reuses it after collapsing and re-expanding", async () => {
    restoreOnLine = mockOnLine(true);
    const spy = vi.spyOn(analyticsApi, "fetchEventDetail").mockResolvedValue(DETAIL);
    const wrapper = await mountView();
    const btn = toggles(wrapper)[0];
    await btn.trigger("click");
    await flushPromises();
    await btn.trigger("click");
    await flushPromises();
    expect(btn.attributes("aria-expanded")).toBe("false");
    expect(wrapper.find(".event-detail").exists()).toBe(false);
    await btn.trigger("click");
    await flushPromises();
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith(1);
  });

  it("shows a skeleton while the detail is loading", async () => {
    restoreOnLine = mockOnLine(true);
    let resolveDetail;
    vi.spyOn(analyticsApi, "fetchEventDetail").mockReturnValue(
      new Promise((resolve) => { resolveDetail = resolve; }),
    );
    const wrapper = await mountView();
    await toggles(wrapper)[0].trigger("click");
    await flushPromises();
    expect(wrapper.find(".event-detail").attributes("data-state")).toBe("loading");
    expect(wrapper.find(".detail-skeleton").exists()).toBe(true);
    resolveDetail(DETAIL);
    await flushPromises();
    expect(wrapper.find(".detail-skeleton").exists()).toBe(false);
  });

  it("renders a single 'no expenses yet' line for an event without expenses", async () => {
    restoreOnLine = mockOnLine(true);
    vi.spyOn(analyticsApi, "fetchEventDetail").mockResolvedValue(EMPTY_DETAIL);
    const wrapper = await mountView();
    await toggles(wrapper)[1].trigger("click");
    await flushPromises();
    const panel = wrapper.find(".event-detail");
    expect(panel.attributes("data-state")).toBe("empty");
    expect(panel.text()).toBe("No expenses yet");
    expect(panel.find(".detail-skeleton").exists()).toBe(false);
  });

  it("renders an offline line instead of a skeleton when there is no cached detail", async () => {
    restoreOnLine = mockOnLine(false);
    const spy = vi.spyOn(analyticsApi, "fetchEventDetail").mockResolvedValue(DETAIL);
    const wrapper = await mountView();
    await toggles(wrapper)[0].trigger("click");
    await flushPromises();
    const panel = wrapper.find(".event-detail");
    expect(panel.attributes("data-state")).toBe("offline");
    expect(panel.text()).toContain("Offline");
    expect(panel.find(".detail-skeleton").exists()).toBe(false);
    expect(spy).not.toHaveBeenCalled();
  });

  it("renders a failure line when the detail request fails", async () => {
    restoreOnLine = mockOnLine(true);
    vi.spyOn(analyticsApi, "fetchEventDetail").mockRejectedValue(new Error("down"));
    const wrapper = await mountView();
    await toggles(wrapper)[0].trigger("click");
    await flushPromises();
    const panel = wrapper.find(".event-detail");
    expect(panel.attributes("data-state")).toBe("error");
    expect(panel.text()).toContain("Failed to load");
    expect(panel.find(".detail-skeleton").exists()).toBe(false);
  });

  it("refetches the open detail after a summary refetch clears the cache", async () => {
    restoreOnLine = mockOnLine(true);
    const updated = { ...DETAIL, total: "500" };
    const spy = vi
      .spyOn(analyticsApi, "fetchEventDetail")
      .mockResolvedValueOnce(DETAIL)
      .mockResolvedValueOnce(updated);
    const wrapper = await mountView();
    await toggles(wrapper)[0].trigger("click");
    await flushPromises();

    const store = useAnalyticsStore();
    analyticsApi.fetchAnalyticsSummary.mockResolvedValue({
      summary: SUMMARY,
      events: EVENTS,
      trends: null,
      receipts_queue: { pending: 0, in_progress: 0, sleeping: 0, poisoned: 0 },
    });
    store.markDirty();
    await store.loadIfNeeded();
    await flushPromises();

    expect(spy).toHaveBeenCalledTimes(2);
    expect(store.eventDetails[1]).toEqual(updated);
    expect(wrapper.find(".event-detail").attributes("data-state")).toBe("ready");
  });
});

describe("AnalyticsView — summary load failure", () => {
  it("shows an error toast and keeps the cached stats when the refetch fails", async () => {
    restoreOnLine = mockOnLine(true);
    localStorage.setItem("dinary:analytics:dirty", "1");
    const wrapper = await mountView();
    expect(useToastStore().message).toBe("Couldn't load stats");
    expect(useToastStore().type).toBe("error");
    expect(wrapper.findAll("button.event-toggle")).toHaveLength(EVENTS.length);
    expect(wrapper.text()).not.toContain("Couldn't load stats");
  });

  it("shows an error line instead of a blank page when there is no cache", async () => {
    restoreOnLine = mockOnLine(true);
    localStorage.clear();
    const wrapper = await mountView();
    expect(useToastStore().message).toBe("Couldn't load stats");
    expect(wrapper.find(".empty-state").text()).toBe("Couldn't load stats");
  });

  it("shows no error when the stats load", async () => {
    restoreOnLine = mockOnLine(true);
    localStorage.clear();
    analyticsApi.fetchAnalyticsSummary.mockResolvedValue({ summary: SUMMARY, events: EVENTS, trends: null });
    const wrapper = await mountView();
    expect(useToastStore().visible).toBe(false);
    expect(wrapper.text()).not.toContain("Couldn't load stats");
  });
});

const TRENDS = [
  { kind: "group", id: 3, basket_name: "Food", direction: "up", pct: "37%", reason: { kind: "event", names: ["Pržno"] } },
  { kind: "tag", id: 3, basket_name: "Food", direction: "down", pct: "12%", reason: { kind: "category", names: ["fruit", "cafe"] } },
  { kind: "group", id: 5, basket_name: "Health", direction: "down", pct: "48%", reason: null },
];

function row(over) {
  return { key: 1, label: "x", sublabel: null, recent: "0", prior: "0", delta: "0", direction: "up", share: 0, count: null, ...over };
}

const TREND_DETAIL = {
  kind: "group",
  id: 3,
  name: "Food",
  direction: "up",
  pct: "37%",
  currency: "EUR",
  recent: "1 973",
  prior: "1 440",
  delta: "+533",
  blocks: [
    {
      dimension: "category",
      receipt_backed_only: false,
      recent: "1 973",
      prior: "1 440",
      delta: "+533",
      rows: [
        row({ key: 1, label: "fruit", sublabel: "Food", recent: "400", prior: "132", delta: "+268", share: 1 }),
        row({ key: 2, label: "groceries", sublabel: "Food", recent: "500", prior: "712", delta: "−212", direction: "down", share: 0.5 }),
        row({ key: null, label: null, recent: "1 073", prior: "596", delta: "+477", share: 0.25, count: 3 }),
      ],
    },
    {
      dimension: "event",
      receipt_backed_only: false,
      recent: "1 973",
      prior: "1 440",
      delta: "+533",
      rows: [
        row({ key: 7, label: "Pržno", recent: "580", prior: "0", delta: "+580", share: 1 }),
        row({ key: null, label: null, recent: "1 393", prior: "1 440", delta: "−47", direction: "down", share: 0.08 }),
      ],
    },
    {
      dimension: "chain",
      receipt_backed_only: true,
      recent: "300",
      prior: "100",
      delta: "+200",
      rows: [row({ key: 4, label: "Maxi", recent: "300", prior: "100", delta: "+200", share: 1 })],
    },
  ],
};

function seedTrendsCache() {
  localStorage.setItem("dinary:analytics:fetchedAt", String(Date.now() - 60_000));
  localStorage.setItem(
    "dinary:analytics:v2",
    JSON.stringify({ summary: SUMMARY, events: EVENTS, trends: TRENDS }),
  );
}

function trendToggles(wrapper) {
  return wrapper.findAll("button.trend-toggle");
}

describe("AnalyticsView — trends", () => {
  beforeEach(() => {
    localStorage.clear();
    seedTrendsCache();
  });

  it("renders one row per basket with its reason line, and none for a null reason", async () => {
    restoreOnLine = mockOnLine(true);
    const wrapper = await mountView();
    const rows = wrapper.findAll(".trend-row");
    expect(rows).toHaveLength(3);
    expect(rows[0].find(".trend-reason").text()).toBe("mostly Pržno");
    expect(rows[1].find(".trend-reason").text()).toBe("mostly fruit, cafe");
    expect(rows[2].find(".trend-reason").exists()).toBe(false);
    expect(rows[0].find(".trend-pct").attributes("style")).toContain("var(--up)");
    expect(rows[2].find(".trend-pct").attributes("style")).toContain("var(--down)");
  });

  it("keeps a group and a tag with the same name and id apart", async () => {
    restoreOnLine = mockOnLine(true);
    const wrapper = await mountView();
    const ids = trendToggles(wrapper).map((b) => b.attributes("aria-controls"));
    expect(ids).toEqual(["trend-detail-group-3", "trend-detail-tag-3", "trend-detail-group-5"]);
  });

  it("expands on tap, fetches once and reuses the detail after re-expanding", async () => {
    restoreOnLine = mockOnLine(true);
    const spy = vi.spyOn(analyticsApi, "fetchTrendDetail").mockResolvedValue(TREND_DETAIL);
    const wrapper = await mountView();
    expect(spy).not.toHaveBeenCalled();
    const btn = trendToggles(wrapper)[0];
    await btn.trigger("click");
    await flushPromises();
    expect(btn.attributes("aria-expanded")).toBe("true");
    const panel = wrapper.find(`#${btn.attributes("aria-controls")}`);
    expect(panel.attributes("data-state")).toBe("ready");
    await btn.trigger("click");
    await flushPromises();
    expect(wrapper.find(".trend-detail").exists()).toBe(false);
    await btn.trigger("click");
    await flushPromises();
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith("group", 3);
  });

  it("renders the header, the blocks, the captions and bar widths from share", async () => {
    restoreOnLine = mockOnLine(true);
    vi.spyOn(analyticsApi, "fetchTrendDetail").mockResolvedValue(TREND_DETAIL);
    const wrapper = await mountView();
    await trendToggles(wrapper)[0].trigger("click");
    await flushPromises();
    const panel = wrapper.find(".trend-detail");
    expect(panel.find(".trend-summary").text()).toContain("1 440 → 1 973");
    expect(panel.find(".trend-summary").text()).toContain("+533");

    const category = panel.find('[data-dimension="category"]');
    expect(category.text()).toContain("BY CATEGORY");
    expect(category.text()).toContain("other (3)");
    expect(category.find(".detail-group").exists()).toBe(false);
    const bars = category.findAll(".detail-bar");
    expect(bars[0].attributes("style")).toContain("width: 100%");
    expect(bars[1].attributes("style")).toContain("width: 50%");
    expect(bars[2].attributes("style")).toContain("width: 25%");
    expect(bars[1].attributes("style")).toContain("var(--down)");

    const events = panel.find('[data-dimension="event"]');
    expect(events.text()).toContain("BY EVENT");
    expect(events.text()).toContain("no event");
    expect(events.text()).not.toContain("receipts only");

    const chain = panel.find('[data-dimension="chain"]');
    expect(chain.text()).toContain("BY SHOP");
    expect(chain.find(".detail-caption").text()).toContain("receipts only");
    expect(chain.find(".detail-caption").text()).toContain("+200");
  });

  it("colours the header difference by direction, and leaves a zero change neutral", async () => {
    restoreOnLine = mockOnLine(true);
    vi.spyOn(analyticsApi, "fetchTrendDetail")
      .mockResolvedValueOnce(TREND_DETAIL)
      .mockResolvedValueOnce({ ...TREND_DETAIL, kind: "tag", recent: "100", prior: "100", delta: "0" });
    const wrapper = await mountView();
    await trendToggles(wrapper)[0].trigger("click");
    await flushPromises();
    expect(wrapper.find(".trend-summary .detail-amount").attributes("style")).toContain("var(--up)");

    await trendToggles(wrapper)[1].trigger("click");
    await flushPromises();
    const amount = wrapper.find(".trend-summary .detail-amount");
    expect(amount.text()).toBe("0 EUR");
    expect(amount.attributes("style")).toBeUndefined();
  });

  it("labels categories of a tag basket with their group, or 'No group'", async () => {
    restoreOnLine = mockOnLine(true);
    const tagDetail = {
      ...TREND_DETAIL,
      kind: "tag",
      blocks: [
        {
          ...TREND_DETAIL.blocks[0],
          rows: [
            row({ key: 1, label: "fruit", sublabel: "Food", delta: "+1", share: 1 }),
            row({ key: 2, label: "gift", sublabel: null, delta: "+1", share: 1 }),
          ],
        },
      ],
    };
    vi.spyOn(analyticsApi, "fetchTrendDetail").mockResolvedValue(tagDetail);
    const wrapper = await mountView();
    await trendToggles(wrapper)[1].trigger("click");
    await flushPromises();
    const groups = wrapper.find(".trend-detail").findAll(".detail-group").map((g) => g.text());
    expect(groups).toEqual(["Food", "No group"]);
  });

  it("opening a trend closes an open event, and the other way round", async () => {
    restoreOnLine = mockOnLine(true);
    vi.spyOn(analyticsApi, "fetchTrendDetail").mockResolvedValue(TREND_DETAIL);
    vi.spyOn(analyticsApi, "fetchEventDetail").mockResolvedValue(DETAIL);
    const wrapper = await mountView();
    await toggles(wrapper)[0].trigger("click");
    await flushPromises();
    expect(wrapper.find(".event-detail").exists()).toBe(true);

    await trendToggles(wrapper)[0].trigger("click");
    await flushPromises();
    expect(wrapper.find(".event-detail").exists()).toBe(false);
    expect(toggles(wrapper)[0].attributes("aria-expanded")).toBe("false");
    expect(wrapper.find(".trend-detail").exists()).toBe(true);

    await toggles(wrapper)[0].trigger("click");
    await flushPromises();
    expect(wrapper.find(".trend-detail").exists()).toBe(false);
    expect(wrapper.find(".event-detail").exists()).toBe(true);
  });

  it("shows a skeleton while the detail is loading", async () => {
    restoreOnLine = mockOnLine(true);
    let resolveDetail;
    vi.spyOn(analyticsApi, "fetchTrendDetail").mockReturnValue(
      new Promise((resolve) => { resolveDetail = resolve; }),
    );
    const wrapper = await mountView();
    await trendToggles(wrapper)[0].trigger("click");
    await flushPromises();
    expect(wrapper.find(".trend-detail").attributes("data-state")).toBe("loading");
    expect(wrapper.find(".trend-detail .detail-skeleton").exists()).toBe(true);
    resolveDetail(TREND_DETAIL);
    await flushPromises();
    expect(wrapper.find(".trend-detail").attributes("data-state")).toBe("ready");
  });

  it("renders an offline line without fetching when offline", async () => {
    restoreOnLine = mockOnLine(false);
    const spy = vi.spyOn(analyticsApi, "fetchTrendDetail").mockResolvedValue(TREND_DETAIL);
    const wrapper = await mountView();
    await trendToggles(wrapper)[0].trigger("click");
    await flushPromises();
    const panel = wrapper.find(".trend-detail");
    expect(panel.attributes("data-state")).toBe("offline");
    expect(panel.text()).toContain("Offline");
    expect(spy).not.toHaveBeenCalled();
  });

  it("renders a failure line when the detail request fails", async () => {
    restoreOnLine = mockOnLine(true);
    vi.spyOn(analyticsApi, "fetchTrendDetail").mockRejectedValue(new Error("down"));
    const wrapper = await mountView();
    await trendToggles(wrapper)[0].trigger("click");
    await flushPromises();
    const panel = wrapper.find(".trend-detail");
    expect(panel.attributes("data-state")).toBe("error");
    expect(panel.text()).toContain("Failed to load");
  });

  it("renders an empty line when no block has rows", async () => {
    restoreOnLine = mockOnLine(true);
    vi.spyOn(analyticsApi, "fetchTrendDetail").mockResolvedValue({
      ...TREND_DETAIL,
      blocks: [{ ...TREND_DETAIL.blocks[0], rows: [] }],
    });
    const wrapper = await mountView();
    await trendToggles(wrapper)[0].trigger("click");
    await flushPromises();
    expect(wrapper.find(".trend-detail").attributes("data-state")).toBe("empty");
  });
});

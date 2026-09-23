import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import AnalyticsView from "../src/views/AnalyticsView.vue";
import { useAnalyticsStore } from "../src/stores/analytics.js";
import * as analyticsApi from "../src/api/analytics.js";

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
    "dinary:analytics:v1",
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

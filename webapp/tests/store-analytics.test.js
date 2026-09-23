import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useAnalyticsStore } from "../src/stores/analytics.js";
import * as analyticsApi from "../src/api/analytics.js";

beforeEach(async () => {
  await allure.epic("Analytics");
  await allure.feature("Frontend");
  await allure.story("Analytics store");
});

const EMPTY_QUEUE = { pending: 0, in_progress: 0, sleeping: 0, poisoned: 0 };
const SUMMARY = { this_month_total: "100", ytd_total: "900", currency: "RSD" };
const EVENTS = [{ id: 1, name: "trip", date_range: "1–3 May 2026", total: "50", currency: "RSD", open: false }];
const TRENDS = [{ basket_name: "Food", direction: "up", pct: "12%" }];

function response(queue = EMPTY_QUEUE) {
  return { summary: SUMMARY, events: EVENTS, trends: TRENDS, receipts_queue: queue };
}

function seedFreshCache() {
  localStorage.setItem("dinary:analytics:fetchedAt", String(Date.now() - 60_000));
  localStorage.setItem("dinary:analytics:v1", JSON.stringify({ summary: SUMMARY, events: EVENTS, trends: TRENDS }));
}

beforeEach(() => {
  localStorage.clear();
  setActivePinia(createPinia());
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("analytics store: cache hydration", () => {
  it("restores summary, events and trends from localStorage", () => {
    seedFreshCache();
    const store = useAnalyticsStore();
    expect(store.summary).toEqual(SUMMARY);
    expect(store.events).toEqual(EVENTS);
    expect(store.trends).toEqual(TRENDS);
  });

  it("reads a cache entry written by the old build and still refetches once", async () => {
    localStorage.setItem(
      "dinary:analytics:v1",
      JSON.stringify({ summary: SUMMARY, events: EVENTS, trends: TRENDS, lastFetched: Date.now() }),
    );
    const spy = vi.spyOn(analyticsApi, "fetchAnalyticsSummary").mockResolvedValue(response());
    const store = useAnalyticsStore();
    expect(store.summary).toEqual(SUMMARY);
    expect(store.isStale()).toBe(true);
    await store.loadIfNeeded();
    expect(spy).toHaveBeenCalledTimes(1);
  });
});

describe("analytics store: loadIfNeeded()", () => {
  it("skips the fetch on a fresh cache", async () => {
    seedFreshCache();
    const spy = vi.spyOn(analyticsApi, "fetchAnalyticsSummary");
    const store = useAnalyticsStore();
    await store.loadIfNeeded();
    expect(spy).not.toHaveBeenCalled();
  });

  it("refetches after markDirty", async () => {
    seedFreshCache();
    const spy = vi.spyOn(analyticsApi, "fetchAnalyticsSummary").mockResolvedValue(response());
    const store = useAnalyticsStore();
    store.markDirty();
    expect(store.isStale()).toBe(true);
    await store.loadIfNeeded();
    expect(spy).toHaveBeenCalledTimes(1);
    expect(store.isStale()).toBe(false);
  });

  it("persists the fetched data to localStorage", async () => {
    vi.spyOn(analyticsApi, "fetchAnalyticsSummary").mockResolvedValue(response());
    const store = useAnalyticsStore();
    await store.loadIfNeeded();
    expect(JSON.parse(localStorage.getItem("dinary:analytics:v1"))).toEqual({
      summary: SUMMARY,
      events: EVENTS,
      trends: TRENDS,
    });
  });

  it("stays stale when markDirty happens while the fetch is in flight", async () => {
    let resolveFetch;
    vi.spyOn(analyticsApi, "fetchAnalyticsSummary").mockReturnValue(
      new Promise((resolve) => {
        resolveFetch = resolve;
      }),
    );
    const store = useAnalyticsStore();
    const pending = store.loadIfNeeded();
    store.markDirty();
    resolveFetch(response());
    await pending;
    expect(store.isStale()).toBe(true);
    expect(localStorage.getItem("dinary:analytics:dirty")).toBe("1");
  });

  it("leaves the store stale and loading cleared when the fetch fails", async () => {
    vi.spyOn(analyticsApi, "fetchAnalyticsSummary").mockRejectedValue(new Error("boom"));
    const store = useAnalyticsStore();
    await expect(store.loadIfNeeded()).rejects.toThrow("boom");
    expect(store.loading).toBe(false);
    expect(store.isStale()).toBe(true);
  });
});

describe("analytics store: receipts queue decides freshness", () => {
  it.each([
    ["pending", { ...EMPTY_QUEUE, pending: 1 }],
    ["in_progress", { ...EMPTY_QUEUE, in_progress: 1 }],
    ["sleeping", { ...EMPTY_QUEUE, sleeping: 1 }],
  ])("stays stale while receipts are %s", async (_name, queue) => {
    vi.spyOn(analyticsApi, "fetchAnalyticsSummary").mockResolvedValue(response(queue));
    const store = useAnalyticsStore();
    store.markDirty();
    await store.loadIfNeeded();
    expect(store.isStale()).toBe(true);
    expect(localStorage.getItem("dinary:analytics:dirty")).toBe("1");
  });

  it("clears when the queue is empty", async () => {
    vi.spyOn(analyticsApi, "fetchAnalyticsSummary").mockResolvedValue(response());
    const store = useAnalyticsStore();
    store.markDirty();
    await store.loadIfNeeded();
    expect(store.isStale()).toBe(false);
    expect(localStorage.getItem("dinary:analytics:dirty")).toBeNull();
  });

  it("clears when only poisoned jobs remain", async () => {
    vi.spyOn(analyticsApi, "fetchAnalyticsSummary").mockResolvedValue(
      response({ ...EMPTY_QUEUE, poisoned: 3 }),
    );
    const store = useAnalyticsStore();
    store.markDirty();
    await store.loadIfNeeded();
    expect(store.isStale()).toBe(false);
  });

  it("cold start: a first fetch with a busy queue leaves the store stale", async () => {
    vi.spyOn(analyticsApi, "fetchAnalyticsSummary").mockResolvedValue(
      response({ ...EMPTY_QUEUE, pending: 1 }),
    );
    const store = useAnalyticsStore();
    expect(store.dirtyFlag).toBe(false);
    expect(store.lastFetchedAt).toBeNull();
    await store.loadIfNeeded();
    expect(store.isStale()).toBe(true);
  });
});

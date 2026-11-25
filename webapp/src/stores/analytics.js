import { defineStore } from "pinia";
import { ref } from "vue";
import { fetchAnalyticsSummary, fetchEventDetail, fetchTrendDetail } from "../api/analytics.js";
import { useStaleCache } from "../composables/useStaleCache.js";

export function trendKey(kind, id) {
  return `${kind}-${id}`;
}

export const useAnalyticsStore = defineStore("analytics", () => {
  const { dirtyFlag, lastFetchedAt, markDirty, beginFetch, stampFresh, isStale, readCache, writeCache } = useStaleCache({
    dirtyKey: "dinary:analytics:dirty",
    fetchedKey: "dinary:analytics:fetchedAt",
    dataKey: "dinary:analytics:v2",
  });
  const cached = readCache();
  const summary = ref(cached?.summary ?? null);
  const events = ref(cached?.events ?? []);
  const trends = ref(cached?.trends ?? null);
  const loading = ref(false);
  const eventDetails = ref({});
  const trendDetails = ref({});
  let summaryGeneration = 0;

  async function loadIfNeeded() {
    if (loading.value || !isStale()) return;
    loading.value = true;
    try {
      const fetchToken = beginFetch();
      const data = await fetchAnalyticsSummary();
      summary.value = data.summary;
      events.value = data.events ?? [];
      trends.value = data.trends;
      writeCache({ summary: summary.value, events: events.value, trends: trends.value });
      eventDetails.value = {};
      trendDetails.value = {};
      summaryGeneration += 1;
      stampFresh(fetchToken);
      // A poisoned job is terminal, so it must not keep the cache dirty forever.
      const q = data.receipts_queue ?? {};
      if (q.pending > 0 || q.in_progress > 0 || q.sleeping > 0) markDirty();
    } finally {
      loading.value = false;
    }
  }

  function detailLoader(fetchDetail, target) {
    const requests = new Map();

    // A detail fetched before a summary refetch landed may predate the change that made
    // the summary stale, so it is fetched again rather than stored.
    async function fetchCurrent(key, args) {
      for (;;) {
        const generation = summaryGeneration;
        const detail = await fetchDetail(...args);
        if (generation === summaryGeneration) {
          target.value = { ...target.value, [key]: detail };
          return detail;
        }
      }
    }

    return (key, ...args) => {
      const cachedDetail = target.value[key];
      if (cachedDetail) return Promise.resolve(cachedDetail);
      if (!requests.has(key)) {
        const request = fetchCurrent(key, args).finally(() => requests.delete(key));
        requests.set(key, request);
      }
      return requests.get(key);
    };
  }

  const loadEvent = detailLoader((eventId) => fetchEventDetail(eventId), eventDetails);
  const loadTrend = detailLoader((kind, id) => fetchTrendDetail(kind, id), trendDetails);

  function loadEventDetail(eventId) {
    return loadEvent(eventId, eventId);
  }

  function loadTrendDetail(kind, id) {
    return loadTrend(trendKey(kind, id), kind, id);
  }

  return {
    summary,
    events,
    trends,
    loading,
    eventDetails,
    trendDetails,
    dirtyFlag,
    lastFetchedAt,
    isStale,
    markDirty,
    loadIfNeeded,
    loadEventDetail,
    loadTrendDetail,
  };
});

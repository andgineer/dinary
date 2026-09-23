import { defineStore } from "pinia";
import { ref } from "vue";
import { fetchAnalyticsSummary, fetchEventDetail } from "../api/analytics.js";
import { useStaleCache } from "../composables/useStaleCache.js";

export const useAnalyticsStore = defineStore("analytics", () => {
  const { dirtyFlag, lastFetchedAt, markDirty, beginFetch, stampFresh, isStale, readCache, writeCache } = useStaleCache({
    dirtyKey: "dinary:analytics:dirty",
    fetchedKey: "dinary:analytics:fetchedAt",
    dataKey: "dinary:analytics:v1",
  });
  const cached = readCache();
  const summary = ref(cached?.summary ?? null);
  const events = ref(cached?.events ?? []);
  const trends = ref(cached?.trends ?? null);
  const loading = ref(false);
  const eventDetails = ref({});
  const detailRequests = new Map();
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
      summaryGeneration += 1;
      stampFresh(fetchToken);
      // A poisoned job is terminal, so it must not keep the cache dirty forever.
      const q = data.receipts_queue ?? {};
      if (q.pending > 0 || q.in_progress > 0 || q.sleeping > 0) markDirty();
    } finally {
      loading.value = false;
    }
  }

  // A detail fetched before a summary refetch landed may predate the change that made the
  // summary stale, so it is fetched again rather than stored.
  async function fetchCurrentDetail(eventId) {
    for (;;) {
      const generation = summaryGeneration;
      const detail = await fetchEventDetail(eventId);
      if (generation === summaryGeneration) {
        eventDetails.value = { ...eventDetails.value, [eventId]: detail };
        return detail;
      }
    }
  }

  function loadEventDetail(eventId) {
    const cachedDetail = eventDetails.value[eventId];
    if (cachedDetail) return Promise.resolve(cachedDetail);
    if (!detailRequests.has(eventId)) {
      const request = fetchCurrentDetail(eventId).finally(() => detailRequests.delete(eventId));
      detailRequests.set(eventId, request);
    }
    return detailRequests.get(eventId);
  }

  return {
    summary,
    events,
    trends,
    loading,
    eventDetails,
    dirtyFlag,
    lastFetchedAt,
    isStale,
    markDirty,
    loadIfNeeded,
    loadEventDetail,
  };
});

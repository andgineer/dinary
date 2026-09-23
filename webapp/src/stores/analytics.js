import { defineStore } from "pinia";
import { ref } from "vue";
import { fetchAnalyticsSummary } from "../api/analytics.js";
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
      stampFresh(fetchToken);
      // A poisoned job is terminal, so it must not keep the cache dirty forever.
      const q = data.receipts_queue ?? {};
      if (q.pending > 0 || q.in_progress > 0 || q.sleeping > 0) markDirty();
    } finally {
      loading.value = false;
    }
  }

  return { summary, events, trends, loading, dirtyFlag, lastFetchedAt, isStale, markDirty, loadIfNeeded };
});

import { defineStore } from "pinia";
import { ref } from "vue";
import { fetchAnalyticsSummary } from "../api/analytics.js";

const CACHE_KEY = "dinary:analytics:v1";

export const useAnalyticsStore = defineStore("analytics", () => {
  const summary = ref(null);
  const events = ref([]);
  const trends = ref(null);
  const loading = ref(false);
  const lastFetched = ref(null);

  try {
    const cached = JSON.parse(localStorage.getItem(CACHE_KEY) || "null");
    if (cached) {
      summary.value = cached.summary;
      events.value = cached.events ?? [];
      trends.value = cached.trends;
      lastFetched.value = cached.lastFetched;
    }
  } catch {}

  const TTL_MS = 24 * 60 * 60 * 1000;

  async function fetchAll() {
    if (lastFetched.value && Date.now() - lastFetched.value < TTL_MS) return;
    loading.value = true;
    try {
      const data = await fetchAnalyticsSummary();
      summary.value = data.summary;
      events.value = data.events ?? [];
      trends.value = data.trends;
      lastFetched.value = Date.now();
      try {
        localStorage.setItem(CACHE_KEY, JSON.stringify({
          summary: summary.value,
          events: events.value,
          trends: trends.value,
          lastFetched: lastFetched.value,
        }));
      } catch {}
    } finally {
      loading.value = false;
    }
  }

  return { summary, events, trends, loading, lastFetched, fetchAll };
});

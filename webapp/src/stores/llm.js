import { defineStore } from "pinia";
import { ref } from "vue";
import * as llmApi from "../api/adminLlm.js";
import { useStaleCache } from "../composables/useStaleCache.js";
import { useToastStore } from "./toast.js";

const CACHE_KEY = "dinary:llm:v1";
const DIRTY_KEY = "dinary:llm:dirty";
const FETCHED_KEY = "dinary:llm:fetchedAt";

export const useLlmStore = defineStore("llm", () => {
  const { dirtyFlag, lastFetchedAt, markDirty, stampFresh, isStale, readCache, writeCache } = useStaleCache({
    dirtyKey: DIRTY_KEY,
    fetchedKey: FETCHED_KEY,
    dataKey: CACHE_KEY,
  });
  const cached = readCache();
  const providers = ref(cached?.providers ?? []);
  const health = ref(cached?.health ?? null);
  const loading = ref(false);

  async function loadIfNeeded() {
    if (isStale()) await refresh();
  }

  async function refresh() {
    loading.value = true;
    try {
      const status = await llmApi.getStatus();
      // Copy so the optimistic disable/enable flip never mutates the caller's array.
      providers.value = [...(status.providers ?? [])];
      health.value = status.health ?? null;
      writeCache({ providers: providers.value, health: health.value });
      stampFresh();
    } catch (err) {
      if (navigator.onLine) {
        useToastStore().show(err?.message || "Failed to load LLM providers", "error");
      }
    } finally {
      loading.value = false;
    }
  }

  // The provider list is owned by the preset file; the only mutation the UI
  // offers is the persistent user disable/enable latch.
  async function toggleDisabled(name) {
    const idx = providers.value.findIndex((p) => p.name === name);
    if (idx === -1) return;
    const wasDisabled = providers.value[idx].disabled;
    // Optimistic flip so the toggle feels instant; refresh reconciles derived fields.
    providers.value[idx] = { ...providers.value[idx], disabled: !wasDisabled };
    try {
      if (wasDisabled) {
        await llmApi.enableProvider(name);
      } else {
        await llmApi.disableProvider(name);
      }
      await refresh();
    } catch (err) {
      providers.value[idx] = { ...providers.value[idx], disabled: wasDisabled };
      useToastStore().show(err?.message || "Toggle failed", "error");
    }
  }

  return {
    providers,
    health,
    loading,
    dirtyFlag,
    lastFetchedAt,
    markDirty,
    loadIfNeeded,
    refresh,
    toggleDisabled,
  };
});

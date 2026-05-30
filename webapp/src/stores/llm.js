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
      providers.value = status.providers ?? [];
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

  async function toggle(id) {
    const idx = providers.value.findIndex((p) => p.id === id);
    if (idx === -1) return;
    const prev = providers.value[idx].is_enabled;
    providers.value[idx] = { ...providers.value[idx], is_enabled: !prev };
    try {
      const updated = await llmApi.updateProvider(id, { is_enabled: !prev });
      providers.value[idx] = { ...providers.value[idx], ...updated };
    } catch (err) {
      providers.value[idx] = { ...providers.value[idx], is_enabled: prev };
      useToastStore().show(err?.message || "Toggle failed", "error");
    }
  }

  async function move(id, dir) {
    const prov = providers.value.find((p) => p.id === id);
    if (!prov) return;
    const newPriority = dir === "up" ? prov.priority - 1 : prov.priority + 1;
    try {
      await llmApi.updateProvider(id, { priority: newPriority });
      await refresh();
    } catch (err) {
      useToastStore().show(err?.message || "Reorder failed", "error");
    }
  }

  async function save(data) {
    try {
      if (data.id) {
        const { id, ...patch } = data;
        await llmApi.updateProvider(id, patch);
      } else {
        await llmApi.createProvider(data);
      }
      await refresh();
    } catch (err) {
      useToastStore().show(err?.message || "Save failed", "error");
      throw err;
    }
  }

  async function remove(id) {
    try {
      await llmApi.deleteProvider(id);
      providers.value = providers.value.filter((p) => p.id !== id);
      useToastStore().show("Provider removed", "info");
    } catch (err) {
      useToastStore().show(err?.message || "Delete failed", "error");
      throw err;
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
    toggle,
    move,
    save,
    remove,
  };
});

import { defineStore } from "pinia";
import { ref } from "vue";
import * as llmApi from "../api/adminLlm.js";
import { useToastStore } from "./toast.js";

const DIRTY_KEY = "dinary:llm:dirty";
const FETCHED_KEY = "dinary:llm:fetchedAt";
const TTL_MS = 24 * 60 * 60 * 1000;

export const useLlmStore = defineStore("llm", () => {
  const providers = ref([]);
  const health = ref(null);
  const loading = ref(false);
  const dirtyFlag = ref(localStorage.getItem(DIRTY_KEY) === "1");
  const lastFetchedAt = ref(Number(localStorage.getItem(FETCHED_KEY)) || null);

  function markDirty() {
    dirtyFlag.value = true;
    localStorage.setItem(DIRTY_KEY, "1");
  }

  async function loadIfNeeded() {
    const age = lastFetchedAt.value ? Date.now() - lastFetchedAt.value : Infinity;
    if (dirtyFlag.value || !lastFetchedAt.value || age > TTL_MS) {
      await refresh();
    }
  }

  async function refresh() {
    loading.value = true;
    try {
      const status = await llmApi.getStatus();
      providers.value = status.providers ?? [];
      health.value = status.health ?? null;
      lastFetchedAt.value = Date.now();
      localStorage.setItem(FETCHED_KEY, String(lastFetchedAt.value));
      if ((status.pending_receipts ?? 0) === 0) {
        dirtyFlag.value = false;
        localStorage.removeItem(DIRTY_KEY);
      }
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

  async function test(id) {
    const toast = useToastStore();
    toast.show("Testing…", "info");
    try {
      const result = await llmApi.testProvider(id);
      const ms = result?.latency_ms ?? result?.ms;
      if (ms !== undefined) {
        toast.show(`OK · ${ms}ms`, "success");
      } else {
        toast.show("Test passed", "success");
      }
      await refresh();
    } catch (err) {
      toast.show(`Test failed: ${err?.message || err}`, "error");
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
    test,
  };
});

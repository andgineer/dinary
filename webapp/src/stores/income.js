import { defineStore } from "pinia";
import { ref } from "vue";
import { createIncome, deleteIncome, listIncomes, updateIncome } from "../api/income.js";
import { useStaleCache } from "../composables/useStaleCache.js";
import { useToastStore } from "./toast.js";

const CACHE_KEY = "dinary:income:v1";
const DIRTY_KEY = "dinary:income:dirty";
const FETCHED_KEY = "dinary:income:fetchedAt";

export const useIncomeStore = defineStore("income", () => {
  const { dirtyFlag, lastFetchedAt, stampFresh, isStale, readCache, writeCache, clearCache } =
    useStaleCache({ dirtyKey: DIRTY_KEY, fetchedKey: FETCHED_KEY, dataKey: CACHE_KEY });

  const cached = readCache() || {};
  const items = ref(cached.items ?? []);
  const hasMore = ref(cached.hasMore ?? true);
  const page = ref(cached.page ?? 0);
  const loading = ref(false);
  const fromCache = ref(Array.isArray(cached.items) && cached.items.length > 0);
  const openRowId = ref(null);

  function _persist() {
    writeCache({ items: items.value, hasMore: hasMore.value, page: page.value });
  }

  async function loadIfNeeded() {
    if (isStale()) {
      reset();
      await loadNextPage();
    }
  }

  async function loadNextPage() {
    if (loading.value) return;
    if (page.value > 0 && !hasMore.value) return;
    loading.value = true;
    try {
      const nextPage = page.value + 1;
      const data = await listIncomes({ page: nextPage, pageSize: 20 });
      const existingKeys = new Set(items.value.map((i) => `${i.year}-${i.month}`));
      const incoming = (data.items ?? []).filter((i) => !existingKeys.has(`${i.year}-${i.month}`));
      items.value = [...items.value, ...incoming];
      hasMore.value = data.has_more ?? false;
      page.value = nextPage;
      stampFresh();
      _persist();
    } catch (err) {
      if (navigator.onLine) {
        const toast = useToastStore();
        toast.show(err?.message || "Failed to load incomes", "error");
      }
    } finally {
      loading.value = false;
    }
  }

  async function add(payload) {
    const toast = useToastStore();
    try {
      await createIncome(payload);
      reset();
      await loadNextPage();
    } catch (err) {
      if (err?.status === 409) {
        toast.show("Income for this month already exists", "error");
      } else {
        toast.show(err?.message || "Failed to save income", "error");
      }
      throw err;
    }
  }

  async function patch(year, month, payload) {
    const toast = useToastStore();
    try {
      await updateIncome(year, month, payload);
      reset();
      await loadNextPage();
    } catch (err) {
      toast.show(err?.message || "Failed to update income", "error");
      throw err;
    }
  }

  async function remove(year, month) {
    const toast = useToastStore();
    try {
      await deleteIncome(year, month);
      reset();
      await loadNextPage();
    } catch (err) {
      toast.show(err?.message || "Failed to delete income", "error");
      throw err;
    }
  }

  function setOpenRow(key) {
    openRowId.value = key;
  }

  function reset() {
    items.value = [];
    page.value = 0;
    hasMore.value = true;
    loading.value = false;
    clearCache();
  }

  return {
    items,
    hasMore,
    page,
    loading,
    fromCache,
    openRowId,
    dirtyFlag,
    lastFetchedAt,
    loadIfNeeded,
    loadNextPage,
    add,
    patch,
    remove,
    setOpenRow,
    reset,
  };
});

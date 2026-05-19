import { defineStore } from "pinia";
import { ref } from "vue";
import { getReviewFeed, getReviewCounts, getRecentExpenses } from "../api/review.js";
import { correctCategory, editExpense } from "../api/expenseCorrections.js";
import { useStaleCache } from "../composables/useStaleCache.js";
import { useToastStore } from "./toast.js";
import { useCatalogStore } from "./catalog.js";

const CACHE_KEY = "dinary:review:v1";
const DIRTY_KEY = "dinary:review:dirty";
const FETCHED_KEY = "dinary:review:fetchedAt";

export const useReviewStore = defineStore("review", () => {
  const { dirtyFlag, lastFetchedAt, markDirty, stampFresh, bumpFetchTime, isStale, readCache, writeCache, clearCache } = useStaleCache({
    dirtyKey: DIRTY_KEY,
    fetchedKey: FETCHED_KEY,
    dataKey: CACHE_KEY,
  });
  const cached = (() => {
    const c = readCache();
    if (!Array.isArray(c?.items) || c.items.length === 0) return null;
    return c;
  })();
  const items = ref(cached?.items ?? []);
  const doubtfulCount = ref(cached?.doubtfulCount ?? 0);
  const hasMore = ref(cached?.hasMore ?? true);
  const page = ref(cached?.page ?? 0);
  const loading = ref(false);
  const totalLoaded = ref(cached?.totalLoaded ?? 0);
  const fromCache = ref(!!cached);

  const expenses = ref([]);
  const expensesLoading = ref(false);
  const expensesLoaded = ref(false);
  const openRowId = ref(null);

  function _persistState() {
    writeCache({
      items: items.value,
      doubtfulCount: doubtfulCount.value,
      hasMore: hasMore.value,
      page: page.value,
      totalLoaded: totalLoaded.value,
    });
  }

  async function loadIfNeeded() {
    if (isStale()) {
      reset();
      await loadNextPage();
    }
  }

  async function fetchCounts() {
    try {
      const data = await getReviewCounts();
      doubtfulCount.value = data.doubtful_count ?? 0;
      if ((data.pending_receipts ?? 0) === 0) {
        dirtyFlag.value = false;
        localStorage.removeItem(DIRTY_KEY);
      }
    } catch {
      // best-effort: badge stays at 0 on failure
    }
  }

  async function loadNextPage() {
    if (loading.value) return;
    if (page.value > 0 && !hasMore.value) return;
    loading.value = true;
    try {
      const nextPage = page.value + 1;
      const data = await getReviewFeed({ page: nextPage, pageSize: 20 });
      const existingIds = new Set(items.value.map((i) => i.id));
      const incoming = (data.items ?? []).filter((i) => !existingIds.has(i.id));
      items.value = [...items.value, ...incoming];
      doubtfulCount.value = data.doubtful_count ?? doubtfulCount.value;
      hasMore.value = data.has_more ?? false;
      page.value = nextPage;
      totalLoaded.value += incoming.length;
      fromCache.value = false;
      if ((data.pending_receipts ?? 0) === 0) {
        stampFresh();
      } else {
        bumpFetchTime();
      }
      _persistState();
    } catch (err) {
      if (navigator.onLine) {
        const toast = useToastStore();
        toast.show(err?.message || "Failed to load review feed", "error");
      }
    } finally {
      loading.value = false;
    }
  }

  async function correct(item, categoryId, scope = "all") {
    const toast = useToastStore();
    const catalog = useCatalogStore();
    try {
      const expenseId = item.expense_id ?? item.id;
      const result = await correctCategory(expenseId, categoryId, scope);
      const count = result?.count ?? item.count ?? 1;
      const cat = catalog.findCategoryById(categoryId);
      const catName = cat?.name ?? "";
      if (item.is_doubtful) {
        const filtered = items.value.filter((i) => i.id !== item.id);
        let insertAt = filtered.length;
        for (let i = 0; i < filtered.length; i++) {
          if (!filtered[i].is_doubtful) {
            insertAt = i;
            break;
          }
        }
        filtered.splice(insertAt, 0, {
          ...item,
          is_doubtful: false,
          category_id: categoryId,
          category_name: catName,
        });
        items.value = filtered;
        doubtfulCount.value = Math.max(0, doubtfulCount.value - 1);
      } else {
        const idx = items.value.findIndex((i) => i.id === item.id);
        if (idx !== -1) {
          items.value[idx] = {
            ...items.value[idx],
            category_id: categoryId,
            category_name: catName,
          };
        }
      }
      _persistState();
      toast.show(`Updated ${count} expenses → ${catName} · rule saved`, "success");
    } catch (err) {
      toast.show(err?.message || "Correction failed", "error");
    }
  }

  function setOpenRow(id) {
    openRowId.value = id;
  }

  async function loadRecentExpenses() {
    if (expensesLoading.value) return;
    expensesLoading.value = true;
    try {
      const data = await getRecentExpenses();
      expenses.value = data?.expenses ?? data ?? [];
      expensesLoaded.value = true;
    } catch (err) {
      if (navigator.onLine) {
        const toast = useToastStore();
        toast.show(err?.message || "Failed to load recent expenses", "error");
      }
    } finally {
      expensesLoading.value = false;
    }
  }

  async function updateExpense(id, payload) {
    const toast = useToastStore();
    try {
      const result = await editExpense(id, payload);
      const idx = expenses.value.findIndex((e) => e.id === id);
      if (idx !== -1 && result) {
        expenses.value[idx] = {
          ...expenses.value[idx],
          category_id: result.category_id ?? expenses.value[idx].category_id,
          category_name: result.category_name ?? expenses.value[idx].category_name,
          tag_ids: result.tag_ids ?? expenses.value[idx].tag_ids,
          event_id: result.event_id ?? null,
          event_name: result.event_name ?? null,
        };
      }
    } catch (err) {
      toast.show(err?.message || "Update failed", "error");
      throw err;
    }
  }

  function reset() {
    items.value = [];
    doubtfulCount.value = 0;
    hasMore.value = true;
    page.value = 0;
    loading.value = false;
    totalLoaded.value = 0;
    fromCache.value = false;
    lastFetchedAt.value = null;
    clearCache();
    try {
      localStorage.removeItem(FETCHED_KEY);
    } catch {}
  }

  return {
    items,
    doubtfulCount,
    hasMore,
    page,
    loading,
    totalLoaded,
    fromCache,
    dirtyFlag,
    lastFetchedAt,
    expenses,
    expensesLoading,
    expensesLoaded,
    openRowId,
    markDirty,
    loadIfNeeded,
    fetchCounts,
    loadNextPage,
    correct,
    setOpenRow,
    loadRecentExpenses,
    updateExpense,
    reset,
  };
});

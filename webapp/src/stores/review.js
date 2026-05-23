import { defineStore } from "pinia";
import { ref } from "vue";
import { getReviewFeed, getReviewCounts, getExpensesFeed, confirmAllRules } from "../api/review.js";
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
  const openRowId = ref(null);

  const expenses = ref([]);
  const expensesPage = ref(0);
  const expensesHasMore = ref(true);
  const expensesLoading = ref(false);

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
        items.value = items.value.filter((i) => i.id !== item.id);
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
      if (item.name) {
        expenses.value = expenses.value.map((e) =>
          e.item_name === item.name
            ? { ...e, category_id: categoryId, category_name: catName, confidence_level: null }
            : e,
        );
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

  async function confirmAll(ruleIds) {
    const toast = useToastStore();
    try {
      const result = await confirmAllRules(ruleIds);
      const confirmedCount = result?.confirmed ?? ruleIds.length;
      items.value = items.value.filter((i) => !ruleIds.includes(i.id));
      doubtfulCount.value = Math.max(0, doubtfulCount.value - confirmedCount);
      totalLoaded.value = items.value.length;
      _persistState();
      resetExpenses();
      await loadExpensesNextPage();
      toast.show(`${confirmedCount} rules confirmed`, "success");
    } catch (err) {
      toast.show(err?.message || "Confirm all failed", "error");
    }
  }

  async function loadExpensesNextPage() {
    if (expensesLoading.value) return;
    if (expensesPage.value > 0 && !expensesHasMore.value) return;
    expensesLoading.value = true;
    try {
      const nextPage = expensesPage.value + 1;
      const data = await getExpensesFeed({ page: nextPage, pageSize: 20 });
      const existingIds = new Set(expenses.value.map((e) => e.id));
      const incoming = (data.items ?? []).filter((e) => !existingIds.has(e.id));
      expenses.value = [...expenses.value, ...incoming];
      expensesHasMore.value = data.has_more ?? false;
      expensesPage.value = nextPage;
    } catch (err) {
      if (navigator.onLine) {
        const toast = useToastStore();
        toast.show(err?.message || "Failed to load expenses", "error");
      }
    } finally {
      expensesLoading.value = false;
    }
  }

  async function loadExpensesIfNeeded() {
    if (expensesPage.value === 0) {
      await loadExpensesNextPage();
    }
  }

  function resetExpenses() {
    expenses.value = [];
    expensesPage.value = 0;
    expensesHasMore.value = true;
    expensesLoading.value = false;
  }

  async function updateExpense(id, payload) {
    const toast = useToastStore();
    try {
      await editExpense(id, payload);
      if (payload.update_rule) {
        const removed = items.value.filter((i) => i.expense_id === id && i.is_doubtful);
        if (removed.length > 0) {
          items.value = items.value.filter((i) => !(i.expense_id === id && i.is_doubtful));
          doubtfulCount.value = Math.max(0, doubtfulCount.value - removed.length);
          _persistState();
        }
        const target = expenses.value.find((e) => e.id === id);
        if (target?.item_name) {
          const patch = { confidence_level: null };
          if (payload.scope && payload.scope !== "single" && payload.category_id != null) {
            const catalog = useCatalogStore();
            const cat = catalog.findCategoryById(payload.category_id);
            patch.category_id = payload.category_id;
            patch.category_name = cat?.name ?? "";
          }
          expenses.value = expenses.value.map((e) =>
            e.item_name === target.item_name ? { ...e, ...patch } : e,
          );
        }
      }
    } catch (err) {
      toast.show(err?.message || "Update failed", "error");
      throw err;
    }
  }

  function patchExpense(id, patch) {
    expenses.value = expenses.value.map((e) => (e.id === id ? { ...e, ...patch } : e));
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
    expenses.value = [];
    expensesPage.value = 0;
    expensesHasMore.value = true;
    expensesLoading.value = false;
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
    openRowId,
    expenses,
    expensesPage,
    expensesHasMore,
    expensesLoading,
    markDirty,
    loadIfNeeded,
    fetchCounts,
    loadNextPage,
    correct,
    confirmAll,
    setOpenRow,
    loadExpensesIfNeeded,
    loadExpensesNextPage,
    resetExpenses,
    updateExpense,
    patchExpense,
    reset,
  };
});

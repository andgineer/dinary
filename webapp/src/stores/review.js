import { defineStore } from "pinia";
import { ref } from "vue";
import { getReviewFeed, getReviewCounts } from "../api/review.js";
import { correctCategory } from "../api/expenseCorrections.js";
import { useToastStore } from "./toast.js";
import { useCatalogStore } from "./catalog.js";

const CACHE_KEY = "dinary:review:v1";

function readCache() {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed?.items) || parsed.items.length === 0) return null;
    return parsed;
  } catch {
    return null;
  }
}

function writeCache(state) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(state));
  } catch {
    // Quota / private mode: harmless.
  }
}

function clearCache() {
  try {
    localStorage.removeItem(CACHE_KEY);
  } catch {}
}

export const useReviewStore = defineStore("review", () => {
  const cached = readCache();
  const items = ref(cached?.items ?? []);
  const doubtfulCount = ref(cached?.doubtfulCount ?? 0);
  const hasMore = ref(cached?.hasMore ?? true);
  const page = ref(cached?.page ?? 0);
  const loading = ref(false);
  const totalLoaded = ref(cached?.totalLoaded ?? 0);
  const fromCache = ref(!!cached);

  function _persistState() {
    writeCache({
      items: items.value,
      doubtfulCount: doubtfulCount.value,
      hasMore: hasMore.value,
      page: page.value,
      totalLoaded: totalLoaded.value,
    });
  }

  async function fetchCounts() {
    try {
      const data = await getReviewCounts();
      doubtfulCount.value = data.doubtful_count ?? 0;
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

  function reset() {
    items.value = [];
    doubtfulCount.value = 0;
    hasMore.value = true;
    page.value = 0;
    loading.value = false;
    totalLoaded.value = 0;
    fromCache.value = false;
    clearCache();
  }

  return {
    items,
    doubtfulCount,
    hasMore,
    page,
    loading,
    totalLoaded,
    fromCache,
    fetchCounts,
    loadNextPage,
    correct,
    reset,
  };
});

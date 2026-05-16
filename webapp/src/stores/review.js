import { defineStore } from "pinia";
import { ref } from "vue";
import { getReviewFeed, getReviewCounts } from "../api/review.js";
import { correctCategory } from "../api/expenseCorrections.js";
import { useToastStore } from "./toast.js";
import { useCatalogStore } from "./catalog.js";

export const useReviewStore = defineStore("review", () => {
  const items = ref([]);
  const doubtfulCount = ref(0);
  const hasMore = ref(true);
  const page = ref(0);
  const loading = ref(false);
  const totalLoaded = ref(0);

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
      items.value = [...items.value, ...data.items];
      doubtfulCount.value = data.doubtful_count ?? doubtfulCount.value;
      hasMore.value = data.has_more ?? false;
      page.value = nextPage;
      totalLoaded.value += data.items.length;
    } catch (err) {
      const toast = useToastStore();
      toast.show(err?.message || "Failed to load review feed", "error");
    } finally {
      loading.value = false;
    }
  }

  async function correct(item, categoryId) {
    const toast = useToastStore();
    const catalog = useCatalogStore();
    try {
      const result = await correctCategory(item.id, categoryId);
      const count = result?.count ?? item.count ?? 1;
      const cat = catalog.findCategoryById(categoryId);
      const catName = cat?.name ?? "";
      items.value = items.value.filter((i) => i.id !== item.id);
      doubtfulCount.value = Math.max(0, doubtfulCount.value - 1);
      totalLoaded.value = Math.max(0, totalLoaded.value - 1);
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
  }

  return {
    items,
    doubtfulCount,
    hasMore,
    page,
    loading,
    totalLoaded,
    fetchCounts,
    loadNextPage,
    correct,
    reset,
  };
});

import { defineStore } from "pinia";
import { ref } from "vue";
import { useCatalogStore } from "./catalog.js";

const MS_24H = 24 * 60 * 60 * 1000;

export const useFrequentCategoriesStore = defineStore("frequentCategories", () => {
  const categories = ref([]);
  const lastFetched = ref(null);

  function ensureLoaded() {
    const now = Date.now();
    if (categories.value.length > 0 && lastFetched.value && now - lastFetched.value < MS_24H) return;
    const catalog = useCatalogStore();
    const fresh = catalog.frequentCategories;
    if (fresh.length > 0) {
      categories.value = fresh;
      lastFetched.value = now;
    }
  }

  function refresh(responseData) {
    if (!responseData?.frequent_categories?.length) return;
    categories.value = responseData.frequent_categories;
    lastFetched.value = Date.now();
  }

  return { categories, lastFetched, ensureLoaded, refresh };
});

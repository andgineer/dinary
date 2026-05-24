<script setup>
import { computed, ref, watch, nextTick } from "vue";
import { Check, Sparkles, X } from "lucide-vue-next";
import { useCatalogStore } from "../stores/catalog.js";
import BaseSheet from "./BaseSheet.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  suggestions: { type: Array, default: () => [] },
  title: { type: String, default: "Select category" },
});
const emit = defineEmits(["select", "close"]);

const catalog = useCatalogStore();
const searchEl = ref(null);
const bodyEl = ref(null);
const query = ref("");

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      query.value = "";
      nextTick(() => {
        if (bodyEl.value) bodyEl.value.scrollTop = 0;
        searchEl.value?.focus({ preventScroll: true });
      });
    }
  },
);

const allGroupsWithCategories = computed(() =>
  catalog.groups
    .map((g) => ({ group: g, categories: catalog.categories(g.id) }))
    .filter((gc) => gc.categories.length > 0),
);

const flatResults = computed(() => {
  if (!query.value.trim()) return [];
  const q = query.value.toLowerCase();
  const results = [];
  for (const { group, categories } of allGroupsWithCategories.value) {
    for (const cat of categories) {
      if (cat.name.toLowerCase().includes(q) || group.name.toLowerCase().includes(q)) {
        results.push({ id: cat.id, name: cat.name, groupName: group.name });
      }
    }
  }
  return results;
});

const showSearch = computed(() => query.value.trim().length > 0);

function select(id) {
  emit("select", id);
  emit("close");
}

function onKeydown(e) {
  if (e.key === "Escape") {
    if (query.value) {
      query.value = "";
    } else {
      emit("close");
    }
  }
}
</script>

<template>
  <BaseSheet
    :open="open"
    :full-height="true"
    :z-index="50"
    :aria-label="title"
    data-testid="category-sheet"
    @close="emit('close')"
  >
    <template #header>
      <div class="sheet-eyebrow">{{ title }}</div>
    </template>

    <template #pre-body>
      <div class="search-wrap">
        <input
          ref="searchEl"
          v-model="query"
          type="search"
          placeholder="Search…"
          class="search-input"
          aria-label="Search categories"
          @keydown="onKeydown"
        />
        <button
          v-if="query"
          type="button"
          class="clear-btn"
          aria-label="Clear search"
          @click="query = ''"
        >
          <X :size="14" />
        </button>
      </div>
    </template>

    <div ref="bodyEl">
      <template v-if="showSearch">
        <div class="flat-list" data-testid="flat-results">
          <button
            v-for="item in flatResults"
            :key="item.id"
            type="button"
            class="flat-item"
            @click="select(item.id)"
          >
            <span class="flat-group">{{ item.groupName }}</span>
            <span class="flat-sep"> › </span>
            <span>{{ item.name }}</span>
          </button>
          <div v-if="flatResults.length === 0" class="no-results">No matches</div>
        </div>
      </template>

      <template v-else>
        <template v-if="suggestions.length > 0">
          <div class="section-label">SUGGESTIONS</div>
          <div class="suggestions-row" data-testid="suggestion-pills">
            <button
              v-for="sug in suggestions"
              :key="sug.id"
              type="button"
              class="cat-btn is-suggested"
              @click="select(sug.id)"
            >
              <Sparkles :size="10" class="suggest-icon" aria-hidden="true" />
              {{ sug.name }}
            </button>
          </div>
        </template>

        <div
          v-for="{ group, categories } in allGroupsWithCategories"
          :key="group.id"
          class="group-section"
          data-testid="category-group"
        >
          <div class="group-label">{{ group.name }}</div>
          <div class="categories-grid">
            <button
              v-for="cat in categories"
              :key="cat.id"
              type="button"
              class="cat-btn"
              @click="select(cat.id)"
            >
              {{ cat.name }}
            </button>
          </div>
        </div>
      </template>
    </div>
  </BaseSheet>
</template>

<style scoped>
.sheet-eyebrow {
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--muted);
  text-transform: uppercase;
}

.search-wrap {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--surface);
  margin: 0 1rem 0.5rem;
  flex-shrink: 0;
}

.search-input {
  width: 100%;
  padding: 0.5rem 2rem 0.5rem 0.75rem;
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text);
  font-size: 0.9rem;
}

.clear-btn {
  position: absolute;
  right: 0.5rem;
  top: 50%;
  transform: translateY(-50%);
  background: none;
  border: none;
  color: var(--muted);
  cursor: pointer;
  padding: 0.25rem;
  width: auto;
  display: flex;
  align-items: center;
}

.section-label {
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 0.4rem;
}

.suggestions-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-bottom: 1rem;
}

.group-section {
  margin-bottom: 1rem;
}

.group-label {
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 0.4rem;
}

.categories-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.4rem;
}

.cat-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0.45rem 0.6rem;
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 9px;
  color: var(--text);
  font-size: 0.82rem;
  cursor: pointer;
  width: 100%;
  text-align: left;
  transition: background 0.12s, border-color 0.12s;
}

.cat-btn.is-suggested {
  border-color: rgba(91, 141, 239, 0.4);
}

.suggest-icon {
  color: #7aabff;
  flex-shrink: 0;
}

.flat-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.flat-item {
  display: flex;
  align-items: center;
  padding: 0.5rem 0.5rem;
  background: none;
  border: none;
  border-radius: 8px;
  color: var(--text);
  font-size: 0.88rem;
  cursor: pointer;
  text-align: left;
  width: 100%;
  transition: background 0.1s;
}

.flat-item:hover {
  background: var(--field);
}

.flat-group {
  color: var(--muted);
  font-size: 0.82rem;
}

.flat-sep {
  color: var(--muted-2);
  margin: 0 2px;
}

.no-results {
  color: var(--muted);
  font-size: 0.85rem;
  padding: 1rem 0;
  text-align: center;
}
</style>

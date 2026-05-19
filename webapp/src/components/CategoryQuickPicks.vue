<script setup>
import { Search } from "lucide-vue-next";

const props = defineProps({
  categories: { type: Array, default: () => [] },
});
const emit = defineEmits(["select", "search"]);
</script>

<template>
  <div class="quick-picks" data-testid="category-quick-picks">
    <button
      v-for="cat in categories"
      :key="cat.id"
      type="button"
      class="pick-pill"
      @click="emit('select', cat.id)"
    >
      {{ cat.name }}
    </button>
    <button type="button" class="pick-search" aria-label="Search categories" @click="emit('search')">
      <Search :size="14" aria-hidden="true" />
    </button>
  </div>
</template>

<style scoped>
.quick-picks {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-bottom: 0.75rem;
  align-items: center;
}

.pick-pill {
  padding: 0.3rem 0.7rem;
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--text);
  font-size: 0.8rem;
  cursor: pointer;
  white-space: nowrap;
  transition: border-color 0.12s;
  width: auto;
}

.pick-pill:hover {
  border-color: var(--border-strong);
}

.pick-search {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--muted);
  cursor: pointer;
  flex-shrink: 0;
}

.pick-search:hover {
  color: var(--text);
  border-color: var(--border-strong);
}
</style>

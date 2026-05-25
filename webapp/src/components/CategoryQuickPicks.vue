<script setup>
const props = defineProps({
  categories: { type: Array, default: () => [] },
  selectedCategoryId: { type: Number, default: null },
});
const emit = defineEmits(["select"]);
</script>

<template>
  <div class="quick-picks" data-testid="category-quick-picks">
    <button
      v-for="cat in categories"
      :key="cat.id"
      type="button"
      class="pick-pill"
      :class="{ 'is-selected': cat.id === props.selectedCategoryId }"
      @click="emit('select', cat.id)"
    >
      {{ cat.name }}
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
  transition: border-color 0.12s, background 0.12s, color 0.12s;
  width: auto;
}

.pick-pill:hover {
  border-color: var(--border-strong);
}

.pick-pill.is-selected {
  background: var(--expense);
  border-color: var(--expense);
  color: #fff;
}
</style>

<script setup>
import { computed } from "vue";

const props = defineProps({
  tags: {
    type: Array,
    required: true,
  },
  modelValue: {
    type: Array,
    default: () => [],
  },
  emptyHint: {
    type: String,
    default: "",
  },
});

const emit = defineEmits(["update:modelValue"]);

const selected = computed({
  get: () => props.modelValue ?? [],
  set: (v) => emit("update:modelValue", v),
});

function toggle(id) {
  const set = new Set(selected.value.map(Number));
  const n = Number(id);
  if (set.has(n)) set.delete(n);
  else set.add(n);
  selected.value = Array.from(set);
}

function isChecked(id) {
  return selected.value.map(Number).includes(Number(id));
}

const isEmpty = computed(() => props.tags.length === 0);
</script>

<template>
  <div>
    <div v-if="!isEmpty" class="tags-list" data-testid="tag-picker">
      <label v-for="tag in tags" :key="tag.id" class="tag-chip">
        <input
          type="checkbox"
          name="tag"
          :value="tag.id"
          :checked="isChecked(tag.id)"
          @change="toggle(tag.id)"
        />
        <span>{{ tag.name }}</span>
      </label>
    </div>
    <div v-else-if="emptyHint" class="tag-empty-hint">
      {{ emptyHint }}
    </div>
  </div>
</template>

<style scoped>
.tags-list {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem;
  background: var(--field, rgba(255, 255, 255, 0.04));
  border-radius: 8px;
  border: 1px solid var(--border, rgba(255, 255, 255, 0.08));
}

.tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.25rem 0.6rem;
  background: var(--surface);
  border-radius: 999px;
  font-size: 0.8rem;
  cursor: pointer;
  user-select: none;
  /* Override the global label baseline (uppercase + muted) so chip text
     reads as a tag value, not a form label. */
  text-transform: none;
  letter-spacing: 0;
  color: var(--text);
  margin-bottom: 0;
}

.tag-chip input[type="checkbox"] {
  position: absolute;
  opacity: 0;
  pointer-events: none;
  width: 1px;
  height: 1px;
}

.tag-chip:has(input:checked) {
  background: var(--accent);
  color: #fff;
}

.tag-empty-hint {
  font-size: 0.75rem;
  color: var(--text-muted);
  margin-top: 0.4rem;
}
</style>

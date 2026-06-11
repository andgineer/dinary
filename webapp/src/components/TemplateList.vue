<script setup>
import { Check } from "lucide-vue-next";

const props = defineProps({
  templates: { type: Array, default: () => [] },
  activeCode: { type: String, default: null },
  lang: { type: String, default: "ru" },
});
const emit = defineEmits(["apply"]);

function localized(field, template) {
  return template[field]?.[props.lang] ?? template[field]?.ru ?? "";
}
</script>

<template>
  <div class="template-list" data-testid="template-list">
    <button
      v-for="tpl in templates"
      :key="tpl.code"
      type="button"
      class="template-card"
      :class="{ 'is-active': tpl.code === activeCode }"
      :data-testid="`template-${tpl.code}`"
      @click="emit('apply', tpl.code)"
    >
      <div class="template-name">
        {{ localized("names", tpl) }}
        <Check v-if="tpl.code === activeCode" :size="14" class="active-icon" aria-hidden="true" />
      </div>
      <div class="template-tagline">{{ localized("taglines", tpl) }}</div>
    </button>
  </div>
</template>

<style scoped>
.template-list {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.template-card {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding: 0.75rem 0.9rem;
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 12px;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  transition: background 0.12s, border-color 0.12s;
}

.template-card.is-active {
  border-color: var(--accent);
}

.template-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.95rem;
  font-weight: 600;
}

.active-icon {
  color: var(--accent);
}

.template-tagline {
  font-size: 0.82rem;
  color: var(--muted);
}
</style>

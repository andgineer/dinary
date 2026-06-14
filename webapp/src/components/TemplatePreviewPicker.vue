<script setup>
import { ref, computed, watch } from "vue";
import { Check } from "lucide-vue-next";

const props = defineProps({
  templates: { type: Array, default: () => [] },
  lang: { type: String, default: "ru" },
  activeCode: { type: String, default: null },
  applying: { type: Boolean, default: false },
});
const emit = defineEmits(["apply"]);

const selectedCode = ref(props.activeCode ?? props.templates[0]?.code ?? null);

watch(
  () => props.templates,
  (tpls) => {
    if (!tpls.some((t) => t.code === selectedCode.value)) {
      selectedCode.value = props.activeCode ?? tpls[0]?.code ?? null;
    }
  },
);

function localized(field, obj) {
  return obj?.[field]?.[props.lang] ?? obj?.[field]?.ru ?? "";
}

const selectedTemplate = computed(
  () => props.templates.find((t) => t.code === selectedCode.value) ?? null,
);

function selectChip(code) {
  selectedCode.value = code;
}

function apply() {
  if (props.applying || selectedCode.value === props.activeCode) return;
  emit("apply", selectedCode.value);
}
</script>

<template>
  <div class="template-preview-picker" data-testid="template-preview-picker">
    <div class="chip-row" role="group" aria-label="Category sets">
      <button
        v-for="tpl in templates"
        :key="tpl.code"
        type="button"
        class="set-chip"
        :class="{ 'is-selected': tpl.code === selectedCode }"
        :data-testid="`template-chip-${tpl.code}`"
        @click="selectChip(tpl.code)"
      >
        {{ localized("names", tpl) }}
        <Check v-if="tpl.code === activeCode" :size="12" class="active-badge" aria-hidden="true" />
      </button>
    </div>

    <div v-if="selectedTemplate" class="preview-panel" data-testid="template-preview-panel">
      <p class="preview-tagline">{{ localized("taglines", selectedTemplate) }}</p>
      <div
        v-for="group in selectedTemplate.groups"
        :key="group.code"
        class="preview-group"
        data-testid="preview-group"
      >
        <span class="preview-group-name">{{ localized("names", group) }}:</span>
        <span class="preview-group-categories">
          {{ group.categories.map((c) => localized("names", c)).join(", ") }}
        </span>
      </div>
    </div>

    <button
      type="button"
      class="apply-btn"
      data-testid="apply-template-btn"
      :disabled="applying || selectedCode === activeCode"
      @click="apply"
    >
      Apply this set
    </button>
  </div>
</template>

<style scoped>
.template-preview-picker {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}

.set-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0.35rem 0.8rem;
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--text);
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
  width: auto;
}

.set-chip.is-selected {
  border-color: var(--accent);
  color: var(--accent);
}

.active-badge {
  color: var(--accent);
}

.preview-panel {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  max-height: 50vh;
  overflow-y: auto;
  padding: 0.75rem;
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 12px;
}

.preview-tagline {
  margin: 0 0 0.25rem;
  font-size: 0.82rem;
  color: var(--muted);
}

.preview-group {
  font-size: 0.85rem;
  line-height: 1.4;
}

.preview-group-name {
  font-weight: 700;
  color: var(--text);
}

.preview-group-categories {
  color: var(--muted);
}

.apply-btn {
  padding: 0.6rem 1rem;
  background: var(--accent);
  border: none;
  border-radius: 9px;
  color: #fff;
  font-size: 0.88rem;
  font-weight: 600;
  cursor: pointer;
}

.apply-btn:disabled {
  background: var(--field);
  color: var(--muted);
  cursor: default;
}
</style>

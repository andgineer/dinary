<script setup>
import { BarChart3, Cpu, ListChecks, Plus, TrendingUp } from "lucide-vue-next";

const TABS = [
  { id: "add",       icon: Plus,       color: "var(--expense)" },
  { id: "review",    icon: ListChecks, color: "var(--review)"  },
  { id: "analytics", icon: BarChart3,  color: "var(--stat)"    },
  { id: "income",    icon: TrendingUp, color: "var(--income)"  },
  { id: "llm",       icon: Cpu,        color: "var(--llm)"     },
];

const props = defineProps({
  tab: { type: String, default: "add" },
  showBadge: { type: Boolean, default: false },
});
const emit = defineEmits(["update:tab"]);
</script>

<template>
  <div class="seg-container" role="tablist" aria-label="Navigation">
    <button
      v-for="t in TABS"
      :key="t.id"
      type="button"
      class="seg-btn"
      :class="{ active: tab === t.id }"
      :style="{ '--tab-color': t.color }"
      role="tab"
      :aria-selected="tab === t.id"
      :aria-label="t.id"
      :data-testid="`seg-${t.id}`"
      @click="emit('update:tab', t.id)"
    >
      <component :is="t.icon" :size="20" aria-hidden="true" />
      <span
        v-if="t.id === 'review' && showBadge"
        class="seg-badge"
        aria-label="review attention"
      >!</span>
    </button>
  </div>
</template>

<style scoped>
.seg-container {
  display: flex;
  align-items: center;
  gap: 1px;
  background: var(--field-deep);
  border: 1px solid var(--border);
  border-radius: 11px;
  padding: 3px;
}

.seg-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 36px;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, box-shadow 0.15s;
  background: color-mix(in srgb, var(--tab-color) 14%, transparent);
  color: var(--tab-color);
  padding: 0;
}

.seg-btn:active {
  transform: scale(0.95);
}

.seg-btn.active {
  background: var(--tab-color);
  color: #fff;
  box-shadow: 0 4px 12px color-mix(in srgb, var(--tab-color) 40%, transparent);
}

.seg-badge {
  position: absolute;
  top: -4px;
  right: -4px;
  background: var(--warning);
  color: #000;
  font-size: 0.6rem;
  font-weight: 700;
  line-height: 1;
  padding: 2px 4px;
  border-radius: 999px;
  border: 2px solid var(--surface);
  min-width: 14px;
  text-align: center;
}
</style>

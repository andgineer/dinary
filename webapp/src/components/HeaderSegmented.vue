<script setup>
import { Plus, ListChecks, Cpu } from "lucide-vue-next";

defineProps({
  tab: { type: String, default: "add" },
  doubtfulCount: { type: Number, default: 0 },
});
defineEmits(["update:tab"]);
</script>

<template>
  <div class="seg-container" role="tablist" aria-label="Navigation">
    <button
      type="button"
      class="seg-btn seg-add"
      :class="{ active: tab === 'add' }"
      role="tab"
      :aria-selected="tab === 'add'"
      aria-label="Add expense"
      data-testid="seg-add"
      @click="$emit('update:tab', 'add')"
    >
      <Plus :size="22" aria-hidden="true" />
    </button>

    <button
      type="button"
      class="seg-btn seg-review"
      :class="{ active: tab === 'review' }"
      role="tab"
      :aria-selected="tab === 'review'"
      aria-label="Review"
      data-testid="seg-review"
      @click="$emit('update:tab', 'review')"
    >
      <ListChecks :size="16" aria-hidden="true" />
      <span
        v-if="doubtfulCount > 0"
        class="seg-badge"
        :aria-label="`${doubtfulCount} items need review`"
      >{{ doubtfulCount }}</span>
    </button>

    <button
      type="button"
      class="seg-btn seg-llm"
      :class="{ active: tab === 'llm' }"
      role="tab"
      :aria-selected="tab === 'llm'"
      aria-label="LLM providers"
      data-testid="seg-llm"
      @click="$emit('update:tab', 'llm')"
    >
      <Cpu :size="16" aria-hidden="true" />
    </button>
  </div>
</template>

<style scoped>
.seg-container {
  display: flex;
  align-items: center;
  gap: 2px;
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
  border: none;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, box-shadow 0.15s;
  color: var(--muted);
  background: transparent;
  padding: 0;
  width: auto;
}

.seg-btn:active {
  transform: scale(0.95);
}

.seg-add {
  width: 56px;
  height: 38px;
  background: rgba(91, 141, 239, 0.12);
  color: var(--accent);
}

.seg-add.active {
  background: var(--accent);
  color: #fff;
  box-shadow: 0 4px 12px rgba(91, 141, 239, 0.4);
}

.seg-review,
.seg-llm {
  width: 36px;
  height: 30px;
}

.seg-review.active,
.seg-llm.active {
  background: var(--accent);
  color: #fff;
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

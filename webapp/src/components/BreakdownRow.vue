<script setup>
import { computed } from "vue";

const props = defineProps({
  label: { type: String, required: true },
  sublabel: { type: String, default: null },
  amount: { type: String, required: true },
  currency: { type: String, default: "" },
  width: { type: Number, default: null },
  detail: { type: String, default: null },
  direction: { type: String, default: null },
});

const barWidth = computed(() => `${Math.max(0, Math.min(1, props.width ?? 0)) * 100}%`);
const tone = computed(() => {
  if (props.direction === "up") return "var(--up)";
  if (props.direction === "down") return "var(--down)";
  return null;
});
</script>

<template>
  <div class="detail-row" :data-direction="direction">
    <div class="detail-row-main">
      <span class="detail-name">
        {{ label }}
        <span v-if="sublabel" class="detail-group">{{ sublabel }}</span>
      </span>
      <span class="detail-amount" :style="tone ? { color: tone } : null">
        {{ amount }}
        <span v-if="currency" class="detail-currency">{{ currency }}</span>
      </span>
    </div>
    <span v-if="detail" class="detail-sub">{{ detail }}</span>
    <div v-if="width !== null" class="detail-bar-track" aria-hidden="true">
      <div
        class="detail-bar"
        :style="{ width: barWidth, ...(tone ? { background: tone } : {}) }"
      />
    </div>
  </div>
</template>

<style scoped>
.detail-row {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.detail-row-main {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.detail-name {
  min-width: 0;
  font-size: 0.85rem;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.detail-group {
  margin-left: 4px;
  font-size: 0.7rem;
  color: var(--muted-2);
}

.detail-amount {
  flex-shrink: 0;
  font-family: var(--font-num);
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--text);
}

.detail-currency {
  font-size: 0.7rem;
  color: var(--muted-2);
}

.detail-sub {
  font-family: var(--font-num);
  font-size: 0.7rem;
  color: var(--muted);
}

.detail-bar-track {
  height: 4px;
  border-radius: 999px;
  background: var(--border);
  overflow: hidden;
}

.detail-bar {
  height: 100%;
  border-radius: 999px;
  background: var(--stat);
}
</style>

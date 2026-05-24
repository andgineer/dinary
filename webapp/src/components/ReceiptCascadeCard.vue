<script setup>
defineProps({
  loading: { type: Boolean, default: false },
  cascade: { type: Object, default: null },
});

function formatDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  } catch {
    return iso;
  }
}
</script>

<template>
  <div class="cascade-card" data-testid="cascade-card">
    <div v-if="loading" class="cascade-loading">Loading…</div>
    <template v-else-if="cascade">
      <div class="cascade-header">
        <span class="cascade-merchant">{{ cascade.merchant || "Receipt" }}</span>
        <span class="cascade-date">{{ formatDate(cascade.captured_at) }}</span>
      </div>
      <div v-for="item in cascade.expenses" :key="item.id" class="cascade-row">
        <span class="cascade-item-name">{{ item.item_name || "—" }}</span>
        <span class="cascade-item-amount">{{ item.amount }} {{ item.currency }}</span>
      </div>
      <div class="cascade-footer">
        <span class="cascade-total-label">TOTAL</span>
        <span class="cascade-total-amount">
          {{ cascade.total.amount.toFixed(2) }} {{ cascade.total.currency }}
        </span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.cascade-card {
  background: var(--field-deep);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  font-size: 0.85rem;
}

.cascade-loading {
  padding: 1rem;
  color: var(--muted);
  font-size: 0.85rem;
  text-align: center;
}

.cascade-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 0.6rem 0.75rem;
  border-bottom: 1px solid var(--border);
}

.cascade-merchant {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--text);
}

.cascade-date {
  font-family: var(--font-num);
  font-size: 0.78rem;
  color: var(--muted);
}

.cascade-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 0.35rem 0.75rem;
  gap: 0.5rem;
}

.cascade-item-name {
  flex: 1;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cascade-item-amount {
  font-family: var(--font-num);
  font-size: 0.82rem;
  color: var(--muted);
  flex-shrink: 0;
}

.cascade-footer {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 0.5rem 0.75rem;
  border-top: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.02);
}

.cascade-total-label {
  font-size: 0.7rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
}

.cascade-total-amount {
  font-family: var(--font-num);
  font-weight: 700;
  color: var(--text);
}
</style>

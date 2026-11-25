<script setup>
defineProps({
  state: { type: String, required: true },
  emptyText: { type: String, default: "No expenses yet" },
});
</script>

<template>
  <div class="breakdown-panel" :data-state="state">
    <div v-if="state === 'loading'" class="detail-skeleton">
      <div class="skeleton skeleton-line" />
      <div class="skeleton skeleton-line" />
      <div class="skeleton skeleton-line" />
    </div>
    <p v-else-if="state === 'offline'" class="detail-note">
      Offline — details unavailable
    </p>
    <p v-else-if="state === 'error'" class="detail-note">
      Failed to load details
    </p>
    <p v-else-if="state === 'empty'" class="detail-note">
      {{ emptyText }}
    </p>
    <slot v-else />
  </div>
</template>

<style scoped>
.breakdown-panel {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  padding: 0 0.85rem 0.8rem;
}

.detail-note {
  margin: 0;
  font-size: 0.8rem;
  color: var(--muted);
}

.detail-skeleton {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.skeleton {
  background: var(--field);
  border-radius: 6px;
  animation: pulse 1.4s ease-in-out infinite;
}

.skeleton-line { height: 14px; }

@keyframes pulse {
  0%, 100% { opacity: 0.5; }
  50% { opacity: 1; }
}
</style>

<script setup>
defineProps({
  kind: {
    type: String,
    default: "off",
    validator: (v) => ["ok", "rate_limited", "off", "error"].includes(v),
  },
});
</script>

<template>
  <span class="status-dot" :class="`dot-${kind}`" aria-hidden="true" />
</template>

<style scoped>
.status-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.dot-ok {
  background: var(--success);
  box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.2);
}

.dot-rate_limited {
  background: var(--warning);
  box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.2);
}

.dot-off {
  background: var(--muted);
}

.dot-error {
  background: var(--error);
  box-shadow: 0 0 0 3px rgba(239, 68, 68, 0.2);
}
</style>

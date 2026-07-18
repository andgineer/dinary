<script setup>
import { computed } from "vue";
import StatusDot from "./StatusDot.vue";

const props = defineProps({
  health: { type: Object, default: null },
});

const dotKind = computed(() => {
  if (!props.health) return "off";
  return props.health.healthy > 0 ? "ok" : "error";
});

</script>

<template>
  <div class="health-card" data-testid="health-summary-card">
    <div class="health-main">
      <StatusDot :kind="dotKind" />
      <span class="health-count">
        {{ health?.healthy ?? 0 }} / {{ health?.total ?? 0 }} healthy
      </span>
    </div>
  </div>
</template>

<style scoped>
.health-card {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.75rem 1rem;
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 10px;
  margin-bottom: 0.25rem;
}

.health-main {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex: 1;
}

.health-count {
  font-size: 0.9rem;
  font-weight: 500;
}
</style>

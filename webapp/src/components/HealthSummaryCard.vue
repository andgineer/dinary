<script setup>
import { computed } from "vue";
import { Plus } from "lucide-vue-next";
import StatusDot from "./StatusDot.vue";

const props = defineProps({
  health: { type: Object, default: null },
});
defineEmits(["add"]);

const dotKind = computed(() => {
  if (!props.health) return "off";
  return props.health.healthy > 0 ? "ok" : "error";
});

function relativeTime(value) {
  if (!value) return "";
  const ts = typeof value === "number" ? value * 1000 : new Date(value).getTime();
  const diff = Math.floor((Date.now() - ts) / 1000);
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

const lastSwitch = computed(() => relativeTime(props.health?.last_switch));
</script>

<template>
  <div class="health-card" data-testid="health-summary-card">
    <div class="health-main">
      <StatusDot :kind="dotKind" />
      <span class="health-count">
        {{ health?.healthy ?? 0 }} / {{ health?.total ?? 0 }} healthy
      </span>
    </div>
    <button
      type="button"
      class="add-btn"
      aria-label="Add provider"
      data-testid="add-provider-btn"
      @click="$emit('add')"
    >
      <Plus :size="16" />
    </button>
  </div>
  <div v-if="health?.strategy" class="health-sub">
    {{ health.strategy }} · last switch {{ lastSwitch }}
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

.add-btn {
  background: none;
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--accent);
  cursor: pointer;
  padding: 0.3rem;
  width: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: border-color 0.12s, background 0.12s;
}

.add-btn:hover {
  border-color: var(--accent);
  background: rgba(233, 69, 96, 0.08);
}

.health-sub {
  font-size: 0.75rem;
  color: var(--muted);
  padding: 0 0.25rem 0.5rem;
}
</style>

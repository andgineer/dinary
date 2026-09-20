<script setup>
import { onBeforeUnmount, onMounted } from "vue";
import { useLlmStore } from "../stores/llm.js";
import { useNow } from "../composables/useNow.js";
import { useOnline } from "../composables/useOnline.js";
import { useToastStore } from "../stores/toast.js";
import HealthSummaryCard from "../components/HealthSummaryCard.vue";
import ProviderCard from "../components/ProviderCard.vue";
import IconBtn from "../components/IconBtn.vue";

const llmStore = useLlmStore();
const { isOnline } = useOnline();
const now = useNow();
const toast = useToastStore();

let refreshTimer = null;

function requireOnline() {
  if (!isOnline.value) {
    toast.show("Not available offline", "info");
    return false;
  }
  return true;
}

// `status` is derived server-side at fetch time, so a badge left saying "cooling down"
// outlives its deadline unless the expired cooldown itself asks for a refetch.
function hasExpiredCooldown() {
  return llmStore.providers.some(
    (p) =>
      p.status === "cooling" &&
      p.cooldown_until &&
      new Date(p.cooldown_until).getTime() <= Date.now(),
  );
}

onMounted(async () => {
  if (isOnline.value) await llmStore.loadIfNeeded();
  refreshTimer = setInterval(() => {
    if (!isOnline.value) return;
    if (llmStore.dirtyFlag || hasExpiredCooldown()) llmStore.refresh();
  }, 30_000);
});

onBeforeUnmount(() => {
  clearInterval(refreshTimer);
});
</script>

<template>
  <div class="llm-view" data-testid="llm-view">
    <HealthSummaryCard :health="llmStore.health" />

    <div class="pool-header">
      <span class="pool-label">PROVIDER POOL</span>
      <IconBtn
        icon="refresh"
        tone="muted"
        label="Refresh"
        :disabled="!isOnline || llmStore.loading"
        @click="llmStore.refresh()"
      />
    </div>

    <div v-if="llmStore.loading && llmStore.providers.length === 0" class="loading-hint">
      Loading…
    </div>

    <ProviderCard
      v-for="provider in llmStore.providers"
      :key="provider.name"
      :provider="provider"
      :now="now"
      @toggle="isOnline ? llmStore.toggleDisabled(provider.name) : requireOnline()"
    />

    <div
      v-if="!llmStore.loading && llmStore.providers.length === 0"
      class="empty-state"
    >
      No providers configured — the model list has not been synced yet.
    </div>
  </div>
</template>

<style scoped>
.llm-view {
  padding: 1rem 1.25rem;
  max-width: 480px;
  width: 100%;
  margin: 0 auto;
}

.pool-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.5rem 0.25rem 0.4rem;
}

.pool-label {
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--muted);
}

.loading-hint,
.empty-state {
  color: var(--muted);
  font-size: 0.85rem;
  padding: 1.5rem 0;
  text-align: center;
}
</style>

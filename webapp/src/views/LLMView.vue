<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useLlmStore } from "../stores/llm.js";
import { useOnline } from "../composables/useOnline.js";
import { useToastStore } from "../stores/toast.js";
import HealthSummaryCard from "../components/HealthSummaryCard.vue";
import ProviderCard from "../components/ProviderCard.vue";
import IconBtn from "../components/IconBtn.vue";

const llmStore = useLlmStore();
const { isOnline } = useOnline();
const toast = useToastStore();

let refreshTimer = null;

function requireOnline() {
  if (!isOnline.value) {
    toast.show("Not available offline", "info");
    return false;
  }
  return true;
}

onMounted(async () => {
  if (isOnline.value) await llmStore.loadIfNeeded();
  refreshTimer = setInterval(() => {
    if (isOnline.value && llmStore.dirtyFlag) llmStore.refresh();
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
      <span class="pool-hint">from .deploy/llms.toml</span>
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
      @toggle="isOnline ? llmStore.toggleDisabled(provider.name) : requireOnline()"
    />

    <div
      v-if="!llmStore.loading && llmStore.providers.length === 0"
      class="empty-state"
    >
      No providers configured — add them to <code>.deploy/llms.toml</code>.
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

.pool-hint {
  font-size: 0.7rem;
  color: var(--muted-2);
  margin-right: auto;
  margin-left: 0.5rem;
  font-family: var(--font-num);
}

.loading-hint,
.empty-state {
  color: var(--muted);
  font-size: 0.85rem;
  padding: 1.5rem 0;
  text-align: center;
}
</style>

<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useLlmStore } from "../stores/llm.js";
import HealthSummaryCard from "../components/HealthSummaryCard.vue";
import ProviderCard from "../components/ProviderCard.vue";
import ProviderSheet from "../components/ProviderSheet.vue";

const llmStore = useLlmStore();

const sheetOpen = ref(false);
const editingProvider = ref(null);
let refreshTimer = null;

function openAdd() {
  editingProvider.value = null;
  sheetOpen.value = true;
}

function openEdit(provider) {
  editingProvider.value = provider;
  sheetOpen.value = true;
}

function closeSheet() {
  sheetOpen.value = false;
  editingProvider.value = null;
}

onMounted(async () => {
  await llmStore.refresh();
  refreshTimer = setInterval(() => llmStore.refresh(), 30_000);
});

onBeforeUnmount(() => {
  clearInterval(refreshTimer);
});
</script>

<template>
  <div class="llm-view" data-testid="llm-view">
    <HealthSummaryCard :health="llmStore.health" @add="openAdd" />

    <div class="pool-header">
      <span class="pool-label">PROVIDER POOL</span>
      <span class="pool-sort">priority</span>
    </div>

    <div v-if="llmStore.loading && llmStore.providers.length === 0" class="loading-hint">
      Loading…
    </div>

    <ProviderCard
      v-for="(provider, idx) in llmStore.providers"
      :key="provider.id"
      :provider="provider"
      :is-first="idx === 0"
      :is-last="idx === llmStore.providers.length - 1"
      @edit="openEdit(provider)"
      @toggle="llmStore.toggle(provider.id)"
      @move-up="llmStore.move(provider.id, 'up')"
      @move-down="llmStore.move(provider.id, 'down')"
      @test="llmStore.test(provider.id)"
    />

    <div
      v-if="!llmStore.loading && llmStore.providers.length === 0"
      class="empty-state"
    >
      No providers yet — tap + to add one.
    </div>
  </div>

  <ProviderSheet :open="sheetOpen" :provider="editingProvider" @close="closeSheet" />
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

.pool-sort {
  font-size: 0.7rem;
  color: var(--muted-2);
}

.loading-hint,
.empty-state {
  color: var(--muted);
  font-size: 0.85rem;
  padding: 1.5rem 0;
  text-align: center;
}
</style>

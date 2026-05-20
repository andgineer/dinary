<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useLlmStore } from "../stores/llm.js";
import { useOnline } from "../composables/useOnline.js";
import { useToastStore } from "../stores/toast.js";
import HealthSummaryCard from "../components/HealthSummaryCard.vue";
import ProviderCard from "../components/ProviderCard.vue";
import ProviderSheet from "../components/ProviderSheet.vue";
import IconBtn from "../components/IconBtn.vue";

const llmStore = useLlmStore();
const { isOnline } = useOnline();
const toast = useToastStore();

const sheetOpen = ref(false);
const editingProvider = ref(null);
let refreshTimer = null;

function requireOnline() {
  if (!isOnline.value) {
    toast.show("Not available offline", "info");
    return false;
  }
  return true;
}

function openAdd() {
  if (!requireOnline()) return;
  editingProvider.value = null;
  sheetOpen.value = true;
}

function openEdit(provider) {
  if (!requireOnline()) return;
  editingProvider.value = provider;
  sheetOpen.value = true;
}

function closeSheet() {
  sheetOpen.value = false;
  editingProvider.value = null;
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
    <HealthSummaryCard :health="llmStore.health" @add="openAdd" />

    <div class="pool-header">
      <span class="pool-label">PROVIDER POOL</span>
      <span class="pool-sort">priority</span>
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
      v-for="(provider, idx) in llmStore.providers"
      :key="provider.id"
      :provider="provider"
      :is-first="idx === 0"
      :is-last="idx === llmStore.providers.length - 1"
      @edit="openEdit(provider)"
      @toggle="isOnline ? llmStore.toggle(provider.id) : requireOnline()"
      @move-up="isOnline ? llmStore.move(provider.id, 'up') : requireOnline()"
      @move-down="isOnline ? llmStore.move(provider.id, 'down') : requireOnline()"
      @test="isOnline ? llmStore.test(provider.id) : requireOnline()"
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
  margin-right: auto;
  margin-left: 0.5rem;
}

.loading-hint,
.empty-state {
  color: var(--muted);
  font-size: 0.85rem;
  padding: 1.5rem 0;
  text-align: center;
}
</style>

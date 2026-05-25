<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { RefreshCw, TrendingUp } from "lucide-vue-next";
import IncomeForm from "../components/IncomeForm.vue";
import IncomeRow from "../components/IncomeRow.vue";
import IncomeEditSheet from "../components/IncomeEditSheet.vue";
import KeyboardSaveBar from "../components/KeyboardSaveBar.vue";
import { useIncomeStore } from "../stores/income.js";
import { useOnline } from "../composables/useOnline.js";
import { useKeyboardVisible } from "../composables/useKeyboardVisible.js";

const incomeStore = useIncomeStore();
const { isOnline } = useOnline();
const { keyboardVisible, keyboardBottom } = useKeyboardVisible();

const incomeForm = ref(null);
const editSheetOpen = ref(false);
const editingIncome = ref(null);
const sentinel = ref(null);
let observer = null;

const groupedByYear = computed(() => {
  const groups = new Map();
  for (const item of incomeStore.items) {
    if (!groups.has(item.year)) groups.set(item.year, []);
    groups.get(item.year).push(item);
  }
  return [...groups.entries()].sort((a, b) => b[0] - a[0]);
});

const cacheAgeLabel = computed(() => {
  const ts = incomeStore.lastFetchedAt;
  if (!ts) return null;
  const mins = Math.floor((Date.now() - ts) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  return hrs < 24 ? `${hrs}h ago` : `${Math.floor(hrs / 24)}d ago`;
});

function yearTotal(yearItems) {
  const sum = yearItems.reduce((acc, item) => acc + item.amount, 0);
  const cur = yearItems[0]?.currency ?? "";
  return `+${sum.toFixed(2)} ${cur}`;
}

function openEdit(income) {
  editingIncome.value = income;
  editSheetOpen.value = true;
}

function closeEdit() {
  editSheetOpen.value = false;
  editingIncome.value = null;
}

async function forceRefresh() {
  if (!isOnline.value || incomeStore.loading) return;
  incomeStore.reset();
  await incomeStore.loadNextPage();
}

function saveIncome() {
  incomeForm.value?.save?.();
}

function setupObserver() {
  if (typeof IntersectionObserver === "undefined") return;
  if (!sentinel.value) return;
  observer = new IntersectionObserver(
    (entries) => {
      if (entries[0].isIntersecting && !incomeStore.loading && incomeStore.hasMore && isOnline.value) {
        incomeStore.loadNextPage();
      }
    },
    { rootMargin: "120px" },
  );
  observer.observe(sentinel.value);
}

onMounted(async () => {
  if (isOnline.value) {
    await incomeStore.loadIfNeeded();
  }
  setupObserver();
});

onBeforeUnmount(() => {
  if (observer) observer.disconnect();
});
</script>

<template>
  <KeyboardSaveBar v-if="keyboardVisible" :bottom="keyboardBottom" accent-color="var(--success)" @save="saveIncome" />

  <div class="income-view">
    <IncomeForm ref="incomeForm" :disabled="!isOnline" />

    <div class="section-header">
      <span class="section-label">INCOMES</span>
      <span v-if="incomeStore.items.length > 0" class="section-badge">{{ incomeStore.items.length }}</span>
      <span v-if="cacheAgeLabel" class="section-age">{{ cacheAgeLabel }}</span>
      <button
        type="button"
        class="refresh-btn"
        :disabled="!isOnline || incomeStore.loading"
        aria-label="Refresh"
        @click="forceRefresh"
      >
        <RefreshCw :size="14" aria-hidden="true" />
      </button>
    </div>

    <template v-if="incomeStore.items.length > 0">
      <template v-for="[year, yearItems] in groupedByYear" :key="year">
        <div class="year-header">
          <span class="year-label">{{ year }}</span>
          <span class="year-total">{{ yearTotal(yearItems) }}</span>
        </div>
        <IncomeRow
          v-for="income in yearItems"
          :key="income.id"
          :income="income"
          @tap="openEdit(income)"
        />
      </template>
    </template>

    <div v-else-if="!incomeStore.loading" class="empty-state">
      <div class="empty-icon">
        <TrendingUp :size="24" />
      </div>
      <p class="empty-title">No incomes yet</p>
      <p class="empty-subtitle">Add your first income above</p>
    </div>

    <div v-if="incomeStore.loading" class="skeleton-rows" aria-label="Loading">
      <div class="skeleton-row" />
      <div class="skeleton-row" />
    </div>

    <div ref="sentinel" class="scroll-sentinel" aria-hidden="true" />
  </div>

  <footer class="action-bar">
    <div class="action-bar-inner">
      <button
        type="button"
        class="btn-save-income"
        :class="{ 'btn-save-income--disabled': !isOnline }"
        :disabled="!isOnline"
        @click="saveIncome"
      >
        Save
      </button>
    </div>
  </footer>

  <IncomeEditSheet :open="editSheetOpen" :income="editingIncome" @close="closeEdit" />
</template>

<style scoped>
.income-view {
  padding: 1.25rem;
  padding-bottom: 2rem;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem 0 0.5rem;
}

.section-label {
  font-size: 0.6875rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--success);
}

.section-badge {
  background: var(--field);
  color: var(--text);
  font-size: 0.65rem;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 999px;
  min-width: 18px;
  text-align: center;
}

.section-age {
  font-size: 0.65rem;
  color: var(--muted-2);
  font-style: italic;
}

.refresh-btn {
  margin-left: auto;
  background: none;
  border: none;
  color: var(--muted);
  cursor: pointer;
  padding: 4px;
  display: flex;
  align-items: center;
  width: auto;
}

.refresh-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.year-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.4rem 0 0.25rem;
}

.year-label {
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
}

.year-total {
  font-family: var(--font-num);
  font-size: 0.72rem;
  color: var(--success);
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 3rem 1rem;
  background: var(--field);
  border: 1px dashed var(--border);
  border-radius: 12px;
  margin-top: 0.5rem;
}

.empty-icon {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: rgba(34, 197, 94, 0.15);
  color: var(--success);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 0.75rem;
}

.empty-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--text);
  margin: 0 0 0.25rem;
}

.empty-subtitle {
  font-size: 0.82rem;
  color: var(--muted);
  text-align: center;
  max-width: 240px;
  margin: 0;
}

.skeleton-rows {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-top: 0.5rem;
}

.skeleton-row {
  height: 62px;
  background: var(--field);
  border-radius: 0 10px 10px 0;
  border: 1px solid var(--border);
  border-left: 4px solid rgba(34, 197, 94, 0.3);
  animation: skeleton-pulse 1.4s ease-in-out infinite;
}

@keyframes skeleton-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.scroll-sentinel {
  height: 1px;
}

.action-bar {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  z-index: 10;
  background: var(--surface);
  border-top: 1px solid var(--border);
  padding: 0.75rem 1.25rem calc(0.75rem + env(safe-area-inset-bottom, 0px));
}

.action-bar-inner {
  max-width: 480px;
  margin: 0 auto;
}

.btn-save-income {
  width: 100%;
  height: 52px;
  background: var(--success);
  color: #04140a;
  border: none;
  border-radius: 14px;
  font-size: 1rem;
  font-weight: 700;
  cursor: pointer;
  box-shadow: 0 4px 14px rgba(34, 197, 94, 0.35);
  transition: box-shadow 0.15s, background 0.15s;
}

.btn-save-income--disabled {
  background: var(--surface-2);
  color: var(--muted);
  cursor: not-allowed;
  box-shadow: none;
}
</style>

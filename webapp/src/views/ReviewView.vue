<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useReviewStore } from "../stores/review.js";
import { useOnline } from "../composables/useOnline.js";
import { useToastStore } from "../stores/toast.js";
import RuleRow from "../components/RuleRow.vue";
import ExpenseRow from "../components/ExpenseRow.vue";
import ExpenseEditSheet from "../components/ExpenseEditSheet.vue";
import IconBtn from "../components/IconBtn.vue";

const reviewStore = useReviewStore();
const { isOnline } = useOnline();
const toast = useToastStore();

const doubtfulItems = computed(() => reviewStore.items.filter((i) => i.is_doubtful));

const expenseEditOpen = ref(false);
const editingExpense = ref(null);
const editingSuggestions = ref([]);
const editingRuleItem = ref(null);

const rulesSentinel = ref(null);
const expensesSentinel = ref(null);
let rulesObserver = null;
let expensesObserver = null;

function openExpenseEdit(expense, suggestions = [], ruleItem = null) {
  if (!isOnline.value) { toast.show("Not available offline", "info"); return; }
  editingExpense.value = expense;
  editingSuggestions.value = suggestions;
  editingRuleItem.value = ruleItem;
  expenseEditOpen.value = true;
}

function closeExpenseEdit() {
  expenseEditOpen.value = false;
  editingExpense.value = null;
  editingSuggestions.value = [];
  editingRuleItem.value = null;
}

async function approveItem({ item, categoryId }) {
  if (!isOnline.value) { toast.show("Not available offline", "info"); return; }
  await reviewStore.correct(item, categoryId, "all");
}

async function handleConfirmAll() {
  if (!isOnline.value) { toast.show("Not available offline", "info"); return; }
  const ruleIds = doubtfulItems.value.map((i) => i.id);
  await reviewStore.confirmAll(ruleIds);
}

async function forceRefresh() {
  if (!isOnline.value) { toast.show("Not available offline", "info"); return; }
  reviewStore.reset();
  await Promise.all([reviewStore.loadNextPage(), reviewStore.loadExpensesNextPage()]);
}

function setupObservers() {
  if (typeof IntersectionObserver === "undefined") return;

  if (rulesSentinel.value) {
    rulesObserver = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && !reviewStore.loading && reviewStore.hasMore && isOnline.value) {
          reviewStore.loadNextPage();
        }
      },
      { rootMargin: "120px" },
    );
    rulesObserver.observe(rulesSentinel.value);
  }

  if (expensesSentinel.value) {
    expensesObserver = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && !reviewStore.expensesLoading && reviewStore.expensesHasMore && isOnline.value) {
          reviewStore.loadExpensesNextPage();
        }
      },
      { rootMargin: "120px" },
    );
    expensesObserver.observe(expensesSentinel.value);
  }
}

onMounted(async () => {
  if (isOnline.value) {
    await Promise.all([reviewStore.loadIfNeeded(), reviewStore.loadExpensesIfNeeded()]);
  }
  setupObservers();
});

onBeforeUnmount(() => {
  if (rulesObserver) rulesObserver.disconnect();
  if (expensesObserver) expensesObserver.disconnect();
});
</script>

<template>
  <div class="review-view" data-testid="review-view">
    <div v-if="!isOnline" class="offline-notice">
      {{ reviewStore.items.length > 0 ? 'Offline — showing cached data' : 'Offline — no cached data' }}
    </div>

    <div class="review-header">
      <div
        v-if="reviewStore.doubtfulCount > 0"
        class="section-header section-header--warning"
      >
        <span class="section-label">NEEDS REVIEW</span>
        <span class="section-badge">{{ reviewStore.doubtfulCount }}</span>
        <span class="section-sort">by impact</span>
      </div>
      <IconBtn
        icon="refresh"
        tone="muted"
        label="Refresh"
        :disabled="!isOnline || reviewStore.loading"
        @click="forceRefresh()"
      />
    </div>

    <template v-for="item in reviewStore.items" :key="item.id">
      <RuleRow
        :item="item"
        @tap="openExpenseEdit(null, item.alternative_categories ?? [], item)"
        @approve="approveItem($event)"
      />
    </template>

<div v-if="reviewStore.loading" class="skeleton-rows" aria-label="Loading">
      <div class="skeleton-row" />
      <div class="skeleton-row" />
    </div>

    <div ref="rulesSentinel" class="scroll-sentinel" aria-hidden="true" />

    <div
      v-if="!reviewStore.hasMore && !reviewStore.loading && doubtfulItems.length > 0"
      class="confirm-all-wrap"
    >
      <button
        type="button"
        class="confirm-all-btn"
        data-testid="confirm-all-btn"
        @click="handleConfirmAll"
      >
        Confirm all ({{ doubtfulItems.length }})
      </button>
    </div>

    <!-- Expenses section -->
    <div class="section-header expenses-header">
      <span class="section-label">EXPENSES</span>
    </div>

    <template v-for="expense in reviewStore.expenses" :key="expense.id">
      <ExpenseRow
        :expense="expense"
        @tap="openExpenseEdit(expense)"
      />
    </template>

    <div v-if="reviewStore.expensesLoading" class="skeleton-rows" aria-label="Loading expenses">
      <div class="skeleton-row" />
    </div>

    <div
      v-if="!reviewStore.expensesLoading && !reviewStore.expensesHasMore && reviewStore.expenses.length === 0"
      class="empty-state"
    >
      <p class="empty-text">No expenses yet</p>
    </div>

    <div ref="expensesSentinel" class="scroll-sentinel" aria-hidden="true" />
  </div>

  <ExpenseEditSheet
    :open="expenseEditOpen"
    :expense="editingExpense"
    :suggestions="editingSuggestions"
    :rule-item="editingRuleItem"
    @close="closeExpenseEdit"
  />
</template>

<style scoped>
.review-view {
  padding: 1rem 1.25rem;
  padding-bottom: 2rem;
  max-width: 480px;
  width: 100%;
  margin: 0 auto;
}

.review-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 0.25rem;
  margin-bottom: 0.5rem;
}

.expenses-header {
  margin-top: 1.5rem;
  margin-bottom: 0.5rem;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0 0.25rem;
  flex: 1;
}

.section-label {
  font-size: 0.6875rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
}

.section-header--warning .section-label {
  color: var(--warning);
}

.section-badge {
  background: var(--warning);
  color: #000;
  font-size: 0.65rem;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 999px;
  min-width: 18px;
  text-align: center;
}

.section-sort {
  margin-left: auto;
  font-size: 0.7rem;
  color: var(--muted);
}

.offline-notice {
  text-align: center;
  font-size: 0.8rem;
  color: var(--muted);
  padding: 0.5rem 0 0.25rem;
}

.empty-state {
  padding: 3rem 1rem;
  text-align: center;
}

.empty-text {
  color: var(--muted);
  font-size: 0.9rem;
}

.skeleton-rows {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.skeleton-row {
  height: 72px;
  background: var(--field);
  border-radius: 10px;
  border: 1px solid var(--border);
  animation: skeleton-pulse 1.4s ease-in-out infinite;
}

@keyframes skeleton-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.scroll-sentinel {
  height: 1px;
}

.confirm-all-wrap {
  padding: 0.75rem 0;
  display: flex;
  justify-content: center;
}

.confirm-all-btn {
  padding: 0.5rem 1.5rem;
  background: rgba(34, 197, 94, 0.15);
  border: 1px solid rgba(34, 197, 94, 0.3);
  border-radius: 999px;
  color: var(--success);
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.12s;
}

.confirm-all-btn:hover {
  background: rgba(34, 197, 94, 0.25);
}
</style>

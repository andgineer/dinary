<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useReviewStore } from "../stores/review.js";
import ExpenseRow from "../components/ExpenseRow.vue";
import CorrectionSheet from "../components/CorrectionSheet.vue";

const reviewStore = useReviewStore();

const correctionItem = ref(null);
const correctionOpen = ref(false);
const sentinel = ref(null);
let observer = null;

const doubtfulItems = computed(() => reviewStore.items.filter((i) => i.is_doubtful));

function openCorrection(item) {
  correctionItem.value = item;
  correctionOpen.value = true;
}

function closeCorrection() {
  correctionOpen.value = false;
  correctionItem.value = null;
}

function isFirstCertain(index) {
  return (
    !reviewStore.items[index].is_doubtful &&
    (index === 0 || reviewStore.items[index - 1].is_doubtful)
  );
}

function setupObserver() {
  if (!sentinel.value || typeof IntersectionObserver === "undefined") return;
  observer = new IntersectionObserver(
    (entries) => {
      if (entries[0].isIntersecting && !reviewStore.loading && reviewStore.hasMore) {
        reviewStore.loadNextPage();
      }
    },
    { rootMargin: "120px" },
  );
  observer.observe(sentinel.value);
}

onMounted(async () => {
  if (reviewStore.items.length === 0) {
    await reviewStore.loadNextPage();
  }
  setupObserver();
});

onBeforeUnmount(() => {
  if (observer) observer.disconnect();
});
</script>

<template>
  <div class="review-view" data-testid="review-view">
    <div
      v-if="reviewStore.doubtfulCount > 0 || doubtfulItems.length > 0"
      class="section-header section-header--warning"
    >
      <span class="section-label">NEEDS REVIEW</span>
      <span class="section-badge">{{ reviewStore.doubtfulCount }}</span>
      <span class="section-sort">by impact</span>
    </div>

    <template v-for="(item, index) in reviewStore.items" :key="item.id">
      <div v-if="isFirstCertain(index)" class="section-divider">
        <span class="divider-text">ALL EXPENSES</span>
      </div>
      <ExpenseRow :item="item" @tap="openCorrection(item)" />
    </template>

    <div
      v-if="!reviewStore.loading && reviewStore.items.length === 0 && !reviewStore.hasMore"
      class="empty-state"
    >
      <p class="empty-text">All caught up!</p>
    </div>

    <div v-if="reviewStore.loading" class="skeleton-rows" aria-label="Loading">
      <div class="skeleton-row" />
      <div class="skeleton-row" />
    </div>

    <div ref="sentinel" class="scroll-sentinel" aria-hidden="true" />

    <div
      v-if="!reviewStore.hasMore && !reviewStore.loading && reviewStore.items.length > 0"
      class="list-end"
    >
      ─── end · {{ reviewStore.totalLoaded }} loaded ───
    </div>
  </div>

  <CorrectionSheet :open="correctionOpen" :item="correctionItem" @close="closeCorrection" />
</template>

<style scoped>
.review-view {
  padding: 1rem 1.25rem;
  padding-bottom: 2rem;
  max-width: 480px;
  width: 100%;
  margin: 0 auto;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
  padding: 0 0.25rem;
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

.section-divider {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin: 1rem 0 0.5rem;
}

.section-divider::before,
.section-divider::after {
  content: "";
  flex: 1;
  height: 1px;
  background: var(--border);
}

.divider-text {
  font-size: 0.6875rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--muted);
  white-space: nowrap;
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

.list-end {
  text-align: center;
  font-size: 0.75rem;
  color: var(--muted-2);
  padding: 1rem 0;
}
</style>

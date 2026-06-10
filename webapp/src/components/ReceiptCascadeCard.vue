<script setup>
import { computed, ref } from "vue";
import CategorySheet from "./CategorySheet.vue";
import { useReviewStore } from "../stores/review.js";
import { useToastStore } from "../stores/toast.js";

const STUCK_THRESHOLD_MS = 5 * 60_000;

const props = defineProps({
  loading: { type: Boolean, default: false },
  cascade: { type: Object, default: null },
});
const emit = defineEmits(["resolved"]);

const reviewStore = useReviewStore();
const toast = useToastStore();
const categorySheetOpen = ref(false);

function formatDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  } catch {
    return iso;
  }
}

function formatDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso.includes("T") ? iso : `${iso.replace(" ", "T")}Z`);
  return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function formatRelative(iso) {
  if (!iso) return "—";
  const then = new Date(iso.includes("T") ? iso : `${iso.replace(" ", "T")}Z`);
  const diffMin = Math.floor((Date.now() - then.getTime()) / 60_000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHours = Math.floor(diffMin / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  return `${Math.floor(diffHours / 24)}d ago`;
}

const job = computed(() => props.cascade?.job ?? null);

const jobTone = computed(() => {
  switch (job.value?.status) {
    case "poisoned": return "error";
    case "pending": return "warning";
    case "in_progress": return "neutral";
    default: return "";
  }
});

const jobHeading = computed(() => {
  switch (job.value?.status) {
    case "poisoned": return "Automatic processing failed";
    case "pending": return "Waiting to retry";
    case "in_progress": return "Processing…";
    default: return "";
  }
});

const isStuckInProgress = computed(() => {
  if (job.value?.status !== "in_progress" || !job.value.last_attempted_at) return false;
  return Date.now() - new Date(`${job.value.last_attempted_at.replace(" ", "T")}Z`).getTime() > STUCK_THRESHOLD_MS;
});

const showResolveButton = computed(() => {
  if (!job.value) return false;
  if (job.value.status === "in_progress") return isStuckInProgress.value;
  return true;
});

function openResolve() {
  categorySheetOpen.value = true;
}

async function onCategorySelect(categoryId) {
  try {
    await reviewStore.resolveStuckReceipt(props.cascade.id, { categoryId });
    emit("resolved");
  } catch (err) {
    if (err?.status === 409) {
      toast.show("Receipt was processed automatically", "info");
      emit("resolved");
    } else {
      toast.show(err?.message || "Resolve failed", "error");
    }
  }
}
</script>

<template>
  <div class="cascade-card" data-testid="cascade-card">
    <div v-if="loading" class="cascade-loading">Loading…</div>
    <template v-else-if="cascade">
      <div v-if="job" class="job-banner" :class="`job-banner--${jobTone}`" data-testid="job-banner">
        <div class="job-banner-heading">
          <span v-if="job.status === 'in_progress'" class="job-spinner" aria-hidden="true" />
          {{ jobHeading }}
        </div>
        <div class="job-banner-body">
          <template v-if="job.status === 'poisoned'">{{ job.last_error }}</template>
          <template v-else-if="job.status === 'pending'">Tried {{ job.retry_count }} times. Next retry: {{ formatDateTime(job.retry_after) }}</template>
          <template v-else-if="job.status === 'in_progress'">Tried {{ job.retry_count }} times, currently running.</template>
        </div>
        <div v-if="job.status === 'poisoned'" class="job-banner-footer">
          Tried {{ job.retry_count }} times · Last attempt: {{ formatRelative(job.last_attempted_at) }}
        </div>
        <div v-if="job.status === 'in_progress' && isStuckInProgress" class="job-banner-warning">
          Processing appears stuck — you can create an expense manually. If the automatic
          processing finishes first, this action will return an error.
        </div>
        <button
          v-if="showResolveButton"
          type="button"
          class="job-banner-action"
          data-testid="job-resolve-btn"
          @click="openResolve"
        >
          Create expense manually
        </button>
      </div>

      <div class="cascade-header">
        <span class="cascade-merchant">{{ cascade.merchant || "Receipt" }}</span>
        <span class="cascade-date">{{ formatDate(cascade.captured_at) }}</span>
      </div>
      <div v-for="item in cascade.expenses" :key="item.id" class="cascade-row">
        <span class="cascade-item-name">{{ item.item_name || "—" }}</span>
        <span class="cascade-item-amount">{{ item.amount }} {{ item.currency }}</span>
      </div>
      <div class="cascade-footer">
        <span class="cascade-total-label">TOTAL</span>
        <span class="cascade-total-amount">
          {{ cascade.total.amount.toFixed(2) }} {{ cascade.total.currency }}
        </span>
      </div>
    </template>
  </div>

  <CategorySheet
    :open="categorySheetOpen"
    title="Select category"
    @select="onCategorySelect"
    @close="categorySheetOpen = false"
  />
</template>

<style scoped>
.cascade-card {
  background: var(--field-deep);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  font-size: 0.85rem;
}

.cascade-loading {
  padding: 1rem;
  color: var(--muted);
  font-size: 0.85rem;
  text-align: center;
}

.cascade-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 0.6rem 0.75rem;
  border-bottom: 1px solid var(--border);
}

.cascade-merchant {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--text);
}

.cascade-date {
  font-family: var(--font-num);
  font-size: 0.78rem;
  color: var(--muted);
}

.cascade-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 0.35rem 0.75rem;
  gap: 0.5rem;
}

.cascade-item-name {
  flex: 1;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cascade-item-amount {
  font-family: var(--font-num);
  font-size: 0.82rem;
  color: var(--muted);
  flex-shrink: 0;
}

.cascade-footer {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 0.5rem 0.75rem;
  border-top: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.02);
}

.cascade-total-label {
  font-size: 0.7rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
}

.cascade-total-amount {
  font-family: var(--font-num);
  font-weight: 700;
  color: var(--text);
}

.job-banner {
  padding: 0.6rem 0.75rem;
  border-bottom: 1px solid var(--border);
  font-size: 0.82rem;
}

.job-banner--error {
  background: rgba(239, 68, 68, 0.08);
  border-bottom-color: rgba(239, 68, 68, 0.25);
}

.job-banner--warning {
  background: rgba(245, 158, 11, 0.08);
  border-bottom-color: rgba(245, 158, 11, 0.25);
}

.job-banner--neutral {
  background: rgba(148, 163, 184, 0.08);
}

.job-banner-heading {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-weight: 700;
  margin-bottom: 0.25rem;
}

.job-banner--error .job-banner-heading {
  color: #fca5a5;
}

.job-banner--warning .job-banner-heading {
  color: #fbbf24;
}

.job-banner-body {
  color: var(--text);
  white-space: pre-wrap;
  overflow-wrap: break-word;
}

.job-banner-footer {
  margin-top: 0.35rem;
  color: var(--muted);
  font-size: 0.75rem;
}

.job-banner-warning {
  margin-top: 0.35rem;
  color: #fbbf24;
  font-size: 0.78rem;
}

.job-banner-action {
  margin-top: 0.5rem;
  padding: 0.35rem 0.75rem;
  background: rgba(96, 165, 250, 0.15);
  border: 1px solid rgba(96, 165, 250, 0.3);
  border-radius: 999px;
  color: #60a5fa;
  font-size: 0.78rem;
  font-weight: 600;
  cursor: pointer;
  width: auto;
}

.job-banner-action:hover {
  background: rgba(96, 165, 250, 0.25);
}

.job-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid rgba(148, 163, 184, 0.3);
  border-top-color: var(--muted);
  border-radius: 50%;
  animation: job-spin 0.8s linear infinite;
}

@keyframes job-spin {
  to { transform: rotate(360deg); }
}
</style>

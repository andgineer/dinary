<script setup>
import { computed, onMounted, onBeforeUnmount, ref, watch } from "vue";
import QueueModal from "./components/QueueModal.vue";
import HeaderSegmented from "./components/HeaderSegmented.vue";
import AddView from "./views/AddView.vue";
import IncomeView from "./views/IncomeView.vue";
import ReviewView from "./views/ReviewView.vue";
import LLMView from "./views/LLMView.vue";
import { useQueueStore } from "./stores/queue.js";
import { useReceiptQueueStore } from "./stores/receiptQueue.js";
import { useToastStore } from "./stores/toast.js";
import { useReviewStore } from "./stores/review.js";
import { flushQueue } from "./composables/flushQueue.js";
import { flushReceiptQueue } from "./composables/flushReceiptQueue.js";
import { useOnline } from "./composables/useOnline.js";

const APP_VERSION =
  typeof __APP_VERSION__ !== "undefined" ? __APP_VERSION__ : "dev";

const isDev = import.meta.env.VITE_DEV_MODE === "true";

const queue = useQueueStore();
const receiptQueue = useReceiptQueueStore();
const toast = useToastStore();
const reviewStore = useReviewStore();

const { isOnline } = useOnline();
const tab = ref("add"); // 'add' | 'income' | 'review' | 'llm'

const offlineMessage = computed(() => {
  if (tab.value === "add") return "Offline — expenses will be queued";
  if (tab.value === "income") return "Offline — incomes can't be added or edited";
  return "Offline — changes not available";
});
const queueModalOpen = ref(false);

const queueCount = computed(() => queue.items.length + receiptQueue.items.length);
const headerVersionLabel = computed(() => `v${APP_VERSION}`);
const showReviewBadge = computed(() => {
  const q = reviewStore.receiptsQueue;
  return reviewStore.dirtyFlag
    || reviewStore.doubtfulCount > 0
    || q.pending > 0
    || q.in_progress > 0
    || q.sleeping > 0
    || q.poisoned > 0;
});

watch(isOnline, (online) => {
  if (online) {
    void flushQueue();
    void flushReceiptQueue();
    if (reviewStore.dirtyFlag) void reviewStore.loadIfNeeded();
  }
});

async function init() {
  await queue.refresh();
  await receiptQueue.refresh();
  if (isOnline.value) {
    if (queue.items.length > 0) void flushQueue();
    if (receiptQueue.items.length > 0) void flushReceiptQueue();
    if (reviewStore.dirtyFlag) void reviewStore.loadIfNeeded();
  }
}

function handleVisibilityChange() {
  if (document.visibilityState !== "visible" || !navigator.onLine) return;
  if (!isOnline.value) window.dispatchEvent(new Event("online"));
  if (reviewStore.dirtyFlag) void reviewStore.loadIfNeeded();
}

let _retryTimerId = null;
function startRetryTimer() {
  _retryTimerId = setInterval(() => {
    if (isOnline.value) {
      if (queue.items.length > 0) void flushQueue();
      if (receiptQueue.items.length > 0) void flushReceiptQueue();
    }
  }, 30_000);
}

function stopRetryTimer() {
  if (_retryTimerId) {
    clearInterval(_retryTimerId);
    _retryTimerId = null;
  }
}

onMounted(() => {
  void init();
  startRetryTimer();
  document.addEventListener("visibilitychange", handleVisibilityChange);
});

onBeforeUnmount(() => {
  stopRetryTimer();
  document.removeEventListener("visibilitychange", handleVisibilityChange);
});
</script>

<template>
  <div v-if="isDev" class="dev-banner">DEV MODE</div>
  <header class="app-header" :class="{ 'below-banner': isDev }">
    <div class="header-row">
      <div class="header-left">
        <h1>
          Dinary
          <span class="header-version">{{ headerVersionLabel }}</span>
        </h1>
        <button
          v-if="queueCount > 0"
          type="button"
          class="queue-badge"
          :aria-label="`${queueCount} queued entries`"
          data-testid="queue-badge"
          @click="queueModalOpen = true"
        >
          {{ queueCount }} queued
        </button>
      </div>
      <HeaderSegmented
        v-model:tab="tab"
        :show-badge="showReviewBadge"
      />
    </div>
    <div v-if="!isOnline" class="offline-notice" role="status">{{ offlineMessage }}</div>
  </header>

  <main class="app-main">
    <AddView v-if="tab === 'add'" />
    <IncomeView v-else-if="tab === 'income'" />
    <ReviewView v-else-if="tab === 'review'" />
    <LLMView v-else-if="tab === 'llm'" />
  </main>

  <QueueModal :open="queueModalOpen" @close="queueModalOpen = false" />

  <div
    class="toast"
    :class="{
      show: toast.visible,
      success: toast.type === 'success',
      error: toast.type === 'error',
      info: toast.type === 'info',
    }"
    role="status"
    aria-live="polite"
    @click="toast.hide()"
  >
    <span class="toast-icon" aria-hidden="true">{{
      toast.type === 'success' ? '✓' : toast.type === 'error' ? '✕' : 'ℹ'
    }}</span>
    {{ toast.message }}
  </div>
</template>

<style scoped>
.dev-banner {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 20;
  background: #f59e0b;
  color: #000;
  text-align: center;
  font-size: 1rem;
  font-weight: 700;
  padding: 6px 0;
  line-height: 1.4;
}

.app-header.below-banner {
  top: 36px;
}

.app-header {
  background: var(--surface);
  display: flex;
  flex-direction: column;
  border-bottom: 1px solid var(--surface-2);
  position: sticky;
  top: 0;
  z-index: 10;
}

.header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 1rem 1.25rem;
}

.app-header h1 {
  font-size: 1.25rem;
  font-weight: 600;
}

.header-version {
  font-size: 0.7rem;
  font-weight: 400;
  color: var(--text-muted);
  margin-left: 0.35rem;
  cursor: pointer;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
}

.queue-badge {
  background: var(--warning);
  color: #000;
  border: none;
  border-radius: 999px;
  padding: 0.2rem 0.6rem;
  font-size: 0.75rem;
  font-weight: 700;
  cursor: pointer;
  width: auto;
}

.offline-notice {
  text-align: center;
  font-size: 0.8rem;
  color: var(--warning);
  padding: 0.3rem 1.25rem 0.4rem;
  border-top: 1px solid var(--warning);
  background: color-mix(in srgb, var(--warning) 10%, transparent);
}

.app-main {
  flex: 1;
  padding: 1.25rem;
  padding-bottom: calc(5rem + env(safe-area-inset-bottom, 0px));
  max-width: 480px;
  width: 100%;
  margin: 0 auto;
}

@media (min-width: 600px) {
  .app-main {
    padding: 2rem;
    padding-bottom: calc(5rem + env(safe-area-inset-bottom, 0px));
  }
}
</style>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from "vue";
import { QrCode, X, Save } from "lucide-vue-next";
import ExpenseForm from "./components/ExpenseForm.vue";
import QrScanner from "./components/QrScanner.vue";
import QueueModal from "./components/QueueModal.vue";
import AddGroupModal from "./modals/AddGroupModal.vue";
import AddCategoryModal from "./modals/AddCategoryModal.vue";
import AddEventModal from "./modals/AddEventModal.vue";
import AddTagModal from "./modals/AddTagModal.vue";
import { useCatalogStore } from "./stores/catalog.js";
import { useQueueStore } from "./stores/queue.js";
import { useReceiptQueueStore } from "./stores/receiptQueue.js";
import { useToastStore } from "./stores/toast.js";
import { isFiscalReceiptUrl, parseReceiptUrl } from "./composables/receipt.js";
import { flushQueue } from "./composables/flushQueue.js";
import { flushReceiptQueue } from "./composables/flushReceiptQueue.js";

const APP_VERSION =
  typeof __APP_VERSION__ !== "undefined" ? __APP_VERSION__ : "dev";

const isDev = import.meta.env.VITE_DEV_MODE === "true";

const catalog = useCatalogStore();
const queue = useQueueStore();
const receiptQueue = useReceiptQueueStore();
const toast = useToastStore();

const isOnline = ref(
  typeof navigator !== "undefined" ? navigator.onLine : true,
);
const queueModalOpen = ref(false);
const scanner = ref(null);
const scannerActive = ref(false);
const expenseForm = ref(null);

const openAddModal = ref(null); // 'group' | 'category' | 'event' | 'tag' | null
const addCategoryGroupId = ref(null);

const queueCount = computed(() => queue.items.length);
const headerVersionLabel = computed(() => `v${APP_VERSION}`);

function onOnline() {
  isOnline.value = true;
  void flushQueue();
  void flushReceiptQueue();
}

function onOffline() {
  isOnline.value = false;
}

async function init() {
  await queue.refresh();
  await receiptQueue.refresh();
  if (isOnline.value) {
    if (queue.items.length > 0) void flushQueue();
    if (receiptQueue.items.length > 0) void flushReceiptQueue();
  }
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

function onRequestAdd(detail) {
  if (detail.kind === "category") {
    if (!detail.groupId) {
      toast.show("Select a group first", "error");
      return;
    }
    addCategoryGroupId.value = Number(detail.groupId);
  }
  openAddModal.value = detail.kind;
}

function closeAddModal() {
  openAddModal.value = null;
  addCategoryGroupId.value = null;
}

function saveExpense() {
  expenseForm.value?.save?.();
}

function toggleScanner() {
  if (!scanner.value) return;
  if (scannerActive.value) {
    scanner.value.stop();
    scannerActive.value = false;
    return;
  }
  scannerActive.value = true;
  scanner.value
    .start()
    .catch((err) => {
      toast.show(err.message || "Camera failed", "error");
      scannerActive.value = false;
    });
}

function onScan(text) {
  scannerActive.value = false;
  if (!isFiscalReceiptUrl(text)) {
    const preview = text.length > 80 ? text.slice(0, 80) + "…" : text;
    toast.show(`Not a fiscal QR: ${preview}`, "error");
    return;
  }
  // Queue the raw URL for server-side classification regardless of
  // whether the client-side parse (amount/date autofill) succeeds.
  receiptQueue.enqueue(text);
  if (isOnline.value) void flushReceiptQueue();

  try {
    const parsed = parseReceiptUrl(text);
    toast.show(`Receipt: ${parsed.amount} RSD, ${parsed.date}`, "success");
    window.dispatchEvent(
      new CustomEvent("dinary:receipt-parsed", { detail: parsed }),
    );
  } catch {
    toast.show("Could not read receipt", "error");
  }
}

function onScanError(err) {
  toast.show(err?.message || "Camera failed", "error");
  scannerActive.value = false;
}

function openQueue() {
  queueModalOpen.value = true;
}

function closeQueue() {
  queueModalOpen.value = false;
}

onMounted(() => {
  window.addEventListener("online", onOnline);
  window.addEventListener("offline", onOffline);
  void init();
  startRetryTimer();
});

onBeforeUnmount(() => {
  window.removeEventListener("online", onOnline);
  window.removeEventListener("offline", onOffline);
  stopRetryTimer();
});
</script>

<template>
  <div v-if="isDev" class="dev-banner">DEV MODE</div>
  <header class="app-header" :class="{ 'below-banner': isDev }">
    <h1>
      Dinary
      <span class="header-version">{{ headerVersionLabel }}</span>
    </h1>
    <div class="header-right">
      <span v-if="!isOnline" class="offline-hint">Offline</span>
      <button
        v-if="queueCount > 0"
        type="button"
        class="queue-badge"
        :aria-label="`${queueCount} queued entries`"
        data-testid="queue-badge"
        @click="openQueue"
      >
        {{ queueCount }} queued
      </button>
    </div>
  </header>

  <main class="app-main">
    <QrScanner
      ref="scanner"
      @scan="onScan"
      @error="onScanError"
    />

    <ExpenseForm ref="expenseForm" @request-add="onRequestAdd" />
  </main>

  <footer class="action-bar">
    <div class="action-bar-inner">
      <button
        type="button"
        class="btn btn-primary action-qr"
        :class="{ 'is-scanning': scannerActive }"
        :aria-label="scannerActive ? 'Stop scanning' : 'Scan QR'"
        data-testid="qr-btn"
        @click="toggleScanner"
      >
        <QrCode v-if="!scannerActive" :size="20" aria-hidden="true" />
        <X v-else :size="20" aria-hidden="true" />
      </button>
      <button
        type="button"
        class="btn btn-secondary action-save"
        data-testid="save-btn"
        @click="saveExpense"
      >
        <Save :size="20" aria-hidden="true" />
        Save
      </button>
    </div>
  </footer>

  <QueueModal :open="queueModalOpen" @close="closeQueue" />

  <AddGroupModal :open="openAddModal === 'group'" @close="closeAddModal" />
  <AddCategoryModal
    :open="openAddModal === 'category'"
    :group-id="addCategoryGroupId"
    @close="closeAddModal"
  />
  <AddEventModal :open="openAddModal === 'event'" @close="closeAddModal" />
  <AddTagModal :open="openAddModal === 'tag'" @close="closeAddModal" />

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
  >
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
  padding: 1rem 1.25rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  border-bottom: 1px solid var(--surface-2);
  position: sticky;
  top: 0;
  z-index: 10;
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

.header-right {
  display: flex;
  align-items: center;
  gap: 0.5rem;
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

.form-placeholder .muted {
  color: var(--text-muted);
  font-size: 0.85rem;
  margin-top: 0.5rem;
}

.action-bar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  background: var(--surface);
  border-top: 1px solid var(--border, rgba(255, 255, 255, 0.08));
  padding: 8px 12px calc(12px + env(safe-area-inset-bottom, 0px));
  z-index: 15;
  box-shadow: 0 -4px 16px rgba(0, 0, 0, 0.25);
}

.action-bar-inner {
  display: flex;
  align-items: stretch;
  gap: 8px;
  max-width: 480px;
  margin: 0 auto;
}

.action-bar .btn {
  margin: 0;
  width: auto;
}

.action-qr {
  flex: 0 0 48px;
  width: 48px;
  height: 48px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--danger, #e94560);
  border-radius: 12px;
  border: none;
  color: #fff;
  cursor: pointer;
  padding: 0;
}

.action-qr:hover {
  filter: brightness(1.1);
}

.action-qr.is-scanning {
  background: var(--surface-2);
}

.action-save {
  flex: 1;
  height: 48px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  background: var(--accent);
  border-radius: 12px;
  border: none;
  color: #fff;
  font-size: 1rem;
  font-weight: 600;
  cursor: pointer;
  padding: 0 1rem;
}
</style>

<script setup>
import { computed, ref, watch } from "vue";
import { Copy, RefreshCw, X } from "lucide-vue-next";
import { apiRequest } from "../api/_request.js";
import { useQueueStore } from "../stores/queue.js";
import { useReceiptQueueStore } from "../stores/receiptQueue.js";
import { useToastStore } from "../stores/toast.js";
import { parseReceiptUrl } from "../composables/receipt.js";

const props = defineProps({
  open: { type: Boolean, default: false },
});
const emit = defineEmits(["close"]);

const queue = useQueueStore();
const receiptQueue = useReceiptQueueStore();
const toast = useToastStore();

const APP_VERSION =
  typeof __APP_VERSION__ !== "undefined" ? __APP_VERSION__ : "dev";
const serverVersion = ref(null);
const versionCheckFailed = ref(false);

async function refreshServerVersion() {
  serverVersion.value = null;
  versionCheckFailed.value = false;
  if (!navigator.onLine) return;
  try {
    const body = await apiRequest("/api/version");
    if (body && typeof body.version === "string") {
      serverVersion.value = body.version;
    }
  } catch {
    versionCheckFailed.value = true;
  }
}

watch(
  () => props.open,
  async (isOpen) => {
    if (!isOpen) return;
    await queue.refresh();
    await receiptQueue.refresh();
    await refreshServerVersion();
  },
  { immediate: true },
);

const updateAvailable = computed(
  () =>
    serverVersion.value &&
    APP_VERSION !== "dev" &&
    serverVersion.value !== APP_VERSION,
);

function formatItem(it) {
  const parts = [`${it.amount} RSD`, it.category_name || `cat#${it.category_id}`];
  if (it.comment) parts.push(it.comment);
  parts.push(it.date);
  return parts.join(" | ");
}

async function copyToClipboard() {
  const text = queue.items.map(formatItem).join("\n");
  try {
    await navigator.clipboard.writeText(text);
    toast.show("Copied to clipboard", "success");
  } catch {
    toast.show("Copy failed", "error");
  }
}

function parseReceiptDisplay(url) {
  try {
    return parseReceiptUrl(url);
  } catch {
    return null;
  }
}

function reloadApp() {
  // Updates land via the Workbox SW (registerType 'autoUpdate' +
  // skipWaiting + clientsClaim — see vite.config.js Step 4 audit), but
  // operators may want to force the swap right now from this modal.
  window.location.reload();
}

function close() {
  emit("close");
}
</script>

<template>
  <div v-if="open" class="modal" role="dialog" aria-modal="true" @click.self="close">
    <div class="modal-content">
      <div class="modal-header">
        <h2>Queued expenses</h2>
        <button
          type="button"
          class="modal-close"
          aria-label="Close"
          @click="close"
        >
          <X :size="18" aria-hidden="true" />
        </button>
      </div>

      <div v-if="queue.lastFlushError" class="queue-error">
        {{ queue.lastFlushError.message ?? String(queue.lastFlushError) }}
      </div>
      <div v-if="receiptQueue.lastFlushError" class="queue-error">
        {{ receiptQueue.lastFlushError.message ?? String(receiptQueue.lastFlushError) }}
      </div>

      <div v-if="queue.items.length === 0 && receiptQueue.items.length === 0" class="queue-empty">
        Queue is empty
      </div>
      <div v-else>
        <div
          v-for="it in queue.items"
          :key="`exp-${it.id}`"
          class="queue-item"
          data-testid="queue-item"
        >
          <span class="qi-amount">{{ it.amount }} RSD</span>
          —
          {{ it.category_name || `cat#${it.category_id}` }}
          <div v-if="it.comment" class="qi-comment">{{ it.comment }}</div>
          <div class="qi-meta">{{ it.date }}</div>
        </div>
        <div
          v-for="it in receiptQueue.items"
          :key="`rec-${it.id}`"
          class="queue-item"
          data-testid="queue-item"
        >
          <template v-for="parsed in [parseReceiptDisplay(it.url)]" :key="it.id">
            <span class="qi-receipt-label">QR receipt</span>
            <template v-if="parsed">
              — <span class="qi-amount">{{ parsed.amount.toFixed(2) }} RSD</span>
              <div class="qi-meta">{{ parsed.date }}</div>
            </template>
          </template>
        </div>
        <button
          v-if="queue.items.length > 0"
          type="button"
          class="btn btn-secondary"
          @click="copyToClipboard"
        >
          <Copy :size="16" aria-hidden="true" />
          Copy to clipboard
        </button>
      </div>

      <div class="version-info">
        <span>v{{ APP_VERSION }}</span>
        <span v-if="updateAvailable" class="update-available">
          · update available ({{ serverVersion }})
          <button
            type="button"
            class="btn-inline reload-btn"
            @click="reloadApp"
          >
            <RefreshCw :size="13" aria-hidden="true" />
            Reload
          </button>
        </span>
        <span v-else-if="versionCheckFailed" class="version-check-failed">
          · server version check failed
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.queue-error {
  background: var(--error);
  color: #fff;
  padding: 0.5rem 0.75rem;
  border-radius: 8px;
  font-size: 0.85rem;
  margin-bottom: 0.75rem;
}

.queue-empty {
  color: var(--text-muted);
  text-align: center;
  margin: 0.5rem 0;
}

.queue-item {
  background: var(--bg);
  border-radius: 8px;
  padding: 0.75rem;
  margin-bottom: 0.5rem;
  font-size: 0.9rem;
  line-height: 1.4;
}

.qi-amount {
  font-weight: 700;
  color: var(--accent);
}

.qi-comment {
  font-size: 0.85rem;
}

.qi-meta {
  color: var(--text-muted);
  font-size: 0.8rem;
}

.qi-receipt-label {
  font-weight: 700;
  color: var(--accent);
}

.version-info {
  margin-top: 1rem;
  font-size: 0.75rem;
  color: var(--text-muted);
  text-align: center;
}

.update-available {
  color: var(--warning);
}

.version-check-failed {
  color: var(--text-muted);
  font-style: italic;
}

.reload-btn {
  margin-left: 0.4rem;
}
</style>

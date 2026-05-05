<script setup>
import { computed, ref, watch } from "vue";
import { useQueueStore } from "../stores/queue.js";
import { useToastStore } from "../stores/toast.js";

const props = defineProps({
  open: { type: Boolean, default: false },
});
const emit = defineEmits(["close"]);

const queue = useQueueStore();
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
    const resp = await fetch("/api/version");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const body = await resp.json();
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
          ×
        </button>
      </div>

      <div v-if="queue.lastFlushError" class="queue-error">
        {{ queue.lastFlushError.message ?? String(queue.lastFlushError) }}
      </div>

      <div v-if="queue.items.length === 0" class="queue-empty">
        No queued expenses
      </div>
      <div v-else>
        <div
          v-for="it in queue.items"
          :key="it.id"
          class="queue-item"
          data-testid="queue-item"
        >
          <span class="qi-amount">{{ it.amount }} RSD</span>
          —
          {{ it.category_name || `cat#${it.category_id}` }}
          <div v-if="it.comment" class="qi-comment">{{ it.comment }}</div>
          <div class="qi-meta">{{ it.date }}</div>
        </div>
        <button
          type="button"
          class="btn btn-secondary"
          @click="copyToClipboard"
        >
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

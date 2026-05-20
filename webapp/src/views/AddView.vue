<script setup>
import { ref } from "vue";
import { QrCode, X, Save } from "lucide-vue-next";
import ExpenseForm from "../components/ExpenseForm.vue";
import QrScanner from "../components/QrScanner.vue";
import KeyboardSaveBar from "../components/KeyboardSaveBar.vue";
import { useReceiptQueueStore } from "../stores/receiptQueue.js";
import { useToastStore } from "../stores/toast.js";
import { isFiscalReceiptUrl } from "../composables/receipt.js";
import { flushReceiptQueue } from "../composables/flushReceiptQueue.js";
import { useKeyboardVisible } from "../composables/useKeyboardVisible.js";

const receiptQueue = useReceiptQueueStore();
const toast = useToastStore();
const { keyboardVisible, keyboardBottom } = useKeyboardVisible();

const scanner = ref(null);
const scannerActive = ref(false);
const expenseForm = ref(null);

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
  scanner.value.start().catch((err) => {
    toast.show(err.message || "Camera failed", "error");
    scannerActive.value = false;
  });
}

async function onScan(text) {
  scannerActive.value = false;
  if (!isFiscalReceiptUrl(text)) {
    const preview = text.length > 80 ? text.slice(0, 80) + "…" : text;
    toast.show(`Not a fiscal QR: ${preview}`, "error");
    return;
  }
  const status = await receiptQueue.enqueue(text);
  if (status === "in-queue") {
    toast.show("Already queued", "info");
    void flushReceiptQueue();
    return;
  }
  if (!navigator.onLine) {
    toast.show("Receipt queued", "info");
    return;
  }
  void flushReceiptQueue();
}

function onScanError(err) {
  toast.show(err?.message || "Camera failed", "error");
  scannerActive.value = false;
}
</script>

<template>
  <QrScanner ref="scanner" @scan="onScan" @error="onScanError" />
  <KeyboardSaveBar v-if="keyboardVisible" :bottom="keyboardBottom" @save="saveExpense" />

  <ExpenseForm ref="expenseForm" />

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
</template>

<style scoped>
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

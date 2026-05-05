<script setup>
import { onBeforeUnmount, onMounted, ref, watch, nextTick } from "vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, required: true },
  submitLabel: { type: String, default: "Save" },
  cancelLabel: { type: String, default: "Cancel" },
  submitDisabled: { type: Boolean, default: false },
  errorMessage: { type: String, default: "" },
});

const emit = defineEmits(["close", "submit"]);

const dialogEl = ref(null);

function close() {
  emit("close");
}

function submit() {
  if (props.submitDisabled) return;
  emit("submit");
}

function onKeydown(ev) {
  if (!props.open) return;
  if (ev.key === "Escape") {
    ev.preventDefault();
    close();
  }
  if (ev.key === "Enter" && !(ev.target instanceof HTMLTextAreaElement)) {
    if (!props.submitDisabled) {
      ev.preventDefault();
      submit();
    }
  }
}

watch(
  () => props.open,
  async (isOpen) => {
    if (!isOpen) return;
    await nextTick();
    // Focus the first focusable element so a keyboard-only operator
    // lands inside the dialog instead of the page background.
    const target = dialogEl.value?.querySelector(
      "input, select, textarea, button:not(.modal-close)",
    );
    target?.focus();
  },
  { immediate: false },
);

onMounted(() => {
  window.addEventListener("keydown", onKeydown);
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown);
});
</script>

<template>
  <div
    v-if="open"
    class="modal"
    role="dialog"
    aria-modal="true"
    :aria-label="title"
    @click.self="close"
  >
    <div ref="dialogEl" class="modal-content">
      <div class="modal-header">
        <h2>{{ title }}</h2>
        <button
          type="button"
          class="modal-close"
          aria-label="Close"
          @click="close"
        >
          ×
        </button>
      </div>
      <div class="modal-body">
        <slot />
      </div>
      <div v-if="errorMessage" class="modal-error" role="alert">
        {{ errorMessage }}
      </div>
      <div class="modal-actions">
        <button
          type="button"
          class="btn btn-secondary"
          @click="close"
        >
          {{ cancelLabel }}
        </button>
        <button
          type="button"
          class="btn btn-primary"
          :disabled="submitDisabled"
          @click="submit"
        >
          {{ submitLabel }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-error {
  color: #f87171;
  font-size: 0.85rem;
  margin-top: 0.5rem;
}

.modal-actions {
  display: flex;
  gap: 0.5rem;
  justify-content: flex-end;
  margin-top: 0.75rem;
}

.modal-actions .btn {
  width: auto;
  margin-bottom: 0;
}
</style>

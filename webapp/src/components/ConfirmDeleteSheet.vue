<script setup>
import { AlertTriangle, Trash2 } from "lucide-vue-next";

defineProps({
  open: { type: Boolean, default: false },
  kind: { type: String, default: "expense" }, // 'expense' | 'receipt'
  title: { type: String, required: true },
  destructiveLabel: { type: String, required: true },
  loading: { type: Boolean, default: false },
});
defineEmits(["cancel", "confirm"]);
</script>

<template>
  <Teleport to="body">
    <Transition name="sheet">
      <div v-if="open" class="confirm-sheet" :data-kind="kind" data-testid="confirm-delete-sheet">
        <div class="drag-handle" />
        <div class="confirm-body">
          <div class="confirm-icon">
            <AlertTriangle v-if="kind === 'receipt'" :size="20" />
            <Trash2 v-else :size="18" />
          </div>
          <h3 class="confirm-title">{{ title }}</h3>
          <p class="confirm-text"><slot name="body" /></p>
          <slot name="detail" />
          <div class="confirm-actions">
            <button type="button" class="btn-cancel" data-testid="confirm-cancel" @click="$emit('cancel')">
              Cancel
            </button>
            <button
              type="button"
              class="btn-danger"
              :disabled="loading"
              data-testid="confirm-delete"
              @click="$emit('confirm')"
            >
              <Trash2 :size="14" />
              {{ destructiveLabel }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.sheet-enter-active,
.sheet-leave-active {
  transition: transform 0.25s cubic-bezier(0.32, 0, 0.67, 0);
}
.sheet-enter-from,
.sheet-leave-to {
  transform: translateY(100%);
}

.confirm-sheet {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 50;
  background: var(--surface);
  border-radius: 18px 18px 0 0;
  box-shadow: 0 -4px 32px rgba(0, 0, 0, 0.5);
}

.drag-handle {
  width: 36px;
  height: 4px;
  border-radius: 2px;
  background: var(--border-strong);
  margin: 10px auto 0;
}

.confirm-body {
  padding: 1.25rem 1rem calc(1.25rem + env(safe-area-inset-bottom, 0px));
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.confirm-icon {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: rgba(239, 68, 68, 0.10);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fca5a5;
  flex-shrink: 0;
}

.confirm-title {
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--text);
  margin: 0;
}

.confirm-text {
  font-size: 0.88rem;
  color: var(--muted);
  line-height: 1.45;
  margin: 0;
}

.confirm-actions {
  display: flex;
  gap: 0.75rem;
  margin-top: 0.25rem;
}

.btn-cancel {
  flex: 1;
  padding: 0.6rem 1rem;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 10px;
  color: var(--text);
  font-size: 0.9rem;
  cursor: pointer;
}

.btn-danger {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.35rem;
  padding: 0.6rem 1rem;
  background: var(--danger);
  border: none;
  border-radius: 10px;
  color: #fff;
  font-size: 0.9rem;
  cursor: pointer;
}

.btn-danger:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
</style>

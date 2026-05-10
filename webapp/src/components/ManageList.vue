<script setup>
import { Eye, EyeOff, Pencil, Trash2 } from "lucide-vue-next";

const props = defineProps({
  kind: {
    type: String,
    required: true,
    validator: (v) => ["group", "category", "event", "tag"].includes(v),
  },
  active: {
    type: Array,
    required: true,
  },
  inactive: {
    type: Array,
    required: true,
  },
  label: {
    type: Function,
    default: (item) => item.name ?? "",
  },
  pendingId: {
    type: [Number, String, null],
    default: null,
  },
});

const emit = defineEmits([
  "deactivate",
  "reactivate",
  "delete",
  "edit",
]);

function isPending(item) {
  return props.pendingId !== null && props.pendingId === item.id;
}

function emitAction(action, item) {
  emit(action, item);
}

function onDelete(item) {
  const labelText = props.label(item);
  if (!window.confirm(`Delete "${labelText}" permanently?`)) return;
  emitAction("delete", item);
}

function rowClass(primary) {
  return primary === "deactivate" ? "inactive-row active-row" : "inactive-row";
}
</script>

<template>
  <div class="inactive-list" data-testid="manage-list">
    <!-- Active section -->
    <div class="state-divider state-divider--active" aria-label="Active">
      <Eye :size="12" aria-hidden="true" />
      <div class="state-divider-line" />
    </div>
    <div v-if="active.length === 0" class="inactive-empty">— none —</div>
    <div
      v-for="item in active"
      :key="`active-${item.id}`"
      :class="rowClass('deactivate')"
    >
      <span class="inactive-name">{{ label(item) }}</span>
      <button
        type="button"
        class="btn-inline inactive-edit"
        :disabled="isPending(item)"
        aria-label="Edit"
        @click="emitAction('edit', item)"
      >
        <Pencil :size="13" aria-hidden="true" />
      </button>
      <button
        type="button"
        class="btn-inline inactive-hide"
        :disabled="isPending(item)"
        aria-label="Hide"
        @click="emitAction('deactivate', item)"
      >
        <EyeOff :size="13" aria-hidden="true" />
      </button>
      <button
        v-if="item.removable"
        type="button"
        class="btn-inline inactive-delete"
        :disabled="isPending(item)"
        aria-label="Delete"
        @click="onDelete(item)"
      >
        <Trash2 :size="13" aria-hidden="true" />
      </button>
    </div>

    <!-- Inactive section -->
    <div class="state-divider state-divider--inactive" aria-label="Inactive">
      <EyeOff :size="12" aria-hidden="true" />
      <div class="state-divider-line state-divider-line--dashed" />
    </div>
    <div v-if="inactive.length === 0" class="inactive-empty">— none —</div>
    <div
      v-for="item in inactive"
      :key="`inactive-${item.id}`"
      :class="rowClass('reactivate')"
    >
      <span class="inactive-name">{{ label(item) }}</span>
      <button
        type="button"
        class="btn-inline inactive-activate"
        :disabled="isPending(item)"
        aria-label="Restore"
        @click="emitAction('reactivate', item)"
      >
        <Eye :size="13" aria-hidden="true" />
      </button>
      <button
        v-if="item.removable"
        type="button"
        class="btn-inline inactive-delete"
        :disabled="isPending(item)"
        aria-label="Delete"
        @click="onDelete(item)"
      >
        <Trash2 :size="13" aria-hidden="true" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.inactive-list {
  margin-top: 0.4rem;
  padding: 0.4rem 0.6rem;
  background: var(--field-deep, rgba(0, 0, 0, 0.18));
  border-radius: 8px;
  border: 1px dashed var(--border, rgba(255, 255, 255, 0.08));
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  font-size: 0.8rem;
}

.inactive-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

.inactive-name {
  color: var(--muted, #94a3b8);
  text-decoration: line-through;
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}

.inactive-row.active-row .inactive-name {
  color: var(--text);
  text-decoration: none;
}

.state-divider {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 0.2rem;
  color: var(--muted-2, #64748b);
}

.state-divider:first-child {
  margin-top: 0;
}

.state-divider--active {
  color: var(--accent);
}

.state-divider-line {
  flex: 1;
  height: 1px;
  background: linear-gradient(to right, var(--border-strong, rgba(255,255,255,0.12)), transparent);
}

.state-divider--active .state-divider-line {
  background: linear-gradient(to right, var(--accent), transparent);
  opacity: 0.4;
}

.state-divider-line--dashed {
  background: none;
  border-top: 1px dashed var(--border-strong, rgba(255, 255, 255, 0.12));
}

.inactive-empty {
  color: var(--muted, #94a3b8);
  font-style: italic;
  font-size: 0.75rem;
}

.inactive-hide,
.inactive-activate,
.inactive-edit {
  font-size: 0.7rem;
}

.inactive-delete {
  font-size: 0.7rem;
  color: #fca5a5;
  background: transparent;
  border: 1px solid #7f1d1d;
}

.inactive-delete:hover:not(:disabled) {
  background: #7f1d1d;
  color: #fee2e2;
}
</style>

<script setup>
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
  // Parent passes the id of the row currently being mutated so all
  // buttons disable in lockstep, matching the legacy "for (const s of
  // siblings) s.disabled = true" behaviour.
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
    <div class="inactive-section">Active</div>
    <div v-if="active.length === 0" class="inactive-empty">— no active —</div>
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
        @click="emitAction('edit', item)"
      >
        Edit
      </button>
      <button
        type="button"
        class="btn-inline inactive-hide"
        :disabled="isPending(item)"
        @click="emitAction('deactivate', item)"
      >
        Hide
      </button>
      <button
        v-if="item.removable"
        type="button"
        class="btn-inline inactive-delete"
        :disabled="isPending(item)"
        @click="onDelete(item)"
      >
        Delete
      </button>
    </div>

    <div class="inactive-section">Inactive</div>
    <div v-if="inactive.length === 0" class="inactive-empty">— no inactive —</div>
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
        @click="emitAction('reactivate', item)"
      >
        Restore
      </button>
      <button
        v-if="item.removable"
        type="button"
        class="btn-inline inactive-delete"
        :disabled="isPending(item)"
        @click="onDelete(item)"
      >
        Delete
      </button>
    </div>
  </div>
</template>

<style scoped>
.inactive-list {
  margin-top: 0.4rem;
  padding: 0.4rem 0.6rem;
  background: var(--bg);
  border-radius: 8px;
  border: 1px dashed var(--surface-2);
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
  color: var(--text-muted);
  text-decoration: line-through;
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}

.inactive-row.active-row .inactive-name {
  color: var(--text);
  text-decoration: none;
}

.inactive-section {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-muted);
  margin-top: 0.2rem;
}

.inactive-section:first-child {
  margin-top: 0;
}

.inactive-empty {
  color: var(--text-muted);
  font-style: italic;
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

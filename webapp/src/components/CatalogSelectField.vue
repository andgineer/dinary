<script setup>
import { computed } from "vue";
import ManageList from "./ManageList.vue";

// Repetitive "label + (+ New) + (Manage) + <select> + <ManageList>"
// row used three times in ExpenseForm (group / category / event).
// Tag uses TagPicker and intentionally does not flow through here.
//
// The select stays the source of v-model truth (not ManageList).
// ManageList stays self-contained: it still emits its own
// deactivate/reactivate/delete/edit events; this wrapper only
// forwards them so the parent can keep its existing
// runCatalogAction(kind, item, action) shape.
const props = defineProps({
  // Catalog kind. Forwarded to ManageList; also used as the default
  // for inputId so existing tests that look up #group / #category /
  // #event keep working without an extra prop on each call site.
  kind: {
    type: String,
    required: true,
    validator: (v) => ["group", "category", "event"].includes(v),
  },
  label: { type: String, required: true },
  // Optional explicit DOM id for the <select> + <label for>. Defaults
  // to ``kind`` so id="group" / id="category" / id="event" stay
  // stable.
  inputId: { type: String, default: null },
  modelValue: { type: String, default: "" },
  options: { type: Array, default: () => [] },
  inactive: { type: Array, default: () => [] },
  manageOpen: { type: Boolean, default: false },
  pendingId: { type: [Number, String, null], default: null },
  // Disabled state for the <select> itself (e.g. category disabled
  // until a group is picked). When true the placeholder switches to
  // ``disabledPlaceholder``.
  selectDisabled: { type: Boolean, default: false },
  placeholder: { type: String, default: "— select —" },
  disabledPlaceholder: { type: String, default: "— select —" },
  // Disabled state for the inline "+ New" button. Independent of
  // ``selectDisabled`` because the form may want to allow opening
  // the manage panel even when the select itself is disabled.
  addDisabled: { type: Boolean, default: false },
  addTitle: { type: String, default: "" },
  // Optional helper line shown under the select (e.g. "Select a
  // group first"). Empty string hides the slot entirely.
  formHint: { type: String, default: "" },
  // Optional formatter for ManageList rows. When omitted, ManageList
  // falls back to ``item.name``. The <select> dropdown always shows
  // ``optionLabelFn(item)`` (default: ``item.name``) — Event uses
  // a different formatter for the manage panel ("name (date_from..
  // date_to)") than for the dropdown, so the two formatters are
  // intentionally split.
  optionLabelFn: { type: Function, default: null },
  manageLabelFn: { type: Function, default: null },
});

const emit = defineEmits([
  "update:modelValue",
  "add",
  "manage-toggle",
  "select-change",
  "deactivate",
  "reactivate",
  "delete",
  "edit",
]);

const fallbackLabel = (item) => item?.name ?? "";
const resolvedOptionLabel = computed(() => props.optionLabelFn || fallbackLabel);
const resolvedManageLabel = computed(() => props.manageLabelFn || fallbackLabel);

function onSelect(e) {
  emit("update:modelValue", e.target.value);
  emit("select-change", e);
}
</script>

<template>
  <div class="form-group">
    <label :for="inputId || kind">
      {{ label }}
      <button
        type="button"
        class="btn-inline"
        :disabled="addDisabled"
        :title="addTitle"
        @click="emit('add')"
      >
        + New
      </button>
      <button
        type="button"
        class="btn-inline"
        @click="emit('manage-toggle')"
      >
        {{ manageOpen ? "Close" : "Manage" }}
      </button>
    </label>
    <select
      :id="inputId || kind"
      :value="modelValue"
      :disabled="selectDisabled"
      @change="onSelect"
    >
      <option value="">
        {{ selectDisabled ? disabledPlaceholder : placeholder }}
      </option>
      <option
        v-for="opt in options"
        :key="opt.id"
        :value="String(opt.id)"
      >
        {{ resolvedOptionLabel(opt) }}
      </option>
    </select>
    <div v-if="formHint" class="form-hint">{{ formHint }}</div>
    <ManageList
      v-if="manageOpen"
      :kind="kind"
      :active="options"
      :inactive="inactive"
      :label="resolvedManageLabel"
      :pending-id="pendingId"
      @deactivate="emit('deactivate', $event)"
      @reactivate="emit('reactivate', $event)"
      @delete="emit('delete', $event)"
      @edit="emit('edit', $event)"
    />
  </div>
</template>

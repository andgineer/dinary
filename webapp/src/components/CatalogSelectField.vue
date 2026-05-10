<script setup>
import { computed, ref, watch } from "vue";
import { ChevronDown, Check } from "lucide-vue-next";
import ManageList from "./ManageList.vue";
import IconBtn from "./IconBtn.vue";

const props = defineProps({
  kind: {
    type: String,
    required: true,
    validator: (v) => ["group", "category", "event"].includes(v),
  },
  label: { type: String, required: true },
  inputId: { type: String, default: null },
  modelValue: { type: String, default: "" },
  options: { type: Array, default: () => [] },
  inactive: { type: Array, default: () => [] },
  manageOpen: { type: Boolean, default: false },
  pendingId: { type: [Number, String, null], default: null },
  selectDisabled: { type: Boolean, default: false },
  placeholder: { type: String, default: "— select —" },
  disabledPlaceholder: { type: String, default: "— select —" },
  addDisabled: { type: Boolean, default: false },
  addTitle: { type: String, default: "" },
  formHint: { type: String, default: "" },
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

const pickerOpen = ref(false);

const fallbackLabel = (item) => item?.name ?? "";
const resolvedOptionLabel = computed(() => props.optionLabelFn || fallbackLabel);
const resolvedManageLabel = computed(() => props.manageLabelFn || fallbackLabel);

const currentLabel = computed(() => {
  if (!props.modelValue) {
    return props.selectDisabled ? props.disabledPlaceholder : props.placeholder;
  }
  const found = props.options.find((o) => String(o.id) === props.modelValue);
  return found ? resolvedOptionLabel.value(found) : props.placeholder;
});

watch(() => props.manageOpen, (open) => {
  if (open) pickerOpen.value = false;
});

function togglePicker() {
  if (props.selectDisabled) return;
  pickerOpen.value = !pickerOpen.value;
  if (pickerOpen.value && props.manageOpen) {
    emit("manage-toggle");
  }
}

function selectOption(opt) {
  emit("update:modelValue", String(opt.id));
  emit("select-change");
  pickerOpen.value = false;
}
</script>

<template>
  <div class="form-group catalog-field">
    <div class="catalog-row" :class="{ 'picker-active': pickerOpen }">
      <button
        type="button"
        class="catalog-trigger"
        :class="{ 'is-disabled': selectDisabled }"
        :aria-label="label"
        :data-testid="`catalog-trigger-${inputId || kind}`"
        :disabled="selectDisabled"
        @click="togglePicker"
      >
        <span class="catalog-trigger-text">{{ currentLabel }}</span>
        <ChevronDown :size="14" class="catalog-chevron" :class="{ 'is-open': pickerOpen }" aria-hidden="true" />
      </button>

      <div v-if="!pickerOpen" class="catalog-actions">
        <IconBtn
          icon="plus"
          tone="accent"
          label="New"
          :disabled="addDisabled"
          :title="addTitle"
          @click="emit('add')"
        />
        <IconBtn
          icon="cog"
          tone="muted"
          :label="manageOpen ? 'Close' : 'Manage'"
          @click="emit('manage-toggle')"
        />
      </div>
      <div v-else class="catalog-actions">
        <IconBtn icon="x" tone="muted" label="Close picker" @click="pickerOpen = false" />
      </div>
    </div>

    <div v-if="formHint" class="form-hint">{{ formHint }}</div>

    <!-- Picker panel -->
    <div v-if="pickerOpen" class="catalog-picker-panel">
      <div
        v-for="opt in options"
        :key="opt.id"
        class="catalog-picker-option"
        :class="{ 'is-selected': modelValue === String(opt.id) }"
        role="option"
        :aria-selected="modelValue === String(opt.id)"
        @click="selectOption(opt)"
      >
        <Check v-if="modelValue === String(opt.id)" :size="13" class="option-check" aria-hidden="true" />
        <span v-else class="option-check-spacer" />
        {{ resolvedOptionLabel(opt) }}
      </div>
      <div v-if="options.length === 0" class="catalog-picker-empty">— none —</div>
    </div>

    <!-- Manage panel -->
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

<style scoped>
.catalog-field {
  position: relative;
}

.catalog-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.catalog-trigger {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 0.55rem 0.75rem;
  background: var(--field, rgba(255, 255, 255, 0.04));
  border: 1px solid var(--border, rgba(255, 255, 255, 0.08));
  border-radius: 8px;
  color: var(--text);
  font-size: 0.9rem;
  text-align: left;
  cursor: pointer;
  width: auto;
  margin-bottom: 0;
  transition: border-color 0.15s;
}

.catalog-trigger:not(.is-disabled):hover {
  border-color: var(--border-strong, rgba(255, 255, 255, 0.12));
}

.catalog-trigger:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.catalog-trigger.is-disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.catalog-trigger-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
}

.catalog-chevron {
  flex-shrink: 0;
  color: var(--muted);
  transition: transform 0.15s;
}

.catalog-chevron.is-open {
  transform: rotate(180deg);
}

.catalog-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.catalog-picker-panel {
  margin-top: 4px;
  background: var(--field-deep, rgba(0, 0, 0, 0.18));
  border: 1px solid var(--border, rgba(255, 255, 255, 0.08));
  border-radius: 8px;
  overflow: hidden;
}

.catalog-picker-option {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0.5rem 0.75rem;
  cursor: pointer;
  font-size: 0.9rem;
  color: var(--text);
  transition: background 0.1s;
}

.catalog-picker-option:hover {
  background: var(--field, rgba(255, 255, 255, 0.04));
}

.catalog-picker-option.is-selected {
  color: var(--accent);
}

.option-check {
  color: var(--accent);
  flex-shrink: 0;
}

.option-check-spacer {
  display: inline-block;
  width: 13px;
  flex-shrink: 0;
}

.catalog-picker-empty {
  padding: 0.5rem 0.75rem;
  font-size: 0.8rem;
  color: var(--muted);
  font-style: italic;
}
</style>

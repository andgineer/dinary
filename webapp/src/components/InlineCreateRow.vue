<script setup>
import { onMounted, ref } from "vue";
import { Plus, X, Check } from "lucide-vue-next";

const props = defineProps({
  placeholder: { type: String, default: "New name…" },
  validate: { type: Function, default: null },
});
const emit = defineEmits(["save", "cancel"]);

const input = ref(null);
const value = ref("");
const validationError = ref("");

onMounted(() => {
  input.value?.focus();
});

function handleSave() {
  const trimmed = value.value.trim();
  if (!trimmed) {
    emit("cancel");
    return;
  }
  if (props.validate) {
    const err = props.validate(trimmed);
    if (err) {
      validationError.value = err;
      return;
    }
  }
  validationError.value = "";
  emit("save", trimmed);
}

function handleCancel() {
  value.value = "";
  validationError.value = "";
  emit("cancel");
}
</script>

<template>
  <div class="inline-create-row" data-testid="inline-create-row">
    <Plus :size="14" class="row-plus" aria-hidden="true" />
    <input
      ref="input"
      v-model="value"
      type="text"
      :placeholder="placeholder"
      class="row-input"
      autocomplete="off"
      @keydown.enter="handleSave"
      @keydown.esc="handleCancel"
    />
    <button type="button" class="row-btn" aria-label="Cancel" @click="handleCancel">
      <X :size="14" />
    </button>
    <button type="button" class="row-btn row-confirm" aria-label="Confirm" @click="handleSave">
      <Check :size="14" />
    </button>
    <p v-if="validationError" class="inline-error">{{ validationError }}</p>
  </div>
</template>

<style scoped>
.inline-create-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--accent);
  background: color-mix(in oklab, var(--accent) 10%, var(--field-deep));
  box-shadow: 0 0 0 3px color-mix(in oklab, var(--accent) 15%, transparent);
  border-radius: 8px;
  margin-top: 0.4rem;
  margin-bottom: 0.4rem;
  flex-wrap: wrap;
}

.row-plus {
  color: var(--accent);
  flex-shrink: 0;
}

.row-input {
  flex: 1;
  background: transparent;
  border: none;
  color: var(--text);
  font-size: 0.875rem;
  padding: 0;
  min-width: 80px;
}

.row-input:focus {
  outline: none;
  border: none;
}

.row-btn {
  background: none;
  border: none;
  color: var(--muted);
  cursor: pointer;
  padding: 0.2rem;
  width: auto;
  display: flex;
  align-items: center;
  flex-shrink: 0;
  border-radius: 4px;
  transition: color 0.12s;
}

.row-btn:hover {
  color: var(--text);
}

.row-confirm:hover {
  color: var(--success);
}

.inline-error {
  width: 100%;
  font-size: 0.72rem;
  color: var(--error);
  margin-top: 0.2rem;
  padding-left: calc(14px + 0.4rem);
}
</style>

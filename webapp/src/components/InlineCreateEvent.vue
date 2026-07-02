<script setup>
import { computed, onMounted, ref } from "vue";
import { X, Check } from "lucide-vue-next";
import { useCatalogStore } from "../stores/catalog.js";
import TagPicker from "./TagPicker.vue";

const emit = defineEmits(["save", "cancel"]);

const catalog = useCatalogStore();

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

const nameInput = ref(null);
const name = ref("");
const dateFrom = ref(todayIso());
const dateTo = ref(todayIso());
const autoAttachEnabled = ref(false);
const tagIds = ref([]);
const error = ref("");

const allActiveTags = computed(() => catalog.tags);

onMounted(() => {
  nameInput.value?.focus();
});

function validate() {
  if (!name.value.trim()) return "Enter a name";
  if (!dateFrom.value || !dateTo.value) return "Specify both dates";
  if (dateFrom.value > dateTo.value) return "Start date must be ≤ end date";
  return null;
}

function handleSave() {
  const err = validate();
  if (err) {
    error.value = err;
    return;
  }
  const autoTags = tagIds.value.map(Number);
  error.value = "";
  emit("save", {
    name: name.value.trim(),
    date_from: dateFrom.value,
    date_to: dateTo.value,
    auto_attach_enabled: autoAttachEnabled.value,
    auto_tags: autoTags.length > 0 ? autoTags : null,
  });
}

function handleCancel() {
  emit("cancel");
}
</script>

<template>
  <div class="inline-create-event" data-testid="inline-create-event">
    <div class="field-row">
      <input
        ref="nameInput"
        v-model="name"
        type="text"
        placeholder="Event name…"
        class="event-input"
        autocomplete="off"
        @keydown.esc="handleCancel"
      />
    </div>

    <div class="date-grid">
      <div>
        <label for="ice-from">FROM</label>
        <input id="ice-from" v-model="dateFrom" type="date" />
      </div>
      <div>
        <label for="ice-to">TO</label>
        <input id="ice-to" v-model="dateTo" type="date" />
      </div>
    </div>

    <label class="attach-row">
      <input v-model="autoAttachEnabled" type="checkbox" />
      <span>Auto-fill when expense date matches</span>
    </label>

    <div class="autotags-section">
      <div class="autotags-header">
        <span class="autotags-label">AUTO-TAGS</span>
        <span class="autotags-hint">attached when event is selected</span>
      </div>
      <TagPicker
        v-model="tagIds"
        :tags="allActiveTags"
        empty-hint="No tags yet — create one above first."
        accent-color="var(--expense)"
      />
    </div>

    <p v-if="error" class="inline-error">{{ error }}</p>

    <div class="event-footer">
      <button type="button" class="btn-ghost" @click="handleCancel">
        <X :size="14" />
        Cancel
      </button>
      <button type="button" class="btn btn-primary save-btn" @click="handleSave">
        <Check :size="14" />
        Add event
      </button>
    </div>
  </div>
</template>

<style scoped>
.inline-create-event {
  border: 1px solid var(--accent);
  background: color-mix(in oklab, var(--accent) 10%, var(--field-deep));
  box-shadow: 0 0 0 3px color-mix(in oklab, var(--accent) 15%, transparent);
  border-radius: 8px;
  padding: 0.6rem 0.75rem;
  margin-top: 0.4rem;
  margin-bottom: 0.4rem;
}

.field-row {
  margin-bottom: 0.5rem;
}

.event-input {
  width: 100%;
  background: var(--field-deep);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text);
  font-size: 0.875rem;
  padding: 0.4rem 0.6rem;
}

.event-input:focus {
  border-color: var(--accent);
  outline: none;
}

.date-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.date-grid label {
  font-size: 0.6rem;
  letter-spacing: 0.07em;
  margin-bottom: 0.15rem;
}

.date-grid input[type="date"] {
  font-size: 0.8rem;
  padding: 0.3rem 0.5rem;
}

.attach-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.35rem 0.5rem;
  background: var(--field-deep);
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  margin-bottom: 0.5rem;
  text-transform: none;
  letter-spacing: 0;
  color: var(--text);
  font-size: 0.82rem;
  cursor: pointer;
}

.attach-row input[type="checkbox"] {
  width: auto;
}

.autotags-section {
  margin-bottom: 0.5rem;
}

.autotags-header {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  margin-bottom: 0.3rem;
}

.autotags-label {
  font-size: 0.6rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  color: var(--muted);
  text-transform: uppercase;
}

.autotags-hint {
  font-size: 0.68rem;
  color: var(--muted-2);
}

.inline-error {
  font-size: 0.72rem;
  color: var(--error);
  margin-bottom: 0.4rem;
}

.event-footer {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  margin-top: 0.5rem;
}

.btn-ghost {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  background: none;
  border: none;
  color: var(--muted);
  font-size: 0.85rem;
  cursor: pointer;
  padding: 0.35rem 0.6rem;
  width: auto;
  border-radius: 6px;
  transition: background 0.12s;
}

.btn-ghost:hover {
  background: var(--field);
}

.save-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  width: auto;
  font-size: 0.85rem;
  padding: 0.35rem 0.75rem;
}
</style>

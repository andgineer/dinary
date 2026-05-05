<script setup>
import { computed, ref, watch } from "vue";
import BaseModal from "../components/BaseModal.vue";
import TagPicker from "../components/TagPicker.vue";
import { useCatalogStore } from "../stores/catalog.js";
import { useToastStore } from "../stores/toast.js";
import { addResultMessage } from "../composables/addResult.js";

const props = defineProps({
  open: { type: Boolean, default: false },
});
const emit = defineEmits(["close", "added"]);

const catalog = useCatalogStore();
const toast = useToastStore();

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

const name = ref("");
const dateFrom = ref(todayIso());
const dateTo = ref(todayIso());
const autoAttachEnabled = ref(false);
const tagIds = ref([]);
const error = ref("");
const submitting = ref(false);

const allActiveTags = computed(() => catalog.tags);

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return;
    name.value = "";
    dateFrom.value = todayIso();
    dateTo.value = todayIso();
    autoAttachEnabled.value = false;
    tagIds.value = [];
    error.value = "";
    submitting.value = false;
  },
);

async function submit() {
  const trimmed = name.value.trim();
  if (!trimmed) {
    error.value = "Enter a name";
    return;
  }
  if (!dateFrom.value || !dateTo.value) {
    error.value = "Specify both dates";
    return;
  }
  if (dateFrom.value > dateTo.value) {
    error.value = "Start date must be <= end date";
    return;
  }
  // Server expects auto_tags as a list of NAMES (events.auto_tags is a
  // JSON name-array; _require_known_tag_names re-validates server-
  // side). Map ids → names here.
  const selected = new Set(tagIds.value.map(Number));
  const autoTags = allActiveTags.value
    .filter((t) => selected.has(Number(t.id)))
    .map((t) => t.name);
  error.value = "";
  submitting.value = true;
  try {
    const snap = await catalog.add("event", {
      name: trimmed,
      date_from: dateFrom.value,
      date_to: dateTo.value,
      auto_attach_enabled: autoAttachEnabled.value,
      auto_tags: autoTags.length > 0 ? autoTags : null,
    });
    const msg = addResultMessage("event", snap?.status);
    if (msg) toast.show(msg, "info");
    emit("added", { snap, kind: "event" });
    emit("close");
  } catch (err) {
    error.value = err?.message || String(err);
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <BaseModal
    :open="open"
    title="New event"
    submit-label="Add"
    :submit-disabled="submitting"
    :error-message="error"
    @close="emit('close')"
    @submit="submit"
  >
    <label for="add-event-name">Name</label>
    <input
      id="add-event-name"
      v-model="name"
      type="text"
      autocomplete="off"
    />

    <label for="add-event-from">From</label>
    <input id="add-event-from" v-model="dateFrom" type="date" />

    <label for="add-event-to">To</label>
    <input id="add-event-to" v-model="dateTo" type="date" />

    <label class="auto-attach-row">
      <input v-model="autoAttachEnabled" type="checkbox" />
      <span>Auto-fill when expense date matches</span>
    </label>

    <label>Auto-tags</label>
    <TagPicker
      v-model="tagIds"
      :tags="allActiveTags"
      empty-hint="No tags exist yet — create one via the main form's + New tag button, then re-open this modal."
    />
    <div class="form-hint">
      Automatically attached to the expense when the event is selected.
    </div>
  </BaseModal>
</template>

<style scoped>
.auto-attach-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  text-transform: none;
  letter-spacing: 0;
  color: var(--text);
  margin-bottom: 0.5rem;
}

.auto-attach-row input[type="checkbox"] {
  width: auto;
}

.auto-attach-row span {
  font-size: 0.85rem;
}
</style>

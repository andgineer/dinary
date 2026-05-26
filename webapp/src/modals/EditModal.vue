<script setup>
import { computed, ref, watch } from "vue";
import BaseModal from "../components/BaseModal.vue";
import TagPicker from "../components/TagPicker.vue";
import { useCatalogStore } from "../stores/catalog.js";
import { useToastStore } from "../stores/toast.js";
import { validateTagName } from "../composables/addResult.js";

const props = defineProps({
  open: { type: Boolean, default: false },
  // 'group' | 'category' | 'event' | 'tag'
  kind: { type: String, default: null },
  // The catalog item to edit. May be null while modal is closed.
  item: { type: Object, default: null },
});
const emit = defineEmits(["close", "edited"]);

const catalog = useCatalogStore();
const toast = useToastStore();

const name = ref("");
const groupId = ref("");
const sortOrder = ref("");
const sheetName = ref("");
const sheetGroup = ref("");
const dateFrom = ref("");
const dateTo = ref("");
const autoAttachEnabled = ref(false);
const tagIds = ref([]);

const error = ref("");
const submitting = ref(false);

const allActiveTags = computed(() => catalog.tags);
const allActiveGroups = computed(() => catalog.groups);

const titles = {
  group: "Edit group",
  category: "Edit category",
  event: "Edit event",
  tag: "Edit tag",
};
const title = computed(() => titles[props.kind] || "Edit");

function resetFromItem() {
  const item = props.item || {};
  name.value = item.name ?? "";
  groupId.value = item.group_id != null ? String(item.group_id) : "";
  sortOrder.value = item.sort_order != null ? String(item.sort_order) : "";
  sheetName.value = item.sheet_name ?? "";
  sheetGroup.value = item.sheet_group ?? "";
  dateFrom.value = item.date_from ?? "";
  dateTo.value = item.date_to ?? "";
  autoAttachEnabled.value = !!item.auto_attach_enabled;
  tagIds.value = (item.auto_tags ?? []).map(Number);
  error.value = "";
  submitting.value = false;
}

watch(
  () => [props.open, props.item, props.kind],
  ([isOpen]) => {
    if (!isOpen) return;
    resetFromItem();
  },
  { immediate: true },
);

function buildPatchBody() {
  // Send only fields that actually changed. The server PATCH bodies
  // accept all-null/optional fields; sending unchanged values would be
  // wasted work and would risk overwriting concurrent edits.
  const item = props.item || {};
  const trimmed = name.value.trim();
  const body = {};
  if (trimmed && trimmed !== item.name) body.name = trimmed;

  if (props.kind === "group") {
    const so = sortOrder.value === "" ? null : Number(sortOrder.value);
    if (so !== (item.sort_order ?? null)) body.sort_order = so;
  }

  if (props.kind === "category") {
    const newGid = groupId.value === "" ? null : Number(groupId.value);
    if (newGid != null && newGid !== Number(item.group_id)) body.group_id = newGid;
    const sn = sheetName.value === "" ? null : sheetName.value;
    if ((sn ?? null) !== (item.sheet_name ?? null)) body.sheet_name = sn;
    const sg = sheetGroup.value === "" ? null : sheetGroup.value;
    if ((sg ?? null) !== (item.sheet_group ?? null)) body.sheet_group = sg;
  }

  if (props.kind === "event") {
    if (dateFrom.value && dateFrom.value !== item.date_from) body.date_from = dateFrom.value;
    if (dateTo.value && dateTo.value !== item.date_to) body.date_to = dateTo.value;
    if (autoAttachEnabled.value !== !!item.auto_attach_enabled) {
      body.auto_attach_enabled = autoAttachEnabled.value;
    }
    const currentAutoTags = (item.auto_tags ?? []).map(Number);
    const newAutoTags = tagIds.value.map(Number);
    const sameTags =
      currentAutoTags.length === newAutoTags.length &&
      new Set(newAutoTags).size === new Set([...currentAutoTags, ...newAutoTags]).size;
    if (!sameTags) body.auto_tags = newAutoTags;
  }

  return body;
}

function validate() {
  const trimmed = name.value.trim();
  if (!trimmed) {
    error.value = "Enter a name";
    return false;
  }
  if (props.kind === "tag") {
    const tagErr = validateTagName(trimmed);
    if (tagErr) {
      error.value = tagErr;
      return false;
    }
  }
  if (props.kind === "category") {
    if (!groupId.value) {
      error.value = "Select a group";
      return false;
    }
  }
  if (props.kind === "event") {
    if (!dateFrom.value || !dateTo.value) {
      error.value = "Specify both dates";
      return false;
    }
    if (dateFrom.value > dateTo.value) {
      error.value = "Start date must be <= end date";
      return false;
    }
  }
  error.value = "";
  return true;
}

async function submit() {
  if (!validate()) return;
  if (!props.item || props.item.id == null) {
    error.value = "Nothing to edit";
    return;
  }
  const body = buildPatchBody();
  if (Object.keys(body).length === 0) {
    // Nothing changed; close without a network round-trip.
    emit("close");
    return;
  }
  submitting.value = true;
  try {
    const snap = await catalog.patch(props.kind, props.item.id, body);
    toast.show("Saved", "success");
    emit("edited", { snap, kind: props.kind, id: props.item.id });
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
    :title="title"
    submit-label="Save"
    :submit-disabled="submitting"
    :error-message="error"
    @close="emit('close')"
    @submit="submit"
  >
    <label for="edit-name">Name</label>
    <input
      id="edit-name"
      v-model="name"
      type="text"
      autocomplete="off"
    />

    <template v-if="kind === 'group'">
      <label for="edit-sort-order">Sort order</label>
      <input
        id="edit-sort-order"
        v-model="sortOrder"
        type="number"
        inputmode="numeric"
      />
    </template>

    <template v-if="kind === 'category'">
      <label for="edit-group">Group</label>
      <select id="edit-group" v-model="groupId">
        <option value="">— select —</option>
        <option
          v-for="g in allActiveGroups"
          :key="g.id"
          :value="String(g.id)"
        >
          {{ g.name }}
        </option>
      </select>

      <label for="edit-sheet-name">Sheet name</label>
      <input
        id="edit-sheet-name"
        v-model="sheetName"
        type="text"
        autocomplete="off"
      />

      <label for="edit-sheet-group">Sheet group</label>
      <input
        id="edit-sheet-group"
        v-model="sheetGroup"
        type="text"
        autocomplete="off"
      />
    </template>

    <template v-if="kind === 'event'">
      <label for="edit-date-from">From</label>
      <input id="edit-date-from" v-model="dateFrom" type="date" />

      <label for="edit-date-to">To</label>
      <input id="edit-date-to" v-model="dateTo" type="date" />

      <label class="auto-attach-row">
        <input v-model="autoAttachEnabled" type="checkbox" />
        <span>Auto-fill when expense date matches</span>
      </label>

      <label>Auto-tags</label>
      <TagPicker
        v-model="tagIds"
        :tags="allActiveTags"
        empty-hint="No tags exist yet."
      />
    </template>
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

<script setup>
import { ref, watch } from "vue";
import BaseModal from "../components/BaseModal.vue";
import { useCatalogStore } from "../stores/catalog.js";
import { useToastStore } from "../stores/toast.js";
import { addResultMessage } from "../composables/addResult.js";

const props = defineProps({
  open: { type: Boolean, default: false },
  groupId: { type: [Number, null], default: null },
});
const emit = defineEmits(["close", "added"]);

const catalog = useCatalogStore();
const toast = useToastStore();

const name = ref("");
const error = ref("");
const submitting = ref(false);

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      name.value = "";
      error.value = "";
      submitting.value = false;
    }
  },
);

async function submit() {
  const trimmed = name.value.trim();
  if (!trimmed) {
    error.value = "Enter a name";
    return;
  }
  if (!props.groupId) {
    error.value = "Select a group first";
    return;
  }
  error.value = "";
  submitting.value = true;
  try {
    const snap = await catalog.add("category", {
      name: trimmed,
      group_id: Number(props.groupId),
    });
    const msg = addResultMessage("category", snap?.status);
    if (msg) toast.show(msg, "info");
    emit("added", { snap, kind: "category" });
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
    title="New category"
    submit-label="Add"
    :submit-disabled="submitting"
    :error-message="error"
    @close="emit('close')"
    @submit="submit"
  >
    <label for="add-category-name">Name</label>
    <input
      id="add-category-name"
      v-model="name"
      type="text"
      autocomplete="off"
    />
    <div class="form-hint">Group is locked to the one selected in the form.</div>
  </BaseModal>
</template>

<script setup>
import { ref, watch } from "vue";
import BaseModal from "../components/BaseModal.vue";
import { useCatalogStore } from "../stores/catalog.js";
import { useToastStore } from "../stores/toast.js";
import { addResultMessage, validateTagName } from "../composables/addResult.js";

const props = defineProps({
  open: { type: Boolean, default: false },
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
  const validationError = validateTagName(trimmed);
  if (validationError) {
    error.value = validationError;
    return;
  }
  error.value = "";
  submitting.value = true;
  try {
    const snap = await catalog.add("tag", { name: trimmed });
    const msg = addResultMessage("tag", snap?.status);
    if (msg) toast.show(msg, "info");
    emit("added", { snap, kind: "tag" });
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
    title="New tag"
    submit-label="Add"
    :submit-disabled="submitting"
    :error-message="error"
    @close="emit('close')"
    @submit="submit"
  >
    <label for="add-tag-name">Name</label>
    <input
      id="add-tag-name"
      v-model="name"
      type="text"
      autocomplete="off"
    />
  </BaseModal>
</template>

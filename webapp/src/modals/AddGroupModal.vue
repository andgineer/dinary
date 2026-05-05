<script setup>
import { ref, watch } from "vue";
import BaseModal from "../components/BaseModal.vue";
import { useCatalogStore } from "../stores/catalog.js";
import { useToastStore } from "../stores/toast.js";
import { addResultMessage } from "../composables/addResult.js";

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
  if (!trimmed) {
    error.value = "Enter a name";
    return;
  }
  error.value = "";
  submitting.value = true;
  try {
    const snap = await catalog.add("group", { name: trimmed });
    const msg = addResultMessage("group", snap?.status);
    if (msg) toast.show(msg, "info");
    emit("added", { snap, kind: "group" });
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
    title="New group"
    submit-label="Add"
    :submit-disabled="submitting"
    :error-message="error"
    @close="emit('close')"
    @submit="submit"
  >
    <label class="add-modal-label" for="add-group-name">
      Name
    </label>
    <input
      id="add-group-name"
      v-model="name"
      type="text"
      autocomplete="off"
    />
  </BaseModal>
</template>

<style scoped>
.add-modal-label {
  margin-top: 0.25rem;
}
</style>

<script setup>
defineProps({
  modelValue: { type: String, required: true },
  options: { type: Array, required: true },
});
const emit = defineEmits(["update:modelValue"]);
</script>

<template>
  <div class="scope-row">
    <label v-for="opt in options" :key="opt.value" class="scope-option">
      <input
        type="radio"
        :value="opt.value"
        :checked="modelValue === opt.value"
        class="scope-radio"
        @change="emit('update:modelValue', opt.value)"
      />
      {{ opt.label }}
    </label>
  </div>
</template>

<style scoped>
.scope-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.scope-option {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.85rem;
  color: var(--text);
  cursor: pointer;
  text-transform: none;
  letter-spacing: 0;
  font-weight: normal;
}

.scope-radio {
  accent-color: var(--accent);
}
</style>

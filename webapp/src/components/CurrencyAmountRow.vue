<script setup>
import { ref } from "vue";
import CurrencyPicker from "./CurrencyPicker.vue";

defineProps({
  amount: { type: String, default: "" },
  currency: { type: String, default: "" },
});
const emit = defineEmits(["update:amount", "update:currency"]);

const pickerOpen = ref(false);
</script>

<template>
  <div class="amount-row">
    <div class="hero-currency-wrap">
      <button
        type="button"
        class="currency-pill"
        :class="{ 'is-open': pickerOpen }"
        aria-label="Select currency"
        data-testid="currency-pill"
        @click="pickerOpen = !pickerOpen"
      >
        {{ currency || "RSD" }}
      </button>
      <div v-if="pickerOpen" class="currency-picker-wrap">
        <CurrencyPicker
          :model-value="currency"
          accent-color="#60a5fa"
          @update:model-value="emit('update:currency', $event)"
          @close="pickerOpen = false"
        />
      </div>
    </div>
    <input
      :value="amount"
      type="text"
      inputmode="decimal"
      placeholder="0"
      autocomplete="off"
      class="amount-input"
      aria-label="Amount"
      data-testid="amount-input"
      @input="emit('update:amount', $event.target.value)"
    />
  </div>
</template>

<style scoped>
.amount-row {
  display: flex;
  align-items: center;
  gap: 8px;
  position: relative;
}

.hero-currency-wrap {
  position: relative;
  flex-shrink: 0;
}

.currency-pill {
  display: inline-flex;
  align-items: center;
  padding: 0.3rem 0.6rem;
  background: #60a5fa;
  color: #fff;
  border: none;
  border-radius: 8px;
  font-size: 0.78rem;
  font-weight: 700;
  font-family: var(--font-num);
  letter-spacing: 0.04em;
  cursor: pointer;
  width: auto;
  margin-bottom: 0;
  white-space: nowrap;
}

.currency-pill.is-open {
  opacity: 0.85;
}

.currency-picker-wrap {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 20;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  padding: 0.6rem;
  min-width: 220px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
}

.amount-input {
  flex: 1;
  height: 60px;
  font-size: 2rem;
  font-weight: 500;
  font-family: var(--font-num);
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--border);
  border-radius: 0;
  color: var(--text);
  padding: 0 0.25rem;
  text-align: right;
}

.amount-input:focus {
  outline: none;
  border-bottom-color: var(--accent);
}
</style>

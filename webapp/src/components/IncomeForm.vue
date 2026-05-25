<script setup>
import { ref, onMounted } from "vue";
import { Calendar } from "lucide-vue-next";
import CurrencyPicker from "./CurrencyPicker.vue";
import { useCurrencyStore } from "../stores/currency.js";
import { useIncomeStore } from "../stores/income.js";
import { useToastStore } from "../stores/toast.js";
import { useOnline } from "../composables/useOnline.js";

const currency = useCurrencyStore();
const incomeStore = useIncomeStore();
const toast = useToastStore();
const { isOnline } = useOnline();

const props = defineProps({
  disabled: { type: Boolean, default: false },
});
const emit = defineEmits(["saved"]);

function currentMonth() {
  return new Date().toISOString().slice(0, 7);
}

const amount = ref("");
const selectedCurrency = ref("");
const monthValue = ref(currentMonth());
const currencyPickerOpen = ref(false);
const submitting = ref(false);

onMounted(async () => {
  try {
    await currency.loadIfNeeded();
  } catch {}
  if (!selectedCurrency.value) {
    selectedCurrency.value = currency.defaultCode || "RSD";
  }
});

async function save() {
  if (props.disabled || !isOnline.value) return;
  const rawAmount = String(amount.value).replace(",", ".").trim();
  const parsed = Number.parseFloat(rawAmount);
  if (!rawAmount || Number.isNaN(parsed) || parsed <= 0) {
    toast.show("Enter a valid amount", "error");
    return;
  }
  const [yearStr, monthStr] = monthValue.value.split("-");
  const year = parseInt(yearStr, 10);
  const month = parseInt(monthStr, 10);
  const code = selectedCurrency.value || currency.preferredCode || "EUR";

  submitting.value = true;
  try {
    await incomeStore.add({ year, month, amount_original: parsed, currency_original: code });
    currency.setLastUsed(code);
    amount.value = "";
    monthValue.value = currentMonth();
    emit("saved");
  } catch {
    // errors handled by store
  } finally {
    submitting.value = false;
  }
}

defineExpose({ save });
</script>

<template>
  <div class="card" :style="disabled ? { opacity: 0.55, pointerEvents: 'none' } : {}">
    <div class="hero-row">
      <div class="hero-currency-wrap">
        <button
          type="button"
          class="currency-pill"
          :class="{ 'is-open': currencyPickerOpen }"
          aria-label="Select currency"
          @click="currencyPickerOpen = !currencyPickerOpen"
        >
          {{ selectedCurrency || "EUR" }}
        </button>
        <div v-if="currencyPickerOpen" class="currency-picker-wrap">
          <CurrencyPicker v-model="selectedCurrency" accent-color="var(--success)" @close="currencyPickerOpen = false" />
        </div>
      </div>

      <input
        v-model="amount"
        type="text"
        inputmode="decimal"
        placeholder="0"
        autocomplete="off"
        class="hero-amount"
        aria-label="Amount"
      />

      <div class="date-field">
        <Calendar :size="14" class="date-icon" aria-hidden="true" />
        <input v-model="monthValue" type="month" class="month-input" aria-label="Month" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.hero-row {
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
  background: var(--success);
  color: #04140a;
  border: none;
  border-radius: 8px;
  font-size: 0.78rem;
  font-weight: 700;
  font-family: var(--font-num);
  letter-spacing: 0.04em;
  cursor: pointer;
  width: auto;
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

.hero-amount {
  flex: 1;
  height: 64px;
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

.hero-amount:focus {
  outline: none;
  border-bottom-color: var(--success);
}

.date-field {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.date-icon {
  color: var(--muted);
}

.month-input {
  width: auto;
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--border);
  border-radius: 0;
  color: var(--muted);
  font-size: 0.8rem;
  padding: 0.2rem 0;
  min-width: 0;
}

.month-input:focus {
  outline: none;
  border-bottom-color: var(--success);
}
</style>

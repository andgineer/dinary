<script setup>
import { ref, onMounted } from "vue";
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

function today() {
  return new Date().toISOString().slice(0, 10);
}

function currentMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

const amount = ref("");
const selectedCurrency = ref("");
const monthValue = ref(currentMonth());
const dateValue = ref(today());
const comment = ref("");
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
  const code = selectedCurrency.value || currency.defaultCode || "EUR";

  const [yearStr, monthStr] = monthValue.value.split("-");
  submitting.value = true;
  try {
    await incomeStore.add({
      year: parseInt(yearStr, 10),
      month: parseInt(monthStr, 10),
      income_date: dateValue.value,
      amount_original: parsed,
      currency_original: code,
      comment: comment.value || null,
    });
    currency.setLastUsed(code);
    amount.value = "";
    comment.value = "";
    monthValue.value = currentMonth();
    dateValue.value = today();
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

    </div>

    <div class="date-row">
      <div class="date-col">
        <span class="field-label">For month</span>
        <input v-model="monthValue" type="month" class="date-input" aria-label="Accounting month" />
      </div>
      <div class="date-col">
        <span class="field-label">Received date</span>
        <input v-model="dateValue" type="date" class="date-input" aria-label="Received date" />
      </div>
    </div>

    <input
      v-model="comment"
      type="text"
      placeholder="Comment (optional)"
      autocomplete="off"
      class="comment-input"
      aria-label="Comment"
    />
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

.date-row {
  display: flex;
  gap: 12px;
  margin-top: 0.6rem;
}

.date-col {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}

.field-label {
  font-size: 0.68rem;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--muted);
}

.date-input {
  width: 100%;
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--border);
  border-radius: 0;
  color: var(--text);
  font-size: 0.82rem;
  padding: 0.2rem 0;
  min-width: 0;
  box-sizing: border-box;
}

.date-input:focus {
  outline: none;
  border-bottom-color: var(--success);
}

.comment-input {
  margin-top: 0.6rem;
  width: 100%;
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--border);
  border-radius: 0;
  color: var(--text);
  font-size: 0.85rem;
  padding: 0.25rem 0;
  box-sizing: border-box;
}

.comment-input::placeholder {
  color: var(--muted);
}

.comment-input:focus {
  outline: none;
  border-bottom-color: var(--success);
}
</style>

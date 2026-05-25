<script setup>
import { ref, watch } from "vue";
import { Trash2, Calendar } from "lucide-vue-next";
import BaseSheet from "./BaseSheet.vue";
import ConfirmDeleteSheet from "./ConfirmDeleteSheet.vue";
import CurrencyPicker from "./CurrencyPicker.vue";
import { useIncomeStore } from "../stores/income.js";
import { useToastStore } from "../stores/toast.js";
import { useCurrencyStore } from "../stores/currency.js";

const props = defineProps({
  open: { type: Boolean, default: false },
  income: { type: Object, default: null },
});
const emit = defineEmits(["close"]);

const incomeStore = useIncomeStore();
const toast = useToastStore();
const currencyStore = useCurrencyStore();

const amount = ref("");
const selectedCurrency = ref("");
const currencyPickerOpen = ref(false);
const submitting = ref(false);
const confirmingDelete = ref(false);
const deleting = ref(false);

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) {
      currencyPickerOpen.value = false;
      confirmingDelete.value = false;
      return;
    }
    amount.value = props.income?.amount != null ? String(props.income.amount) : "";
    selectedCurrency.value = props.income?.currency || currencyStore.defaultCode || "EUR";
    submitting.value = false;
  },
  { immediate: true },
);

function monthLabel(income) {
  if (!income) return "";
  return new Date(income.year, income.month - 1, 1).toLocaleString("en", { month: "long", year: "numeric" });
}

async function save() {
  if (submitting.value || !props.income) return;
  const parsed = Number.parseFloat(String(amount.value).replace(",", "."));
  if (!amount.value || Number.isNaN(parsed) || parsed <= 0) {
    toast.show("Enter a valid amount", "error");
    return;
  }
  submitting.value = true;
  try {
    await incomeStore.patch(props.income.year, props.income.month, {
      amount_original: parsed,
      currency_original: selectedCurrency.value,
    });
    emit("close");
  } catch {
    // error toast handled by store
  } finally {
    submitting.value = false;
  }
}

async function confirmDelete() {
  if (deleting.value || !props.income) return;
  deleting.value = true;
  try {
    await incomeStore.remove(props.income.year, props.income.month);
    confirmingDelete.value = false;
    emit("close");
  } catch {
    // error toast handled by store
  } finally {
    deleting.value = false;
  }
}
</script>

<template>
  <BaseSheet
    :open="open"
    :dimmed="confirmingDelete"
    aria-label="Edit income"
    data-testid="income-edit-sheet"
    @close="emit('close')"
  >
    <template #header>
      <span class="sheet-eyebrow">EDIT INCOME</span>
    </template>

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
          <CurrencyPicker v-model="selectedCurrency" @close="currencyPickerOpen = false" />
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

      <div class="month-display">
        <Calendar :size="14" class="date-icon" aria-hidden="true" />
        <span class="month-label">{{ monthLabel(income) }}</span>
      </div>
    </div>

    <template #footer>
      <button
        type="button"
        class="btn-delete"
        data-testid="delete-btn"
        @click="confirmingDelete = true"
      >
        <Trash2 :size="14" aria-hidden="true" />
        Delete
      </button>
      <button
        type="button"
        class="btn-save"
        :disabled="submitting"
        data-testid="save-btn"
        @click="save"
      >
        Save
      </button>
    </template>
  </BaseSheet>

  <ConfirmDeleteSheet
    :open="confirmingDelete"
    kind="income"
    title="Delete this income?"
    destructive-label="Delete"
    :loading="deleting"
    @cancel="confirmingDelete = false"
    @confirm="confirmDelete"
  >
    <template #body>
      <span v-if="income">
        Income for
        <span class="confirm-highlight">{{ monthLabel(income) }}</span>.
        This can't be undone.
      </span>
    </template>
  </ConfirmDeleteSheet>
</template>

<style scoped>
.sheet-eyebrow {
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--muted);
  text-transform: uppercase;
}

.hero-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 1.25rem;
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

.month-display {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.date-icon {
  color: var(--muted);
}

.month-label {
  font-size: 0.8rem;
  color: var(--muted);
  font-family: var(--font-num);
  white-space: nowrap;
}

.btn-delete {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.5rem 0.7rem;
  background: transparent;
  border: 1px solid rgba(239, 68, 68, 0.3);
  border-radius: 8px;
  color: #fca5a5;
  font-size: 0.85rem;
  cursor: pointer;
  white-space: nowrap;
  width: auto;
  flex-shrink: 0;
}

.btn-save {
  flex: 1;
  padding: 0.5rem 1rem;
  background: var(--success);
  color: #04140a;
  border: none;
  border-radius: 8px;
  font-size: 0.9rem;
  font-weight: 600;
  cursor: pointer;
}

.btn-save:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.confirm-highlight {
  font-family: var(--font-num);
  color: var(--text);
}
</style>

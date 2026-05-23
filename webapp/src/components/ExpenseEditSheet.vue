<script setup>
import { computed, ref, watch } from "vue";
import { Check, Receipt, Trash2, X } from "lucide-vue-next";
import { useCatalogStore } from "../stores/catalog.js";
import { useReviewStore } from "../stores/review.js";
import { useToastStore } from "../stores/toast.js";
import { useCurrencyStore } from "../stores/currency.js";
import { useOnline } from "../composables/useOnline.js";
import { getReceipt } from "../api/receipts.js";
import CategorySheet from "./CategorySheet.vue";
import ConfirmDeleteSheet from "./ConfirmDeleteSheet.vue";
import CurrencyPicker from "./CurrencyPicker.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  expense: { type: Object, default: null },
  suggestions: { type: Array, default: () => [] },
  ruleItem: { type: Object, default: null },
});
const emit = defineEmits(["close"]);

const catalog = useCatalogStore();
const reviewStore = useReviewStore();
const toast = useToastStore();
const currencyStore = useCurrencyStore();
const { isOnline } = useOnline();

const selectedCategoryId = ref(null);
const selectedTagIds = ref(new Set());
const selectedEventId = ref(null);
const scope = ref("single");
const updateRule = ref(false);
const categorySheetOpen = ref(false);
const submitting = ref(false);

// Amount editing (manual expenses only)
const amount = ref("");
const selectedCurrency = ref("");
const currencyPickerOpen = ref(false);

// Delete flow
const confirmingDelete = ref(false);
const deleting = ref(false);
const cascade = ref(null);
const cascadeLoading = ref(false);

const SCOPE_OPTIONS = [
  { value: "single", label: "Only this" },
  { value: "month", label: "Last month" },
  { value: "year", label: "This year" },
  { value: "all", label: "All history" },
];

const source = computed(() => props.expense ?? props.ruleItem);
const isManual = computed(() => props.expense?.receipt_id == null);
const isReceiptBacked = computed(() => props.expense?.receipt_id != null);

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) {
      categorySheetOpen.value = false;
      confirmingDelete.value = false;
      cascade.value = null;
      cascadeLoading.value = false;
      currencyPickerOpen.value = false;
      return;
    }
    const src = source.value;
    selectedCategoryId.value = src?.category_id != null ? Number(src.category_id) : null;
    selectedTagIds.value = new Set((src?.tags ?? []).map((t) => Number(t.id ?? t)));
    selectedEventId.value = props.expense?.event_id != null ? Number(props.expense.event_id) : null;
    scope.value = "single";
    updateRule.value = false;
    submitting.value = false;
    amount.value = props.expense?.amount_original != null ? String(props.expense.amount_original) : "";
    selectedCurrency.value =
      props.expense?.currency_original || currencyStore.preferredCode || "";
  },
  { immediate: true },
);

const selectedCategory = computed(() =>
  selectedCategoryId.value ? catalog.findCategoryById(selectedCategoryId.value) : null,
);

const activeTags = computed(() => catalog.tags);
const activeEvents = computed(() => catalog.events());
const showScope = computed(() => props.expense?.receipt_id != null);
const showUpdateRule = computed(
  () => props.expense?.receipt_id != null && props.expense?.has_rule === true,
);

function toggleTag(tagId) {
  const id = Number(tagId);
  const next = new Set(selectedTagIds.value);
  if (next.has(id)) {
    next.delete(id);
  } else {
    next.add(id);
  }
  selectedTagIds.value = next;
}

function onEventSelect(eventId) {
  const id = eventId ? Number(eventId) : null;
  selectedEventId.value = id;
  if (id) {
    const ev = activeEvents.value.find((e) => e.id === id);
    if (ev?.auto_tags?.length) {
      const next = new Set(selectedTagIds.value);
      for (const tid of ev.auto_tags) {
        next.add(Number(tid));
      }
      selectedTagIds.value = next;
    }
  }
}

function _resolvedTags() {
  const ids = new Set([...selectedTagIds.value].map(Number));
  return activeTags.value.filter((t) => ids.has(Number(t.id)));
}

async function save() {
  if (submitting.value) return;

  if (isManual.value && props.expense) {
    const parsed = Number.parseFloat(String(amount.value).replace(",", "."));
    if (!amount.value || Number.isNaN(parsed) || parsed <= 0) {
      toast.show("Enter a valid amount", "error");
      return;
    }
  }

  submitting.value = true;
  try {
    if (props.expense) {
      const patch = {
        category_id: selectedCategoryId.value,
        tag_ids: [...selectedTagIds.value],
        event_id: selectedEventId.value,
        clear_event: selectedEventId.value === null,
        scope: scope.value,
        update_rule: updateRule.value,
      };
      if (isManual.value) {
        patch.amount_original = Number.parseFloat(String(amount.value).replace(",", "."));
        patch.currency_original = selectedCurrency.value;
      }
      await reviewStore.updateExpense(props.expense.id, patch);
      const ev = selectedEventId.value
        ? (activeEvents.value.find((e) => e.id === selectedEventId.value) ?? null)
        : null;
      reviewStore.patchExpense(props.expense.id, {
        category_id: selectedCategoryId.value,
        category_name: selectedCategory.value?.name ?? null,
        tags: _resolvedTags(),
        event_id: selectedEventId.value,
        event_name: ev?.name ?? null,
        ...(isManual.value
          ? {
              amount_original: Number.parseFloat(String(amount.value).replace(",", ".")),
              currency_original: selectedCurrency.value,
            }
          : {}),
      });
    } else if (props.ruleItem) {
      await reviewStore.correct(props.ruleItem, selectedCategoryId.value, "all");
      const expenseId = props.ruleItem.expense_id ?? props.ruleItem.id;
      await reviewStore.updateExpense(expenseId, {
        tag_ids: [...selectedTagIds.value],
        update_rule: true,
      });
      reviewStore.patchExpense(expenseId, { tags: _resolvedTags() });
    }
    emit("close");
  } finally {
    submitting.value = false;
  }
}

function openDeleteConfirm() {
  if (!isOnline.value) {
    toast.show("Not available offline", "error");
    return;
  }
  if (isReceiptBacked.value && !cascade.value) {
    _fetchCascade();
  }
  confirmingDelete.value = true;
}

async function _fetchCascade() {
  cascadeLoading.value = true;
  try {
    cascade.value = await getReceipt(props.expense.receipt_id, { include: "expenses" });
  } catch {
    // cascade card will show loading until retry
  } finally {
    cascadeLoading.value = false;
  }
}

async function confirmDelete() {
  if (!isOnline.value) {
    toast.show("Not available offline", "error");
    return;
  }
  if (deleting.value) return;
  deleting.value = true;
  try {
    if (isManual.value) {
      await reviewStore.deleteExpense(props.expense.id);
      toast.show("Expense deleted", "info");
    } else {
      const receiptId = props.expense.receipt_id;
      const count = cascade.value?.expenses?.length ?? 0;
      await reviewStore.deleteReceipt(receiptId);
      toast.show(`Receipt deleted (${count} expense${count !== 1 ? "s" : ""} removed)`, "info");
    }
    confirmingDelete.value = false;
    emit("close");
  } catch (err) {
    toast.show(err?.message || "Delete failed", "error");
  } finally {
    deleting.value = false;
  }
}

function cancelDelete() {
  confirmingDelete.value = false;
}

function _formatDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  } catch {
    return iso;
  }
}
</script>

<template>
  <Teleport to="body">
    <Transition name="scrim">
      <div v-if="open" class="sheet-scrim" @click="emit('close')" />
    </Transition>
    <Transition name="sheet">
      <div
        v-if="open"
        class="sheet"
        :class="{ 'sheet-dimmed': confirmingDelete }"
        role="dialog"
        aria-modal="true"
        aria-label="Edit expense"
        data-testid="expense-edit-sheet"
      >
        <div class="drag-handle" />

        <div class="sheet-header">
          <span class="sheet-eyebrow">EDIT EXPENSE</span>
          <span v-if="isReceiptBacked" class="from-receipt-pill" data-testid="from-receipt-pill">
            <Receipt :size="12" aria-hidden="true" />
            FROM RECEIPT
          </span>
          <button type="button" class="sheet-close" aria-label="Close" @click="emit('close')">
            <X :size="16" />
          </button>
        </div>

        <div class="sheet-body">
          <!-- Amount + Currency (manual expenses only) -->
          <div v-if="isManual && expense" class="field-block" data-testid="amount-block">
            <div class="field-label">AMOUNT</div>
            <div class="amount-row">
              <div class="hero-currency-wrap">
                <button
                  type="button"
                  class="currency-pill"
                  :class="{ 'is-open': currencyPickerOpen }"
                  aria-label="Select currency"
                  data-testid="currency-pill"
                  @click="currencyPickerOpen = !currencyPickerOpen"
                >
                  {{ selectedCurrency || "RSD" }}
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
                class="amount-input"
                aria-label="Amount"
                data-testid="amount-input"
              />
            </div>
          </div>

          <!-- Category -->
          <div class="field-block">
            <div class="field-label">CATEGORY</div>
            <button
              type="button"
              class="category-chip"
              data-testid="category-chip"
              @click="categorySheetOpen = true"
            >
              {{ selectedCategory?.name ?? "— select —" }}
              <span class="chip-arrow">▾</span>
            </button>
          </div>

          <!-- Tags -->
          <div class="field-block">
            <div class="field-label">TAGS</div>
            <div class="tag-toggle-row">
              <button
                v-for="tag in activeTags"
                :key="tag.id"
                type="button"
                class="tag-toggle"
                :class="{ 'is-on': selectedTagIds.has(Number(tag.id)) }"
                :data-testid="`tag-toggle-${tag.id}`"
                @click="toggleTag(tag.id)"
              >
                <Check
                  v-if="selectedTagIds.has(Number(tag.id))"
                  :size="11"
                  class="tag-check"
                  aria-hidden="true"
                />
                {{ tag.name }}
              </button>
            </div>
          </div>

          <!-- Event -->
          <div class="field-block">
            <div class="field-label">EVENT</div>
            <select
              class="event-select"
              :value="selectedEventId ?? ''"
              data-testid="event-select"
              @change="onEventSelect($event.target.value || null)"
            >
              <option value="">None</option>
              <option v-for="ev in activeEvents" :key="ev.id" :value="ev.id">
                {{ ev.name }}
              </option>
            </select>
          </div>

          <!-- Scope selector (receipt-backed only) -->
          <div v-if="showScope" class="field-block scope-block" data-testid="scope-selector">
            <div class="field-label">SCOPE</div>
            <div class="scope-row">
              <label v-for="opt in SCOPE_OPTIONS" :key="opt.value" class="scope-option">
                <input
                  type="radio"
                  :value="opt.value"
                  :checked="scope === opt.value"
                  class="scope-radio"
                  @change="scope = opt.value"
                />
                {{ opt.label }}
              </label>
            </div>
          </div>

          <!-- Update rule checkbox (has_rule only) -->
          <div v-if="showUpdateRule" class="field-block" data-testid="update-rule-wrap">
            <label class="update-rule-label">
              <input v-model="updateRule" type="checkbox" data-testid="update-rule-checkbox" />
              Also update rule
            </label>
          </div>
        </div>

        <div class="sheet-footer">
          <!-- Delete button -->
          <button
            v-if="expense"
            type="button"
            class="btn-delete"
            :class="{ 'btn-delete-tint': isReceiptBacked }"
            data-testid="delete-btn"
            @click="openDeleteConfirm"
          >
            <Trash2 :size="14" aria-hidden="true" />
            {{ isReceiptBacked ? "Delete receipt" : "Delete" }}
          </button>

          <button type="button" class="btn-cancel" @click="emit('close')">Cancel</button>
          <button
            type="button"
            class="btn btn-primary save-btn"
            :disabled="!selectedCategoryId || submitting"
            data-testid="save-btn"
            @click="save"
          >
            Save
          </button>
        </div>
      </div>
    </Transition>
  </Teleport>

  <CategorySheet
    v-if="open"
    :open="categorySheetOpen"
    :suggestions="suggestions"
    @select="selectedCategoryId = $event"
    @close="categorySheetOpen = false"
  />

  <!-- Manual expense delete confirm -->
  <ConfirmDeleteSheet
    v-if="isManual"
    :open="confirmingDelete"
    kind="expense"
    title="Delete this expense?"
    :destructive-label="'Delete'"
    :loading="deleting"
    @cancel="cancelDelete"
    @confirm="confirmDelete"
  >
    <template #body>
      <span v-if="expense">
        <span class="confirm-highlight">{{ expense.amount_original }} {{ expense.currency_original }}</span>
        on {{ expense.category_name }}{{ expense.datetime ? ", " + _formatDate(expense.datetime) : "" }}.
        This can't be undone.
      </span>
    </template>
  </ConfirmDeleteSheet>

  <!-- Receipt-backed delete confirm -->
  <ConfirmDeleteSheet
    v-if="isReceiptBacked"
    :open="confirmingDelete"
    kind="receipt"
    title="Delete this receipt?"
    :destructive-label="cascade ? `Delete ${cascade.expenses.length} item${cascade.expenses.length !== 1 ? 's' : ''}` : 'Delete'"
    :loading="deleting"
    @cancel="cancelDelete"
    @confirm="confirmDelete"
  >
    <template #body>
      This deletes the whole receipt and all
      <span class="confirm-highlight">{{ cascade?.expenses?.length ?? "…" }} expenses</span>
      created from it — not just this one. This can't be undone.
    </template>
    <template #detail>
      <div class="cascade-card" data-testid="cascade-card">
        <div v-if="cascadeLoading" class="cascade-loading">Loading…</div>
        <template v-else-if="cascade">
          <div class="cascade-header">
            <span class="cascade-merchant">{{ cascade.merchant || "Receipt" }}</span>
            <span class="cascade-date">{{ _formatDate(cascade.captured_at) }}</span>
          </div>
          <div
            v-for="item in cascade.expenses"
            :key="item.id"
            class="cascade-row"
          >
            <span class="cascade-item-name">{{ item.item_name || "—" }}</span>
            <span class="cascade-item-amount">{{ item.amount }} {{ item.currency }}</span>
          </div>
          <div class="cascade-footer">
            <span class="cascade-total-label">TOTAL</span>
            <span class="cascade-total-amount">
              {{ cascade.total.amount.toFixed(2) }} {{ cascade.total.currency }}
            </span>
          </div>
        </template>
      </div>
    </template>
  </ConfirmDeleteSheet>
</template>

<style scoped>
.scrim-enter-active,
.scrim-leave-active {
  transition: opacity 0.26s;
}
.scrim-enter-from,
.scrim-leave-to {
  opacity: 0;
}

.sheet-enter-active,
.sheet-leave-active {
  transition: transform 0.28s cubic-bezier(0.32, 0, 0.67, 0);
}
.sheet-enter-from,
.sheet-leave-to {
  transform: translateY(100%);
}

.sheet-scrim {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  z-index: 40;
}

.sheet {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 45;
  background: var(--surface);
  border-radius: 18px 18px 0 0;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 -4px 24px rgba(0, 0, 0, 0.35);
  transition: opacity 0.18s, filter 0.18s;
}

.sheet-dimmed {
  opacity: 0.55;
  filter: blur(0.5px);
  pointer-events: none;
}

.drag-handle {
  width: 36px;
  height: 4px;
  border-radius: 2px;
  background: var(--border-strong);
  margin: 10px auto 0;
  flex-shrink: 0;
}

.sheet-header {
  padding: 0.75rem 3rem 0.5rem 1rem;
  position: relative;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.sheet-eyebrow {
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--muted);
  text-transform: uppercase;
}

.from-receipt-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.12);
  color: var(--muted);
  font-size: 0.62rem;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.sheet-close {
  position: absolute;
  top: 0.75rem;
  right: 1rem;
  background: none;
  border: none;
  color: var(--muted);
  cursor: pointer;
  padding: 0.25rem;
  width: auto;
  display: flex;
  align-items: center;
}

.sheet-body {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem 1rem;
}

.field-block {
  margin-bottom: 1.25rem;
}

.field-label {
  font-size: 0.62rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 0.4rem;
}

/* Amount row */
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
  background: var(--accent);
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

.category-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.45rem 0.75rem;
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 9px;
  color: var(--text);
  font-size: 0.9rem;
  cursor: pointer;
  width: 100%;
  text-align: left;
  transition: border-color 0.12s;
}

.category-chip:hover {
  border-color: var(--border-strong);
}

.chip-arrow {
  margin-left: auto;
  color: var(--muted);
  font-size: 0.75rem;
}

.tag-toggle-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}

.tag-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0.3rem 0.65rem;
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--muted);
  font-size: 0.8rem;
  cursor: pointer;
  white-space: nowrap;
  transition: border-color 0.12s, color 0.12s;
  width: auto;
}

.tag-toggle.is-on {
  border-color: var(--accent);
  color: var(--text);
}

.tag-check {
  color: var(--accent);
  flex-shrink: 0;
}

.event-select {
  width: 100%;
  font-size: 0.9rem;
}

.scope-block {
  border-top: 1px solid var(--border);
  padding-top: 1rem;
}

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

.update-rule-label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.88rem;
  color: var(--text);
  cursor: pointer;
  text-transform: none;
  letter-spacing: 0;
  font-weight: normal;
}

.sheet-footer {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem 1rem calc(0.75rem + env(safe-area-inset-bottom, 0px));
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}

.btn-delete {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.5rem 0.7rem;
  background: transparent;
  border: 1px solid rgba(239, 68, 68, 0.30);
  border-radius: 8px;
  color: #fca5a5;
  font-size: 0.85rem;
  cursor: pointer;
  white-space: nowrap;
  width: auto;
  flex-shrink: 0;
}

.btn-delete-tint {
  background: rgba(239, 68, 68, 0.10);
}

.btn-cancel {
  flex: 1;
  padding: 0.5rem 1rem;
  background: none;
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--muted);
  font-size: 0.9rem;
  cursor: pointer;
}

.save-btn {
  flex: 1;
  padding: 0.5rem 1rem;
  font-size: 0.9rem;
}

/* Confirm body text highlight */
.confirm-highlight {
  font-family: var(--font-num);
  color: var(--text);
}

/* Cascade card (receipt delete summary) */
.cascade-card {
  background: var(--field-deep);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  font-size: 0.85rem;
}

.cascade-loading {
  padding: 1rem;
  color: var(--muted);
  font-size: 0.85rem;
  text-align: center;
}

.cascade-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 0.6rem 0.75rem;
  border-bottom: 1px solid var(--border);
}

.cascade-merchant {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--text);
}

.cascade-date {
  font-family: var(--font-num);
  font-size: 0.78rem;
  color: var(--muted);
}

.cascade-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 0.35rem 0.75rem;
  gap: 0.5rem;
}

.cascade-item-name {
  flex: 1;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cascade-item-amount {
  font-family: var(--font-num);
  font-size: 0.82rem;
  color: var(--muted);
  flex-shrink: 0;
}

.cascade-footer {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 0.5rem 0.75rem;
  border-top: 1px solid var(--border);
  background: rgba(255, 255, 255, 0.02);
}

.cascade-total-label {
  font-size: 0.7rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
}

.cascade-total-amount {
  font-family: var(--font-num);
  font-weight: 700;
  color: var(--text);
}
</style>

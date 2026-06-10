<script setup>
import { computed, ref, watch } from "vue";
import { Check, Receipt, Trash2 } from "lucide-vue-next";
import { useCatalogStore } from "../stores/catalog.js";
import { useReviewStore } from "../stores/review.js";
import { useToastStore } from "../stores/toast.js";
import { useCurrencyStore } from "../stores/currency.js";
import { useExpenseDeleteFlow } from "../composables/useExpenseDeleteFlow.js";
import BaseSheet from "./BaseSheet.vue";
import CategorySheet from "./CategorySheet.vue";
import ConfirmDeleteSheet from "./ConfirmDeleteSheet.vue";
import CurrencyAmountRow from "./CurrencyAmountRow.vue";
import ReceiptCascadeCard from "./ReceiptCascadeCard.vue";
import ScopeSelector from "./ScopeSelector.vue";

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

const selectedCategoryId = ref(null);
const selectedTagIds = ref(new Set());
const selectedEventId = ref(null);
const scope = ref("single");
const updateRule = ref(false);
const categorySheetOpen = ref(false);
const submitting = ref(false);
const amount = ref("");
const selectedCurrency = ref("");

const SCOPE_OPTIONS = [
  { value: "single", label: "Only this" },
  { value: "month", label: "Last month" },
  { value: "year", label: "This year" },
  { value: "all", label: "All history" },
];

const source = computed(() => props.expense ?? props.ruleItem);
const isManual = computed(() => props.expense?.receipt_id == null);
const isReceiptBacked = computed(() => props.expense?.receipt_id != null);

const {
  confirmingDelete,
  deleting,
  cascade,
  cascadeLoading,
  resetDeleteState,
  openDeleteConfirm,
  confirmDelete,
  cancelDelete,
} = useExpenseDeleteFlow({
  getExpense: () => props.expense,
  isManual,
  isReceiptBacked,
  onClose: () => emit("close"),
});

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) {
      categorySheetOpen.value = false;
      resetDeleteState();
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

const visibleTags = computed(() => {
  const inactive = catalog.inactiveTags.filter((t) => selectedTagIds.value.has(Number(t.id)));
  return [...catalog.tags, ...inactive];
});

const visibleEvents = computed(() => {
  const active = catalog.activeEventsLast();
  if (!selectedEventId.value) return active;
  if (active.find((e) => e.id === selectedEventId.value)) return active;
  const ev = catalog.findEventById(selectedEventId.value);
  return ev ? [ev, ...active] : active;
});

const showScope = computed(() => props.expense?.receipt_id != null);
const showUpdateRule = computed(
  () => props.expense?.receipt_id != null && props.expense?.has_rule === true,
);

function toggleTag(tagId) {
  const id = Number(tagId);
  const next = new Set(selectedTagIds.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  selectedTagIds.value = next;
}

function onEventSelect(eventId) {
  const id = eventId ? Number(eventId) : null;
  const next = new Set(selectedTagIds.value);
  if (selectedEventId.value) {
    const prev = catalog.findEventById(selectedEventId.value);
    for (const tid of prev?.auto_tags ?? []) next.delete(Number(tid));
  }
  selectedEventId.value = id;
  if (id) {
    const ev = catalog.findEventById(id);
    for (const tid of ev?.auto_tags ?? []) next.add(Number(tid));
  }
  selectedTagIds.value = next;
}

function _resolvedTags() {
  const ids = new Set([...selectedTagIds.value].map(Number));
  return visibleTags.value.filter((t) => ids.has(Number(t.id)));
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
        ? (visibleEvents.value.find((e) => e.id === selectedEventId.value) ?? null)
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
      await reviewStore.updateExpense(expenseId, { tag_ids: [...selectedTagIds.value], update_rule: true });
      reviewStore.patchExpense(expenseId, { tags: _resolvedTags() });
    }
    emit("close");
  } finally {
    submitting.value = false;
  }
}

function _formatDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  } catch {
    return iso;
  }
}

function onReceiptResolved() {
  cancelDelete();
  emit("close");
}
</script>

<template>
  <BaseSheet
    :open="open"
    :dimmed="confirmingDelete"
    aria-label="Edit expense"
    data-testid="expense-edit-sheet"
    @close="emit('close')"
  >
    <template #header>
      <span class="sheet-eyebrow">EDIT EXPENSE</span>
      <span v-if="isReceiptBacked" class="from-receipt-pill" data-testid="from-receipt-pill">
        <Receipt :size="12" aria-hidden="true" />
        FROM RECEIPT
      </span>
    </template>

    <!-- Amount + Currency (manual expenses only) -->
    <div v-if="isManual && expense" class="field-block" data-testid="amount-block">
      <div class="field-label">AMOUNT</div>
      <CurrencyAmountRow
        v-model:amount="amount"
        v-model:currency="selectedCurrency"
      />
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
          v-for="tag in visibleTags"
          :key="tag.id"
          type="button"
          class="tag-toggle"
          :class="{ 'is-on': selectedTagIds.has(Number(tag.id)), 'is-inactive': !tag.is_active }"
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
        <option v-for="ev in visibleEvents" :key="ev.id" :value="ev.id">
          {{ ev.name }}{{ ev.is_active === false ? " (inactive)" : "" }}
        </option>
      </select>
    </div>

    <!-- Scope selector (receipt-backed only) -->
    <div v-if="showScope" class="field-block scope-block" data-testid="scope-selector">
      <div class="field-label">SCOPE</div>
      <ScopeSelector v-model="scope" :options="SCOPE_OPTIONS" />
    </div>

    <!-- Update rule checkbox (has_rule only) -->
    <div v-if="showUpdateRule" class="field-block" data-testid="update-rule-wrap">
      <label class="update-rule-label">
        <input v-model="updateRule" type="checkbox" data-testid="update-rule-checkbox" />
        Also update rule
      </label>
    </div>

    <template #footer>
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
    </template>
  </BaseSheet>

  <CategorySheet
    v-if="open"
    :open="categorySheetOpen"
    :suggestions="suggestions"
    @select="selectedCategoryId = $event"
    @close="categorySheetOpen = false"
  />

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
      <ReceiptCascadeCard
        :loading="cascadeLoading"
        :cascade="cascade"
        @resolved="onReceiptResolved"
      />
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

.tag-toggle.is-inactive {
  opacity: 0.6;
  border-style: dashed;
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
  background: #60a5fa;
  color: #fff;
}

.confirm-highlight {
  font-family: var(--font-num);
  color: var(--text);
}
</style>

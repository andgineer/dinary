<script setup>
import { computed, ref, watch } from "vue";
import { Check, X } from "lucide-vue-next";
import { useCatalogStore } from "../stores/catalog.js";
import { useReviewStore } from "../stores/review.js";
import CategorySheet from "./CategorySheet.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  expense: { type: Object, default: null },
  suggestions: { type: Array, default: () => [] },
  ruleItem: { type: Object, default: null },
});
const emit = defineEmits(["close"]);

const catalog = useCatalogStore();
const reviewStore = useReviewStore();

const selectedCategoryId = ref(null);
const selectedTagIds = ref(new Set());
const selectedEventId = ref(null);
const scope = ref("single");
const updateRule = ref(false);
const categorySheetOpen = ref(false);
const submitting = ref(false);

const SCOPE_OPTIONS = [
  { value: "single", label: "Single" },
  { value: "month", label: "Last month" },
  { value: "year", label: "This year" },
  { value: "all", label: "All history" },
];

const source = computed(() => props.expense ?? props.ruleItem);

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) {
      categorySheetOpen.value = false;
      return;
    }
    const src = source.value;
    selectedCategoryId.value = src?.category_id != null ? Number(src.category_id) : null;
    selectedTagIds.value = new Set((src?.tags ?? []).map((t) => Number(t.id ?? t)));
    selectedEventId.value = props.expense?.event_id != null ? Number(props.expense.event_id) : null;
    scope.value = "single";
    updateRule.value = false;
    submitting.value = false;
  },
  { immediate: true },
);

const selectedCategory = computed(() =>
  selectedCategoryId.value ? catalog.findCategoryById(selectedCategoryId.value) : null,
);

const activeTags = computed(() => catalog.tags);

const activeEvents = computed(() => catalog.events());

const showScope = computed(() => props.expense?.receipt_id != null);
const showUpdateRule = computed(() =>
  props.expense?.receipt_id != null && props.expense?.has_rule === true,
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

async function save() {
  if (submitting.value) return;
  submitting.value = true;
  try {
    if (props.expense) {
      await reviewStore.updateExpense(props.expense.id, {
        category_id: selectedCategoryId.value,
        tag_ids: [...selectedTagIds.value],
        event_id: selectedEventId.value,
        clear_event: selectedEventId.value === null,
        scope: scope.value,
        update_rule: updateRule.value,
      });
    } else if (props.ruleItem) {
      await reviewStore.correct(props.ruleItem, selectedCategoryId.value, "all");
      const expenseId = props.ruleItem.expense_id ?? props.ruleItem.id;
      await reviewStore.updateExpense(expenseId, {
        tag_ids: [...selectedTagIds.value],
        update_rule: true,
      });
    }
    emit("close");
  } finally {
    submitting.value = false;
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
        role="dialog"
        aria-modal="true"
        aria-label="Edit expense"
        data-testid="expense-edit-sheet"
      >
        <div class="drag-handle" />

        <div class="sheet-header">
          <button type="button" class="sheet-close" aria-label="Close" @click="emit('close')">
            <X :size="16" />
          </button>
        </div>

        <div class="sheet-body">
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
}

.sheet-eyebrow {
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--muted);
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
</style>

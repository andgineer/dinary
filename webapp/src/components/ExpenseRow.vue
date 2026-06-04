<script setup>
import { computed, watch } from "vue";
import { Pencil } from "lucide-vue-next";
import { useSwipeRow } from "../composables/useSwipeRow.js";
import { useReviewStore } from "../stores/review.js";

const PANEL_WIDTH = 84;

const props = defineProps({
  expense: { type: Object, required: true },
  hideStore: { type: Boolean, default: false },
});
const emit = defineEmits(["tap"]);

const reviewStore = useReviewStore();

const { sliderEl, isOpen, isCommit, onPointerDown, onPointerMove, endDrag, shouldFireTap, close } =
  useSwipeRow({
    panelWidth: PANEL_WIDTH,
    commitOver: 60,
    onPrimary: () => emit("tap"),
  });

watch(isOpen, (val) => {
  if (val) reviewStore.setOpenRow(props.expense.id);
});

watch(
  () => reviewStore.openRowId,
  (id) => {
    if (isOpen.value && id !== props.expense.id) close();
  },
);

const primaryText = computed(() => {
  const e = props.expense;
  return e.item_name ?? e.store_name ?? e.store ?? e.merchant ?? null;
});

const secondaryText = computed(() => {
  if (props.hideStore) return null;
  const e = props.expense;
  if (e.item_name && (e.store_name ?? e.store)) return e.store_name ?? e.store;
  return null;
});

const amountText = computed(() => {
  const { amount_original: amt, currency_original: cur } = props.expense;
  if (amt == null) return null;
  const n = Number(amt);
  if (!isFinite(n)) return null;
  const fmt = n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return cur ? `${fmt} ${cur}` : fmt;
});

function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return `${d.getDate()} ${d.toLocaleString("en", { month: "short" })}`;
}

function onRowClick() {
  if (shouldFireTap()) emit("tap");
}

function onEditClick(e) {
  e.stopPropagation();
  close();
  emit("tap");
}
</script>

<template>
  <div
    class="row-wrap"
    :class="{ 'row-wrap--warning': expense.confidence_level != null && expense.confidence_level < 4 }"
    data-testid="expense-row"
    :data-expense-id="expense.id"
  >
    <div class="row-panel" :style="{ pointerEvents: isOpen ? 'auto' : 'none' }">
      <button
        type="button"
        class="panel-btn"
        :class="{ 'panel-btn--commit': isCommit }"
        aria-label="Edit expense"
        @click.stop="onEditClick($event)"
      >
        <Pencil :size="14" aria-hidden="true" />
        Edit
      </button>
    </div>

    <div
      ref="sliderEl"
      class="row-slider"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="endDrag"
      @pointercancel="endDrag"
      @click="onRowClick"
    >
      <div v-if="primaryText" class="row-top">
        <span class="row-primary">{{ primaryText }}</span>
        <span v-if="secondaryText" class="row-store">{{ secondaryText }}</span>
      </div>
      <div class="row-bottom">
        <span class="row-date">{{ formatDate(expense.date ?? expense.datetime) }}</span>
        <span class="row-category">{{ expense.category_name }}</span>
        <template v-if="expense.tags?.length">
          <span
            v-for="tag in expense.tags"
            :key="tag.id ?? tag"
            class="tag-chip"
          >
            <template v-if="tag.icon">{{ tag.icon }} </template>{{ tag.name ?? tag }}
          </span>
        </template>
        <span v-if="expense.event_name" class="event-name">· {{ expense.event_name }}</span>
        <span v-if="amountText" class="row-amount">{{ amountText }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.row-wrap {
  position: relative;
  overflow: hidden;
  background-color: var(--bg);
  border-radius: 10px;
  border: 1px solid var(--border);
  margin-bottom: 0.5rem;
}

.row-wrap--warning {
  border-left: 4px solid var(--warning);
  border-radius: 0 10px 10px 0;
}

.row-panel {
  position: absolute;
  top: 0;
  bottom: 0;
  right: 0;
  display: flex;
  align-items: stretch;
  pointer-events: none;
  width: 84px;
}

.panel-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  flex: 1;
  background: var(--surface-2);
  border: none;
  color: var(--text);
  font-size: 0.72rem;
  cursor: pointer;
  transition: background 0.15s, flex 0.15s;
}

.panel-btn--commit {
  background: var(--accent);
}

.row-slider {
  position: relative;
  z-index: 1;
  background-color: var(--bg);
  touch-action: pan-y;
  user-select: none;
  padding: 0.5rem 0.75rem;
  transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  cursor: pointer;
}

.row-wrap--warning .row-slider {
  background-image: linear-gradient(rgba(245, 158, 11, 0.07), rgba(245, 158, 11, 0.07));
}

.row-top {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  margin-bottom: 3px;
}

.row-primary {
  font-weight: 600;
  font-size: 0.9375rem;
  color: var(--text);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row-store {
  font-size: 0.78rem;
  color: var(--muted);
  white-space: nowrap;
  flex-shrink: 0;
}

.row-bottom {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
}

.row-date {
  font-size: 0.72rem;
  color: var(--muted);
  white-space: nowrap;
  flex-shrink: 0;
}

.row-category {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--text);
  white-space: nowrap;
  flex-shrink: 0;
}

.tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: 0.68rem;
  padding: 1px 5px;
  border-radius: 999px;
  background: var(--field);
  border: 1px solid var(--border);
  color: var(--muted);
}

.event-name {
  font-size: 0.72rem;
  color: var(--muted-2);
}

.row-amount {
  margin-left: auto;
  font-size: 0.72rem;
  color: var(--muted);
  white-space: nowrap;
  flex-shrink: 0;
}
</style>

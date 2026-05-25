<script setup>
import { watch } from "vue";
import { Pencil } from "lucide-vue-next";
import { useSwipeRow } from "../composables/useSwipeRow.js";
import { useIncomeStore } from "../stores/income.js";
import { useToastStore } from "../stores/toast.js";
import { useOnline } from "../composables/useOnline.js";

const PANEL_WIDTH = 84;

const props = defineProps({
  income: { type: Object, required: true },
});
const emit = defineEmits(["tap"]);

const incomeStore = useIncomeStore();
const toast = useToastStore();
const { isOnline } = useOnline();

const { sliderEl, isOpen, isCommit, onPointerDown, onPointerMove, endDrag, shouldFireTap, close } =
  useSwipeRow({
    panelWidth: PANEL_WIDTH,
    commitOver: 60,
    onPrimary: () => onEditTap(),
  });

const rowKey = () => `${props.income.year}-${props.income.month}`;

watch(isOpen, (val) => {
  if (val) incomeStore.setOpenRow(rowKey());
});

watch(
  () => incomeStore.openRowId,
  (id) => {
    if (isOpen.value && id !== rowKey()) close();
  },
);

function monthLabel(year, month) {
  return new Date(year, month - 1, 1).toLocaleString("en", { month: "long" }) + " " + year;
}

function onRowClick() {
  if (shouldFireTap()) onEditTap();
}

function onEditTap() {
  if (!isOnline.value) {
    toast.show("Not available offline", "info");
    return;
  }
  emit("tap");
}

function onEditClick(e) {
  e.stopPropagation();
  close();
  onEditTap();
}
</script>

<template>
  <div class="row-wrap" data-testid="income-row">
    <div class="row-panel" :style="{ pointerEvents: isOpen ? 'auto' : 'none' }">
      <button
        type="button"
        class="panel-btn"
        :class="{ 'panel-btn--commit': isCommit, 'panel-btn--offline': !isOnline }"
        aria-label="Edit income"
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
      <div class="row-top">
        <span class="row-month">{{ monthLabel(income.year, income.month) }}</span>
        <span class="row-amount">
          <span class="row-amount-num">+{{ income.amount.toFixed(2) }}</span>
          <span class="row-amount-cur"> {{ income.currency }}</span>
        </span>
      </div>
      <div class="row-bottom">
        <span class="row-key">{{ income.year }}-{{ String(income.month).padStart(2, "0") }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.row-wrap {
  position: relative;
  overflow: hidden;
  background-color: var(--bg);
  border-radius: 0 10px 10px 0;
  border: 1px solid var(--border);
  border-left: 4px solid var(--success);
  margin-bottom: 0.5rem;
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
  background: var(--success);
  border: none;
  color: #04140a;
  font-size: 0.72rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s;
}

.panel-btn--offline {
  background: var(--surface-2);
  color: var(--muted);
}

.panel-btn--commit {
  filter: brightness(1.1);
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

.row-top {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 3px;
}

.row-month {
  font-size: 0.9375rem;
  font-weight: 600;
  color: var(--text);
}

.row-amount {
  font-family: var(--font-num);
  font-size: 0.9rem;
}

.row-amount-num {
  color: var(--success);
  font-weight: 600;
}

.row-amount-cur {
  color: var(--muted);
  font-size: 0.78rem;
}

.row-bottom {
  display: flex;
  align-items: center;
}

.row-key {
  font-family: var(--font-num);
  font-size: 0.72rem;
  color: var(--muted);
}
</style>

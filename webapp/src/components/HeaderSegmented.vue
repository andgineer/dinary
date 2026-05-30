<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { Plus, ListChecks, Cpu, TrendingUp, MoreHorizontal } from "lucide-vue-next";

const RARE_TABS = [
  { id: "income", label: "Income", icon: TrendingUp },
  { id: "llm", label: "LLM providers", icon: Cpu },
];

const props = defineProps({
  tab: { type: String, default: "add" },
  showBadge: { type: Boolean, default: false },
});
const emit = defineEmits(["update:tab"]);

const menuOpen = ref(false);
const overflowRef = ref(null);

const isRareTab = computed(() => RARE_TABS.some((t) => t.id === props.tab));

function toggleMenu() {
  menuOpen.value = !menuOpen.value;
}

function selectRareTab(id) {
  emit("update:tab", id);
  menuOpen.value = false;
}

function handleKeydown(e) {
  if (e.key === "Escape") menuOpen.value = false;
}

function handleOutsidePointer(e) {
  if (!menuOpen.value || !overflowRef.value) return;
  if (!overflowRef.value.contains(e.target)) menuOpen.value = false;
}

onMounted(() => {
  document.addEventListener("keydown", handleKeydown);
  document.addEventListener("pointerdown", handleOutsidePointer);
});

onBeforeUnmount(() => {
  document.removeEventListener("keydown", handleKeydown);
  document.removeEventListener("pointerdown", handleOutsidePointer);
});
</script>

<template>
  <div class="seg-container" role="tablist" aria-label="Navigation">
    <button
      type="button"
      class="seg-btn seg-add"
      :class="{ active: tab === 'add' }"
      role="tab"
      :aria-selected="tab === 'add'"
      aria-label="Add expense"
      data-testid="seg-add"
      @click="$emit('update:tab', 'add')"
    >
      <Plus :size="22" aria-hidden="true" />
    </button>

    <button
      type="button"
      class="seg-btn seg-review"
      :class="{ active: tab === 'review' }"
      role="tab"
      :aria-selected="tab === 'review'"
      aria-label="Review"
      data-testid="seg-review"
      @click="$emit('update:tab', 'review')"
    >
      <ListChecks :size="22" aria-hidden="true" />
      <span
        v-if="showBadge"
        class="seg-badge"
        aria-label="review attention"
      >!</span>
    </button>

    <div ref="overflowRef" class="seg-overflow">
      <button
        type="button"
        class="seg-btn seg-more"
        :class="{ active: isRareTab }"
        aria-label="More tabs"
        aria-haspopup="menu"
        :aria-expanded="menuOpen ? 'true' : 'false'"
        data-testid="seg-more"
        @click="toggleMenu"
      >
        <MoreHorizontal :size="16" aria-hidden="true" />
      </button>

      <div
        v-if="menuOpen"
        class="overflow-menu"
        role="menu"
        data-testid="overflow-menu"
      >
        <button
          v-for="item in RARE_TABS"
          :key="item.id"
          type="button"
          class="overflow-item"
          :class="{ active: tab === item.id }"
          role="menuitem"
          :data-testid="`menu-${item.id}`"
          @click="selectRareTab(item.id)"
        >
          <component :is="item.icon" :size="22" aria-hidden="true" />
          {{ item.label }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.seg-container {
  display: flex;
  align-items: center;
  gap: 2px;
  background: var(--field-deep);
  border: 1px solid var(--border);
  border-radius: 11px;
  padding: 3px;
}

.seg-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, box-shadow 0.15s;
  color: var(--muted);
  background: transparent;
  padding: 0;
  width: auto;
}

.seg-btn:active {
  transform: scale(0.95);
}

/* Add — amber/orange */
.seg-add {
  width: 56px;
  height: 38px;
  background: rgba(249, 115, 22, 0.12);
  color: var(--expense);
}

.seg-add.active {
  background: var(--expense);
  color: #fff;
  box-shadow: 0 4px 12px rgba(249, 115, 22, 0.4);
}

/* Review — sky blue, same size class as Add */
.seg-review {
  width: 56px;
  height: 38px;
  background: rgba(96, 165, 250, 0.12);
  color: #60a5fa;
}

.seg-review.active {
  background: #60a5fa;
  color: #fff;
  box-shadow: 0 4px 12px rgba(96, 165, 250, 0.35);
}

/* Overflow ··· */
.seg-overflow {
  position: relative;
}

.seg-more {
  width: 36px;
  height: 30px;
}

.seg-more.active {
  background: var(--accent);
  color: #fff;
}

/* Dropdown */
.overflow-menu {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: 14px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.45), 0 3px 8px rgba(0, 0, 0, 0.25);
  min-width: 200px;
  overflow: hidden;
  z-index: 20;
}

.overflow-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  width: 100%;
  min-height: 52px;
  padding: 0.85rem 1.1rem;
  background: transparent;
  border: none;
  border-radius: 0;
  color: var(--text-muted);
  font-size: 1rem;
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
  text-align: left;
}

.overflow-item + .overflow-item {
  border-top: 1px solid var(--border);
}

.overflow-item:hover {
  background: var(--field);
  color: var(--text);
}

.overflow-item.active {
  background: var(--surface-2);
  color: var(--text);
  font-weight: 600;
}

.seg-badge {
  position: absolute;
  top: -4px;
  right: -4px;
  background: var(--warning);
  color: #000;
  font-size: 0.6rem;
  font-weight: 700;
  line-height: 1;
  padding: 2px 4px;
  border-radius: 999px;
  border: 2px solid var(--surface);
  min-width: 14px;
  text-align: center;
}
</style>

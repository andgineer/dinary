<script setup>
import { computed, ref, watch } from "vue";
import { Check, Sparkles, X } from "lucide-vue-next";
import { useCatalogStore } from "../stores/catalog.js";
import { useReviewStore } from "../stores/review.js";

const props = defineProps({
  open: { type: Boolean, default: false },
  item: { type: Object, default: null },
});
const emit = defineEmits(["close"]);

const catalog = useCatalogStore();
const reviewStore = useReviewStore();

const selectedCategoryId = ref(null);
const submitting = ref(false);

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      const raw = props.item?.current_category_id;
      selectedCategoryId.value = raw != null ? Number(raw) : null;
      submitting.value = false;
    }
  },
);

const allGroupsWithCategories = computed(() =>
  catalog.groups
    .map((g) => ({ group: g, categories: catalog.categories(g.id) }))
    .filter((gc) => gc.categories.length > 0),
);

const selectedCategory = computed(() =>
  selectedCategoryId.value ? catalog.findCategoryById(selectedCategoryId.value) : null,
);

const selectedGroup = computed(() => {
  const cat = selectedCategory.value;
  if (!cat) return null;
  return catalog.snapshot?.category_groups?.find((g) => g.id === cat.group_id) ?? null;
});

const footerText = computed(() => {
  if (!selectedCategory.value) return null;
  const count = props.item?.count ?? 1;
  const groupName = selectedGroup.value?.name ?? "";
  return `Updates ${count} expenses · sets ${groupName} › ${selectedCategory.value.name}`;
});

function selectCategory(id) {
  selectedCategoryId.value = Number(id);
}

function isSuggested(catId) {
  return (
    props.item?.suggested_category_id &&
    Number(catId) === Number(props.item.suggested_category_id) &&
    Number(catId) !== Number(selectedCategoryId.value)
  );
}

async function confirm() {
  if (!selectedCategoryId.value || !props.item) return;
  submitting.value = true;
  try {
    await reviewStore.correct(props.item, selectedCategoryId.value);
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
      aria-label="Correct category"
      data-testid="correction-sheet"
    >
      <div class="drag-handle" />

      <div class="sheet-header">
        <div class="sheet-eyebrow">CORRECT CATEGORY</div>
        <div v-if="item" class="sheet-title">{{ item.name }}</div>
        <div v-if="item" class="sheet-meta">
          {{ item.store }} · ×{{ item.count }} · {{ item.total?.toLocaleString("ru-RU") }} {{ item.currency }}
        </div>
        <button type="button" class="sheet-close" aria-label="Close" @click="emit('close')">
          <X :size="16" />
        </button>
      </div>

      <div v-if="item?.store" class="info-banner">
        Saves a rule for {{ item.store }} — future scans will auto-classify.
      </div>

      <div class="sheet-body">
        <div
          v-for="{ group, categories } in allGroupsWithCategories"
          :key="group.id"
          class="group-section"
        >
          <div class="group-label">{{ group.name }}</div>
          <div class="categories-grid">
            <button
              v-for="cat in categories"
              :key="cat.id"
              type="button"
              class="cat-btn"
              :class="{
                'is-selected': selectedCategoryId === cat.id,
                'is-suggested': isSuggested(cat.id),
              }"
              @click="selectCategory(cat.id)"
            >
              <Sparkles
                v-if="isSuggested(cat.id)"
                :size="10"
                class="suggest-icon"
                aria-hidden="true"
              />
              <Check
                v-if="selectedCategoryId === cat.id"
                :size="12"
                class="check-icon"
                aria-hidden="true"
              />
              {{ cat.name }}
            </button>
          </div>
        </div>
      </div>

      <div class="sheet-footer">
        <span v-if="footerText" class="footer-text">{{ footerText }}</span>
        <button
          type="button"
          class="btn btn-primary confirm-btn"
          :disabled="!selectedCategoryId || submitting"
          @click="confirm"
        >
          Confirm
        </button>
      </div>
    </div>
    </Transition>
  </Teleport>
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
  min-height: 50vh;
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
  margin-bottom: 0.25rem;
}

.sheet-title {
  font-size: 1rem;
  font-weight: 600;
  color: var(--text);
}

.sheet-meta {
  font-size: 0.78rem;
  color: var(--muted);
  margin-top: 2px;
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

.info-banner {
  margin: 0 1rem 0.5rem;
  padding: 0.5rem 0.75rem;
  background: rgba(91, 141, 239, 0.1);
  border: 1px solid rgba(91, 141, 239, 0.2);
  border-radius: 8px;
  font-size: 0.78rem;
  color: #7aabff;
  flex-shrink: 0;
}

.sheet-body {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem 1rem;
}

.group-section {
  margin-bottom: 1rem;
}

.group-label {
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 0.4rem;
}

.categories-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.4rem;
}

.cat-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0.45rem 0.6rem;
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 9px;
  color: var(--text);
  font-size: 0.82rem;
  cursor: pointer;
  width: 100%;
  text-align: left;
  transition: background 0.12s, border-color 0.12s;
}

.cat-btn.is-selected {
  border-color: var(--accent);
  background: rgba(91, 141, 239, 0.18);
}

.cat-btn.is-suggested:not(.is-selected) {
  border-color: rgba(91, 141, 239, 0.4);
}

.suggest-icon {
  color: #7aabff;
  flex-shrink: 0;
}

.check-icon {
  color: var(--accent);
  flex-shrink: 0;
}

.sheet-footer {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem 1rem calc(0.75rem + env(safe-area-inset-bottom, 0px));
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}

.footer-text {
  flex: 1;
  font-size: 0.78rem;
  color: var(--muted);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.confirm-btn {
  flex: 0 0 auto;
  width: auto;
  padding: 0.5rem 1.25rem;
  font-size: 0.9rem;
}
</style>

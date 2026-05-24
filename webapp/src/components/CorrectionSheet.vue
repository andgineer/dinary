<script setup>
import { computed, ref, watch } from "vue";
import { Check, Sparkles } from "lucide-vue-next";
import { useCatalogStore } from "../stores/catalog.js";
import { useReviewStore } from "../stores/review.js";
import BaseSheet from "./BaseSheet.vue";
import ScopeSelector from "./ScopeSelector.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  item: { type: Object, default: null },
});
const emit = defineEmits(["close"]);

const catalog = useCatalogStore();
const reviewStore = useReviewStore();

const selectedCategoryId = ref(null);
const selectedScope = ref("single");
const submitting = ref(false);

const SCOPE_OPTIONS = [
  { value: "single", label: "Last expense" },
  { value: "month", label: "Last month" },
  { value: "year", label: "This year" },
  { value: "all", label: "All history" },
];

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      const raw = props.item?.category_id;
      selectedCategoryId.value = raw != null ? Number(raw) : null;
      selectedScope.value = "single";
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
  const noun = count === 1 ? "occurrence" : "occurrences";
  return `Updates ${count} ${noun} · sets ${groupName} › ${selectedCategory.value.name}`;
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
    const scope = props.item?.is_doubtful ? "all" : selectedScope.value;
    await reviewStore.correct(props.item, selectedCategoryId.value, scope);
    emit("close");
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <BaseSheet
    :open="open"
    :tall="true"
    aria-label="Correct category"
    data-testid="correction-sheet"
    @close="emit('close')"
  >
    <template #header>
      <div class="sheet-eyebrow">CORRECT CATEGORY</div>
      <div v-if="item" class="sheet-title">{{ item.name ?? item.store }}</div>
      <div v-if="item" class="sheet-meta">
        <template v-if="item.store">{{ item.store }}</template
        ><template v-if="item.count"> · ×{{ item.count }}</template>
        · {{ item.total?.toLocaleString("ru-RU") }} {{ item.currency }}
      </div>
    </template>

    <template #pre-body>
      <div v-if="item?.store" class="info-banner">
        Saves a rule for {{ item.store }} — future scans will auto-classify.
      </div>
    </template>

    <div v-if="!item?.is_doubtful" class="scope-selector" data-testid="scope-selector">
      <div class="scope-label">Apply change to:</div>
      <ScopeSelector v-model="selectedScope" :options="SCOPE_OPTIONS" />
    </div>

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

    <template #footer>
      <span v-if="footerText" class="footer-text">{{ footerText }}</span>
      <button
        type="button"
        class="btn btn-primary confirm-btn"
        :disabled="!selectedCategoryId || submitting"
        @click="confirm"
      >
        Confirm
      </button>
    </template>
  </BaseSheet>
</template>

<style scoped>
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

.scope-selector {
  margin-bottom: 1rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid var(--border);
}

.scope-label {
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 0.5rem;
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

<script setup>
import { computed } from "vue";
import { Sparkles } from "lucide-vue-next";
import { useCatalogStore } from "../stores/catalog.js";

const props = defineProps({
  item: { type: Object, required: true },
});
defineEmits(["tap"]);

const catalog = useCatalogStore();

const categoryId = computed(() => props.item.category_id);
const currentCategory = computed(() => catalog.findCategoryById(categoryId.value));
const currentGroup = computed(() => {
  const cat = currentCategory.value;
  if (!cat) return null;
  return catalog.snapshot?.category_groups?.find((g) => g.id === cat.group_id) ?? null;
});

const suggestedCategory = computed(() => catalog.findCategoryById(props.item.suggested_category_id));
const suggestedGroup = computed(() => {
  const cat = suggestedCategory.value;
  if (!cat) return null;
  return catalog.snapshot?.category_groups?.find((g) => g.id === cat.group_id) ?? null;
});
const hasSuggestion = computed(
  () =>
    props.item.suggested_category_id &&
    Number(props.item.suggested_category_id) !== Number(categoryId.value),
);

const CONFIDENCE_LABELS = { 1: "no match", 2: "guess", 3: "maybe" };
const CONFIDENCE_TONES = { 1: "danger", 2: "warn", 3: "warn" };
const confidenceLabel = computed(() => CONFIDENCE_LABELS[props.item.confidence_level] ?? "?");
const confidenceTone = computed(() => CONFIDENCE_TONES[props.item.confidence_level] ?? "warn");

const formattedDate = computed(() => {
  if (!props.item.datetime) return "";
  const d = new Date(props.item.datetime);
  return `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}`;
});

function formatAmount(total) {
  return total != null ? total.toLocaleString("ru-RU") : "";
}
</script>

<template>
  <div
    class="rule-row"
    :class="{ 'rule-row--doubtful': item.is_doubtful }"
    role="button"
    tabindex="0"
    :data-testid="item.is_doubtful ? 'doubtful-row' : 'certain-row'"
    @click="$emit('tap')"
    @keydown.enter="$emit('tap')"
  >
    <div class="row-top">
      <span class="row-name">{{ item.name ?? item.store }}</span>
      <span class="row-total">{{ formatAmount(item.total) }}</span>
    </div>
    <div class="row-sub">
      <span v-if="item.store && item.name" class="row-store">{{ item.store }}</span>
      <span v-if="item.count && item.count > 1" class="row-count">×{{ item.count }}</span>
      <span v-if="formattedDate" class="row-date">{{ formattedDate }}</span>
      <span class="row-currency">{{ item.currency }}</span>
    </div>
    <div class="row-bottom">
      <template v-if="item.is_doubtful">
        <span class="confidence-pill" :class="`pill-${confidenceTone}`">{{ confidenceLabel }}</span>
        <span v-if="currentCategory" class="row-category">
          {{ currentGroup?.name }} › {{ currentCategory.name }}
        </span>
        <template v-if="hasSuggestion && suggestedCategory">
          <span class="row-arrow">→</span>
          <span class="suggested-pill">
            <Sparkles :size="10" aria-hidden="true" />
            {{ suggestedGroup?.name }} › {{ suggestedCategory.name }}
          </span>
        </template>
      </template>
      <template v-else>
        <span v-if="currentCategory" class="row-category">
          {{ currentGroup?.name }} › {{ currentCategory.name }}
        </span>
        <span v-else-if="item.category_name" class="row-category">{{ item.category_name }}</span>
      </template>
    </div>
  </div>
</template>

<style scoped>
.rule-row {
  background: var(--field);
  border-radius: 10px;
  border: 1px solid var(--border);
  padding: 0.625rem 0.75rem;
  margin-bottom: 0.5rem;
  cursor: pointer;
  transition: opacity 0.15s;
}

.rule-row--doubtful {
  border-left: 4px solid var(--warning);
  border-radius: 0 10px 10px 0;
  background: rgba(245, 158, 11, 0.07);
}

.rule-row:active {
  opacity: 0.85;
}

.row-top {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 2px;
}

.row-name {
  font-weight: 600;
  font-size: 0.9375rem;
  color: var(--text);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-right: 0.5rem;
}

.rule-row--doubtful .row-name {
  font-weight: 700;
}

.row-total {
  font-family: var(--font-num);
  font-size: 0.9375rem;
  font-weight: 500;
  color: var(--text);
  white-space: nowrap;
}

.row-sub {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.8rem;
  color: var(--muted);
  margin-bottom: 0.4rem;
}

.row-store {
  color: var(--muted);
}

.row-count {
  color: var(--muted);
}

.row-date {
  color: var(--muted);
}

.row-currency {
  margin-left: auto;
  font-family: var(--font-num);
  font-size: 0.75rem;
  color: var(--muted);
}

.row-bottom {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
}

.confidence-pill {
  font-size: 0.68rem;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 999px;
  text-transform: lowercase;
}

.pill-danger {
  background: rgba(239, 68, 68, 0.15);
  color: var(--error);
  border: 1px solid rgba(239, 68, 68, 0.3);
}

.pill-warn {
  background: rgba(245, 158, 11, 0.15);
  color: var(--warning);
  border: 1px solid rgba(245, 158, 11, 0.3);
}

.row-category {
  font-size: 0.78rem;
  color: var(--muted);
}

.row-arrow {
  font-size: 0.75rem;
  color: var(--muted-2);
}

.suggested-pill {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 0.78rem;
  font-weight: 500;
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(91, 141, 239, 0.12);
  color: #7aabff;
  border: 1px solid rgba(91, 141, 239, 0.2);
}
</style>

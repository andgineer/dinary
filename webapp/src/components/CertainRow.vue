<script setup>
import { computed } from "vue";
import { useCatalogStore } from "../stores/catalog.js";

const props = defineProps({
  item: { type: Object, required: true },
});
defineEmits(["tap"]);

const catalog = useCatalogStore();

const dominantGroupName = computed(() => {
  if (!props.item.top_categories?.length) return null;
  const topCatId = props.item.top_categories[0].id;
  const cat = catalog.findCategoryById(topCatId);
  if (!cat) return null;
  const group = catalog.snapshot?.category_groups?.find((g) => g.id === cat.group_id);
  return group?.name ?? cat.name;
});

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
    class="certain-row"
    role="button"
    tabindex="0"
    data-testid="certain-row"
    @click="$emit('tap')"
    @keydown.enter="$emit('tap')"
  >
    <div class="row-top">
      <span class="row-store">{{ item.store }}</span>
      <span class="row-total">{{ formatAmount(item.total) }}</span>
    </div>
    <div class="row-sub">
      <span v-if="formattedDate" class="row-date">{{ formattedDate }}</span>
      <span v-if="item.items_count" class="row-items">· {{ item.items_count }} items</span>
      <span v-if="dominantGroupName" class="row-cat">· {{ dominantGroupName }}</span>
      <span class="row-currency">{{ item.currency }}</span>
    </div>
  </div>
</template>

<style scoped>
.certain-row {
  background: var(--field);
  border-radius: 10px;
  border: 1px solid var(--border);
  padding: 0.5rem 0.75rem;
  margin-bottom: 0.4rem;
  cursor: pointer;
  transition: opacity 0.15s;
}

.certain-row:active {
  opacity: 0.85;
}

.row-top {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.row-store {
  font-weight: 500;
  font-size: 0.9rem;
  color: var(--text);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-right: 0.5rem;
}

.row-total {
  font-family: var(--font-num);
  font-size: 0.9rem;
  color: var(--text);
  white-space: nowrap;
}

.row-sub {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.78rem;
  color: var(--muted);
  margin-top: 2px;
}

.row-currency {
  margin-left: auto;
  font-family: var(--font-num);
  font-size: 0.72rem;
  color: var(--muted);
}
</style>

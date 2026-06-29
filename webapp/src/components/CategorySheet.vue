<script setup>
import { computed, ref, watch, nextTick } from "vue";
import {
  Check,
  ChevronRight,
  EyeOff,
  Layers,
  Pencil,
  Plus,
  Settings,
  Sparkles,
  X,
} from "lucide-vue-next";
import { useCatalogStore } from "../stores/catalog.js";
import { useToastStore } from "../stores/toast.js";
import { recordOutOfSetActivation } from "../composables/oosNudge.js";
import BaseSheet from "./BaseSheet.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  suggestions: { type: Array, default: () => [] },
  title: { type: String, default: "Select category" },
  initialManage: { type: Boolean, default: false },
});
const emit = defineEmits(["select", "close"]);

const catalog = useCatalogStore();
const toast = useToastStore();

const searchEl = ref(null);
const bodyEl = ref(null);
const query = ref("");
const searchResults = ref([]);
const activatingCode = ref(null);
const manageMode = ref(false);

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      query.value = "";
      searchResults.value = [];
      manageMode.value = props.initialManage;
      void catalog.loadIfNeeded();
      catalog.ensureTemplateCatalog().catch((e) => {
        toast.show(e?.message || "Failed to load category sets", "error");
      });
      nextTick(() => {
        if (bodyEl.value) bodyEl.value.scrollTop = 0;
        searchEl.value?.focus({ preventScroll: true });
      });
    }
  },
  { immediate: true },
);

watch(query, (q) => {
  const trimmed = q.trim();
  searchResults.value = trimmed ? catalog.searchCategories(trimmed) : [];
});

const groupedCategories = computed(() => {
  const groups = new Map();
  for (const cat of catalog.visibleCategories) {
    let entry = groups.get(cat.group_id);
    if (!entry) {
      entry = {
        groupId: cat.group_id,
        groupCode: cat.group_code,
        groupName: cat.group_name,
        groupSortOrder: cat.group_sort_order,
        categories: [],
      };
      groups.set(cat.group_id, entry);
    }
    entry.categories.push(cat);
  }
  return [...groups.values()].sort((a, b) => a.groupSortOrder - b.groupSortOrder);
});

const showSearch = computed(() => query.value.trim().length > 0);

const inSetResults = computed(() =>
  searchResults.value
    .filter((item) => item.is_active && !item.is_hidden)
    .map((item) => ({
      id: item.id,
      groupName: catalog.visibleCategoryByCode(item.code)?.group_name ?? null,
      name: item.name,
    })),
);

const addableResults = computed(() =>
  searchResults.value.filter((item) => !item.is_active || item.is_hidden),
);

function select(id) {
  emit("select", id);
  emit("close");
}

async function selectAddable(item) {
  if (activatingCode.value) return;
  if (!navigator.onLine) {
    toast.show("Not available offline", "error");
    return;
  }
  activatingCode.value = item.code;
  try {
    if (item.is_hidden) {
      await catalog.unhideCategory(item.code);
    } else {
      await catalog.activateCategory(item.code);
    }
    window.dispatchEvent(new Event("online"));
    const nudged = recordOutOfSetActivation();
    if (!nudged) {
      toast.show(`"${item.name}" added to your set`, "info");
    }
    item.is_active = true;
    item.is_hidden = false;
    const visible = catalog.visibleCategoryByCode(item.code);
    if (visible) {
      select(item.id);
    }
  } catch (e) {
    toast.show(e?.message || "Couldn't enable category", "error");
  } finally {
    activatingCode.value = null;
  }
}

function onKeydown(e) {
  if (e.key === "Escape") {
    if (query.value) {
      query.value = "";
    } else {
      emit("close");
    }
  }
}

// ----- Manage mode --------------------------------------------------------

const editingCode = ref(null);
const editingName = ref("");
const addingGroupCode = ref(null);
const addingName = ref("");
const busyCode = ref(null);

function toggleManage() {
  manageMode.value = !manageMode.value;
  if (!manageMode.value) return;
  editingCode.value = null;
  addingGroupCode.value = null;
}

async function hideCategoryRow(cat) {
  if (busyCode.value) return;
  busyCode.value = cat.code;
  try {
    await catalog.hideCategory(cat.code);
  } catch (e) {
    toast.show(e?.message || "Failed to hide category", "error");
  } finally {
    busyCode.value = null;
  }
}

function startRename(cat) {
  editingCode.value = cat.code;
  editingName.value = cat.name;
}

function cancelRename() {
  editingCode.value = null;
  editingName.value = "";
}

async function confirmRename(cat) {
  const trimmed = editingName.value.trim();
  if (!trimmed || trimmed === cat.name) {
    cancelRename();
    return;
  }
  busyCode.value = cat.code;
  try {
    await catalog.renameCategory(cat.code, trimmed);
    cancelRename();
  } catch (e) {
    toast.show(e?.message || "Failed to rename category", "error");
  } finally {
    busyCode.value = null;
  }
}

async function moveCategoryRow(cat, groupCode) {
  if (!groupCode || groupCode === cat.group_code) return;
  busyCode.value = cat.code;
  try {
    await catalog.moveCategory(cat.code, groupCode);
  } catch (e) {
    toast.show(e?.message || "Failed to move category", "error");
  } finally {
    busyCode.value = null;
  }
}

function startAdd(groupCode) {
  addingGroupCode.value = groupCode;
  addingName.value = "";
}

function cancelAdd() {
  addingGroupCode.value = null;
  addingName.value = "";
}

async function confirmAdd(groupCode) {
  const trimmed = addingName.value.trim();
  if (!trimmed) {
    cancelAdd();
    return;
  }
  busyCode.value = `__add__${groupCode}`;
  try {
    await catalog.createCategory(trimmed, groupCode);
    cancelAdd();
  } catch (e) {
    toast.show(e?.message || "Failed to add category", "error");
  } finally {
    busyCode.value = null;
  }
}
</script>

<template>
  <BaseSheet
    :open="open"
    :full-height="true"
    :z-index="50"
    :aria-label="title"
    data-testid="category-sheet"
    @close="emit('close')"
  >
    <template #header>
      <div class="sheet-eyebrow">{{ title }}</div>
    </template>

    <template #pre-body>
      <div class="search-wrap">
        <div class="search-input-wrap">
          <input
            ref="searchEl"
            v-model="query"
            type="search"
            placeholder="Search…"
            class="search-input"
            aria-label="Search categories"
            @keydown="onKeydown"
          />
          <button
            v-if="query"
            type="button"
            class="clear-btn"
            aria-label="Clear search"
            @click="query = ''"
          >
            <X :size="14" />
          </button>
        </div>
      </div>
    </template>

    <div ref="bodyEl">
      <template v-if="manageMode">
        <div class="manage-view" data-testid="manage-view">
          <div
            v-for="g in groupedCategories"
            :key="g.groupId"
            class="manage-group"
            data-testid="manage-group"
          >
            <div class="group-label">{{ g.groupName }}</div>

            <div
              v-for="cat in g.categories"
              :key="cat.code"
              class="manage-row"
              data-testid="manage-category-row"
            >
              <template v-if="editingCode === cat.code">
                <input
                  v-model="editingName"
                  type="text"
                  class="manage-rename-input"
                  :aria-label="`Rename ${cat.name}`"
                  @keydown.enter="confirmRename(cat)"
                  @keydown.escape="cancelRename"
                />
                <button
                  type="button"
                  class="manage-icon-btn"
                  :aria-label="`Save ${cat.name}`"
                  :disabled="busyCode === cat.code"
                  @click="confirmRename(cat)"
                >
                  <Check :size="14" aria-hidden="true" />
                </button>
                <button
                  type="button"
                  class="manage-icon-btn"
                  aria-label="Cancel rename"
                  @click="cancelRename"
                >
                  <X :size="14" aria-hidden="true" />
                </button>
              </template>
              <template v-else>
                <span class="manage-name">{{ cat.name }}</span>
                <select
                  class="manage-move-select"
                  :aria-label="`Move ${cat.name} to group`"
                  :value="cat.group_code"
                  :disabled="busyCode === cat.code"
                  @change="moveCategoryRow(cat, $event.target.value)"
                >
                  <option v-for="grp in groupedCategories" :key="grp.groupCode" :value="grp.groupCode">
                    {{ grp.groupName }}
                  </option>
                </select>
                <button
                  type="button"
                  class="manage-icon-btn"
                  :aria-label="`Rename ${cat.name}`"
                  :disabled="busyCode === cat.code"
                  @click="startRename(cat)"
                >
                  <Pencil :size="14" aria-hidden="true" />
                </button>
                <button
                  type="button"
                  class="manage-icon-btn"
                  :aria-label="`Hide ${cat.name}`"
                  :disabled="busyCode === cat.code"
                  @click="hideCategoryRow(cat)"
                >
                  <EyeOff :size="14" aria-hidden="true" />
                </button>
              </template>
            </div>

            <div v-if="addingGroupCode === g.groupCode" class="manage-row manage-add-row">
              <input
                v-model="addingName"
                type="text"
                class="manage-add-input"
                :aria-label="`New category in ${g.groupName}`"
                placeholder="Category name"
                @keydown.enter="confirmAdd(g.groupCode)"
                @keydown.escape="cancelAdd"
              />
              <button
                type="button"
                class="manage-icon-btn"
                :aria-label="`Save new category in ${g.groupName}`"
                :disabled="busyCode === `__add__${g.groupCode}`"
                @click="confirmAdd(g.groupCode)"
              >
                <Check :size="14" aria-hidden="true" />
              </button>
              <button
                type="button"
                class="manage-icon-btn"
                aria-label="Cancel add category"
                @click="cancelAdd"
              >
                <X :size="14" aria-hidden="true" />
              </button>
            </div>
            <button v-else type="button" class="manage-add-btn" @click="startAdd(g.groupCode)">
              <Plus :size="14" aria-hidden="true" />
              <span>Add category</span>
            </button>
          </div>
        </div>
      </template>

      <template v-else-if="showSearch">
        <div class="flat-list" data-testid="flat-results">
          <button
            v-for="item in inSetResults"
            :key="item.id"
            type="button"
            class="flat-item"
            @click="select(item.id)"
          >
            <span class="flat-group">{{ item.groupName }}</span>
            <span class="flat-sep"> › </span>
            <span>{{ item.name }}</span>
          </button>

          <div v-if="addableResults.length > 0" class="addable-section" data-testid="addable-section">
            <div class="addable-eyebrow">
              <Layers :size="12" aria-hidden="true" />
              <span>Not in your set</span>
              <span class="addable-sub">· add with one tap</span>
            </div>
            <button
              v-for="item in addableResults"
              :key="item.code"
              type="button"
              class="addable-item"
              :disabled="activatingCode === item.code"
              @click="selectAddable(item)"
            >
              <span class="addable-name">{{ item.name }}</span>
              <span v-if="item.is_hidden" class="hidden-tag">
                <EyeOff :size="10" aria-hidden="true" />
                hidden
              </span>
              <span class="add-icon">
                <Check v-if="activatingCode === item.code" :size="14" aria-hidden="true" />
                <Plus v-else :size="14" aria-hidden="true" />
              </span>
            </button>
          </div>

          <div v-if="inSetResults.length === 0 && addableResults.length === 0" class="no-results">
            No matches
          </div>
        </div>
      </template>

      <template v-else>
        <template v-if="suggestions.length > 0">
          <div class="section-label">SUGGESTIONS</div>
          <div class="suggestions-row" data-testid="suggestion-pills">
            <button
              v-for="sug in suggestions"
              :key="sug.id"
              type="button"
              class="cat-btn is-suggested"
              @click="select(sug.id)"
            >
              <Sparkles :size="10" class="suggest-icon" aria-hidden="true" />
              {{ sug.name }}
            </button>
          </div>
        </template>

        <div
          v-for="g in groupedCategories"
          :key="g.groupId"
          class="group-section"
          data-testid="category-group"
        >
          <div class="group-label">{{ g.groupName }}</div>
          <div class="categories-grid">
            <button
              v-for="cat in g.categories"
              :key="cat.id"
              type="button"
              class="cat-btn"
              @click="select(cat.id)"
            >
              {{ cat.name }}
            </button>
          </div>
        </div>
      </template>
    </div>

    <template #footer>
      <button
        type="button"
        class="set-switch-btn"
        data-testid="open-template-switch"
        @click="catalog.openTemplateSwitch()"
      >
        <Layers :size="14" aria-hidden="true" />
        <span>Category set: {{ catalog.activeTemplateName }}</span>
        <ChevronRight :size="14" aria-hidden="true" />
      </button>
      <button
        type="button"
        class="manage-toggle-btn"
        data-testid="manage-toggle"
        :aria-label="manageMode ? 'Close manage' : 'Manage categories'"
        @click="toggleManage"
      >
        <X v-if="manageMode" :size="16" aria-hidden="true" />
        <Settings v-else :size="16" aria-hidden="true" />
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
}

.search-wrap {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--surface);
  margin: 0 1rem 0.5rem;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.search-input-wrap {
  position: relative;
  flex: 1;
}

.search-input {
  width: 100%;
  padding: 0.5rem 2rem 0.5rem 0.75rem;
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text);
  font-size: 0.9rem;
}

.manage-toggle-btn {
  flex-shrink: 0;
  width: 2.1rem;
  height: 2.1rem;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--muted);
  cursor: pointer;
}

.set-switch-btn {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.55rem 0.75rem;
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text);
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
  overflow: hidden;
}

.set-switch-btn span {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
}

.clear-btn {
  position: absolute;
  right: 0.5rem;
  top: 50%;
  transform: translateY(-50%);
  background: none;
  border: none;
  color: var(--muted);
  cursor: pointer;
  padding: 0.25rem;
  width: auto;
  display: flex;
  align-items: center;
}

.section-label {
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 0.4rem;
}

.suggestions-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-bottom: 1rem;
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

.cat-btn.is-suggested {
  border-color: rgba(91, 141, 239, 0.4);
}

.suggest-icon {
  color: #7aabff;
  flex-shrink: 0;
}

.flat-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.flat-item {
  display: flex;
  align-items: center;
  padding: 0.5rem 0.5rem;
  background: none;
  border: none;
  border-radius: 8px;
  color: var(--text);
  font-size: 0.88rem;
  cursor: pointer;
  text-align: left;
  width: 100%;
  transition: background 0.1s;
}

.flat-item:hover {
  background: var(--field);
}

.flat-group {
  color: var(--muted);
  font-size: 0.82rem;
}

.flat-sep {
  color: var(--muted-2);
  margin: 0 2px;
}

.no-results {
  color: var(--muted);
  font-size: 0.85rem;
  padding: 1rem 0;
  text-align: center;
}

.addable-section {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  margin-top: 0.5rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--border);
}

.addable-eyebrow {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 0.62rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: #7aabff;
}

.addable-sub {
  text-transform: none;
  font-weight: 400;
  letter-spacing: normal;
  color: var(--muted-2);
}

.addable-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem;
  background: rgba(91, 141, 239, 0.12);
  border: 1px solid rgba(91, 141, 239, 0.4);
  border-radius: 9px;
  color: var(--text);
  font-size: 0.88rem;
  opacity: 0.92;
  cursor: pointer;
  text-align: left;
  width: 100%;
}

.addable-item:disabled {
  cursor: default;
}

.addable-name {
  flex: 1;
}

.hidden-tag {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 0.1rem 0.35rem;
  background: rgba(148, 163, 184, 0.1);
  border-radius: 999px;
  color: var(--muted);
  font-size: 0.64rem;
  font-weight: 600;
  flex-shrink: 0;
}

.add-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 999px;
  background: rgba(91, 141, 239, 0.2);
  color: #7aabff;
  flex-shrink: 0;
}

.manage-view {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.manage-group {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.manage-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.35rem 0.1rem;
}

.manage-name {
  flex: 1;
  font-size: 0.88rem;
  color: var(--text);
  overflow-wrap: anywhere;
}

.manage-move-select {
  flex-shrink: 0;
  max-width: 40%;
  padding: 0.3rem 0.4rem;
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 7px;
  color: var(--text);
  font-size: 0.78rem;
}

.manage-rename-input,
.manage-add-input {
  flex: 1;
  padding: 0.3rem 0.5rem;
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 7px;
  color: var(--text);
  font-size: 0.85rem;
}

.manage-icon-btn {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1.8rem;
  height: 1.8rem;
  background: none;
  border: 1px solid var(--border);
  border-radius: 7px;
  color: var(--muted);
  cursor: pointer;
}

.manage-icon-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

.manage-add-btn {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.4rem 0.6rem;
  margin-top: 0.2rem;
  background: none;
  border: 1px dashed var(--border);
  border-radius: 7px;
  color: var(--muted);
  font-size: 0.8rem;
  cursor: pointer;
  width: fit-content;
}

.manage-add-row {
  margin-top: 0.2rem;
}
</style>

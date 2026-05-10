<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Calendar } from "lucide-vue-next";
import TagPicker from "./TagPicker.vue";
import ManageList from "./ManageList.vue";
import CatalogSelectField from "./CatalogSelectField.vue";
import CurrencyPicker from "./CurrencyPicker.vue";
import IconBtn from "./IconBtn.vue";
import EditModal from "../modals/EditModal.vue";
import { useCatalogStore } from "../stores/catalog.js";
import { useQueueStore } from "../stores/queue.js";
import { useToastStore } from "../stores/toast.js";
import { useCurrencyStore } from "../stores/currency.js";
import { flushQueue } from "../composables/flushQueue.js";
import { useCatalogManage } from "../composables/catalogManage.js";

const emit = defineEmits(["request-add"]);

const catalog = useCatalogStore();
const queue = useQueueStore();
const toast = useToastStore();
const currency = useCurrencyStore();
const {
  manageMode,
  pendingManageId,
  editModal,
  toggleManage,
  runCatalogAction,
  onEdit,
  closeEdit,
} = useCatalogManage();

const selectedCurrency = ref("");
const currencyPickerOpen = ref(false);

const DEFAULT_GROUP_NAME = "еда";
const DEFAULT_CATEGORY_NAME = "еда";

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

const amount = ref("");
const comment = ref("");
const date = ref(todayIso());
const groupId = ref("");
const categoryId = ref("");
const eventId = ref("");
const tagIds = ref([]);
const userEventOverride = ref(false);
const submitting = ref(false);
const justSavedFlash = ref(false);

const activeGroups = computed(() => catalog.groups);
const activeCategories = computed(() =>
  groupId.value ? catalog.categories(groupId.value) : [],
);
const activeEvents = computed(() => catalog.events(date.value || todayIso()));
const inactiveGroupsList = computed(() => catalog.inactiveGroups);
const inactiveCategoriesList = computed(() =>
  groupId.value ? catalog.inactiveCategories(groupId.value) : [],
);
const inactiveEventsList = computed(() =>
  catalog.inactiveEventsInWindow(date.value || todayIso()),
);
const allActiveTags = computed(() => catalog.tags);
const allInactiveTags = computed(() => catalog.inactiveTags);

const eventLabel = (e) => `${e.name} (${e.date_from}..${e.date_to})`;

function applyDefaultGroupAndCategory() {
  const group = catalog.findGroupByName(DEFAULT_GROUP_NAME);
  if (!group) return;
  if (!activeGroups.value.some((g) => g.id === group.id)) return;
  groupId.value = String(group.id);
  const cat = catalog.findCategoryByName(DEFAULT_CATEGORY_NAME, { groupId: group.id });
  if (cat && catalog.categories(group.id).some((c) => c.id === cat.id)) {
    categoryId.value = String(cat.id);
  }
}

function applyAutoAttachEventForDate() {
  if (userEventOverride.value) return;
  const matches = catalog.autoAttachEventsOn(date.value || todayIso());
  if (matches.length === 0) {
    eventId.value = "";
    return;
  }
  const pick = matches[0];
  if (activeEvents.value.some((e) => e.id === pick.id)) {
    eventId.value = String(pick.id);
  } else {
    eventId.value = "";
  }
}

watch(groupId, (gid) => {
  if (!gid) {
    categoryId.value = "";
    return;
  }
  if (categoryId.value) {
    const cat = catalog.findCategoryById(categoryId.value);
    if (!cat || cat.group_id !== Number(gid)) {
      categoryId.value = "";
    }
  }
});

watch(date, () => {
  if (eventId.value) {
    const stillThere = activeEvents.value.some((e) => String(e.id) === String(eventId.value));
    if (!stillThere) {
      eventId.value = "";
      userEventOverride.value = false;
      toast.show("Selected event is outside the date range — cleared", "info");
    }
  }
  applyAutoAttachEventForDate();
});

function onEventChanged() {
  userEventOverride.value = true;
}

function reset() {
  amount.value = "";
  comment.value = "";
  date.value = todayIso();
  eventId.value = "";
  tagIds.value = [];
  userEventOverride.value = false;
  applyDefaultGroupAndCategory();
  applyAutoAttachEventForDate();
}

async function init() {
  await catalog.load();
  if (catalog.lastError) {
    toast.show(`Catalog: ${catalog.lastError.message}`, "error");
  }
  applyDefaultGroupAndCategory();
  applyAutoAttachEventForDate();
  try {
    await currency.load();
  } catch (err) {
    toast.show(`Currencies: ${err?.message || err}`, "error");
  }
  if (!selectedCurrency.value) {
    selectedCurrency.value = currency.preferredCode;
  }
}

async function save() {
  const rawAmount = String(amount.value).replace(",", ".").trim();
  const parsedAmount = Number.parseFloat(rawAmount);
  if (!rawAmount || Number.isNaN(parsedAmount) || parsedAmount <= 0) {
    toast.show("Enter a valid amount", "error");
    return;
  }
  const cid = categoryId.value ? Number(categoryId.value) : null;
  if (!cid) {
    toast.show("Select a category", "error");
    return;
  }

  const code = selectedCurrency.value || currency.preferredCode || "RSD";
  const entry = {
    amount: parsedAmount,
    currency: code,
    category_id: cid,
    event_id: eventId.value ? Number(eventId.value) : null,
    tag_ids: tagIds.value.map(Number),
    category_name: catalog.findCategoryById(cid)?.name || "",
    comment: comment.value.trim(),
    date: date.value,
  };

  submitting.value = true;
  try {
    await queue.enqueue(entry);
  } catch (err) {
    toast.show(`Save failed: ${err?.message || err}`, "error");
    submitting.value = false;
    return;
  }
  justSavedFlash.value = true;
  setTimeout(() => {
    justSavedFlash.value = false;
    submitting.value = false;
    toast.show(
      `${parsedAmount} ${code}`,
      typeof navigator !== "undefined" && navigator.onLine ? "success" : "info",
    );
    reset();
    if (typeof navigator !== "undefined" && navigator.onLine) {
      void flushQueue();
    }
  }, 800);
}

function requestAdd(kind, options = {}) {
  emit("request-add", { kind, ...options });
}

function onReceiptParsed(ev) {
  const detail = ev?.detail;
  if (!detail) return;
  if (typeof detail.amount === "number" && !Number.isNaN(detail.amount)) {
    amount.value = String(detail.amount);
  }
  if (typeof detail.date === "string" && detail.date) {
    date.value = detail.date;
    applyAutoAttachEventForDate();
  }
}

function onOnline() {
  void flushQueue();
}

onMounted(() => {
  void init();
  window.addEventListener("dinary:receipt-parsed", onReceiptParsed);
  window.addEventListener("online", onOnline);
});

onBeforeUnmount(() => {
  window.removeEventListener("dinary:receipt-parsed", onReceiptParsed);
  window.removeEventListener("online", onOnline);
});

defineExpose({ save, reset });
</script>

<template>
  <div class="card" data-testid="expense-form">

    <!-- Hero amount row: currency pill + amount + date -->
    <div class="hero-row">
      <div class="hero-currency-wrap">
        <button
          type="button"
          class="currency-pill"
          :class="{ 'is-open': currencyPickerOpen }"
          aria-label="Select currency"
          data-testid="currency-pill"
          @click="currencyPickerOpen = !currencyPickerOpen"
        >
          {{ selectedCurrency || "RSD" }}
        </button>
        <div v-if="currencyPickerOpen" class="currency-picker-wrap">
          <CurrencyPicker v-model="selectedCurrency" />
        </div>
      </div>

      <input
        id="amount"
        v-model="amount"
        type="text"
        inputmode="decimal"
        placeholder="0"
        autocomplete="off"
        class="hero-amount"
        aria-label="Amount"
      />

      <div class="date-field">
        <Calendar :size="14" class="date-icon" aria-hidden="true" />
        <input id="date" v-model="date" type="date" class="date-input" aria-label="Date" />
      </div>
    </div>

    <!-- Group → Category hierarchy -->
    <div class="group-category-block">
      <CatalogSelectField
        kind="group"
        label="Group"
        v-model="groupId"
        :options="activeGroups"
        :inactive="inactiveGroupsList"
        :manage-open="manageMode.group"
        :pending-id="pendingManageId.group"
        @add="requestAdd('group')"
        @manage-toggle="toggleManage('group')"
        @deactivate="runCatalogAction('group', $event, 'deactivate')"
        @reactivate="runCatalogAction('group', $event, 'reactivate')"
        @delete="runCatalogAction('group', $event, 'remove')"
        @edit="onEdit('group', $event)"
      />

      <div class="category-connector">
        <div class="connector-line" />
        <div class="connector-indent">
          <CatalogSelectField
            kind="category"
            label="Category"
            v-model="categoryId"
            :options="activeCategories"
            :inactive="inactiveCategoriesList"
            :manage-open="manageMode.category"
            :pending-id="pendingManageId.category"
            :select-disabled="!groupId"
            placeholder="— select —"
            disabled-placeholder="— select group first —"
            :add-disabled="!groupId"
            :add-title="groupId ? 'New category' : 'Select a group first'"
            :form-hint="groupId ? '' : 'Select a group first'"
            @add="requestAdd('category', { groupId: groupId ? Number(groupId) : null })"
            @manage-toggle="toggleManage('category')"
            @deactivate="runCatalogAction('category', $event, 'deactivate')"
            @reactivate="runCatalogAction('category', $event, 'reactivate')"
            @delete="runCatalogAction('category', $event, 'remove')"
            @edit="onEdit('category', $event)"
          />
        </div>
      </div>
    </div>

    <CatalogSelectField
      kind="event"
      label="Event"
      v-model="eventId"
      :options="activeEvents"
      :inactive="inactiveEventsList"
      :manage-open="manageMode.event"
      :pending-id="pendingManageId.event"
      :placeholder="activeEvents.length === 0 ? '— no active events —' : '— no event —'"
      :manage-label-fn="eventLabel"
      @add="requestAdd('event')"
      @manage-toggle="toggleManage('event')"
      @select-change="onEventChanged"
      @deactivate="runCatalogAction('event', $event, 'deactivate')"
      @reactivate="runCatalogAction('event', $event, 'reactivate')"
      @delete="runCatalogAction('event', $event, 'remove')"
      @edit="onEdit('event', $event)"
    />

    <!-- Tags -->
    <div class="form-group tags-group">
      <div class="tags-header">
        <IconBtn icon="plus" tone="accent" label="New tag" @click="requestAdd('tag')" />
        <IconBtn
          :icon="manageMode.tag ? 'x' : 'cog'"
          tone="muted"
          :label="manageMode.tag ? 'Close tags' : 'Manage tags'"
          @click="toggleManage('tag')"
        />
      </div>
      <TagPicker
        v-model="tagIds"
        :tags="allActiveTags"
        empty-hint="No tags yet"
      />
      <ManageList
        v-if="manageMode.tag"
        kind="tag"
        :active="allActiveTags"
        :inactive="allInactiveTags"
        :pending-id="pendingManageId.tag"
        @deactivate="runCatalogAction('tag', $event, 'deactivate')"
        @reactivate="runCatalogAction('tag', $event, 'reactivate')"
        @delete="runCatalogAction('tag', $event, 'remove')"
        @edit="onEdit('tag', $event)"
      />
    </div>

    <div class="form-group">
      <textarea id="comment" v-model="comment" rows="2" placeholder="Note" aria-label="Comment" />
    </div>

    <EditModal
      :open="editModal.open"
      :kind="editModal.kind"
      :item="editModal.item"
      @close="closeEdit"
    />
  </div>
</template>

<style scoped>
/* Hero row: currency pill + amount input + date */
.hero-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 1rem;
  position: relative;
}

.hero-currency-wrap {
  position: relative;
  flex-shrink: 0;
}

.currency-pill {
  display: inline-flex;
  align-items: center;
  padding: 0.3rem 0.6rem;
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: 8px;
  font-size: 0.78rem;
  font-weight: 700;
  font-family: var(--font-num);
  letter-spacing: 0.04em;
  cursor: pointer;
  width: auto;
  margin-bottom: 0;
  white-space: nowrap;
}

.currency-pill.is-open {
  opacity: 0.85;
}

.currency-picker-wrap {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 20;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  padding: 0.6rem;
  min-width: 220px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
}

.hero-amount {
  flex: 1;
  height: 64px;
  font-size: 2rem;
  font-weight: 500;
  font-family: var(--font-num);
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--border);
  border-radius: 0;
  color: var(--text);
  padding: 0 0.25rem;
  text-align: right;
}

.hero-amount:focus {
  outline: none;
  border-bottom-color: var(--accent);
}

.date-field {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.date-icon {
  color: var(--muted);
}

.date-input {
  width: auto;
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--border);
  border-radius: 0;
  color: var(--muted);
  font-size: 0.8rem;
  padding: 0.2rem 0;
  min-width: 0;
}

.date-input:focus {
  outline: none;
  border-bottom-color: var(--accent);
}

/* Group → Category hierarchy */
.group-category-block {
  margin-bottom: 1rem;
}

.group-category-block .form-group {
  margin-bottom: 0;
}

.category-connector {
  display: flex;
  align-items: stretch;
  padding-left: 18px;
  margin-top: 4px;
}

.connector-line {
  width: 1px;
  background: var(--border-strong);
  margin-right: 12px;
  flex-shrink: 0;
  border-radius: 1px;
  align-self: stretch;
}

.connector-indent {
  flex: 1;
  min-width: 0;
}

/* Tags section */
.tags-group {
  margin-bottom: 1rem;
}

.tags-header {
  display: flex;
  justify-content: flex-end;
  gap: 4px;
  margin-bottom: 4px;
}
</style>

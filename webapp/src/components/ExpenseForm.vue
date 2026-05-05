<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import TagPicker from "./TagPicker.vue";
import ManageList from "./ManageList.vue";
import CatalogSelectField from "./CatalogSelectField.vue";
import CurrencyPicker from "./CurrencyPicker.vue";
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
  // Reset category when the operator switches groups, unless the
  // current category still belongs to the new group.
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
  // If the previously selected event left the dropdown window, clear it.
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
  // Currency list / rates load async; the picker shows the chip
  // skeleton immediately and fills in numbers once requests resolve.
  // Errors are surfaced via the store's lastListError.
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
    <div class="form-grid form-grid-amount-date">
      <div class="form-group">
        <label for="amount">Amount ({{ selectedCurrency || "RSD" }})</label>
        <input
          id="amount"
          v-model="amount"
          type="text"
          inputmode="decimal"
          placeholder="0"
          autocomplete="off"
        />
      </div>
      <div class="form-group">
        <label for="date">Date</label>
        <input id="date" v-model="date" type="date" />
      </div>
    </div>

    <div class="form-group">
      <label>Currency</label>
      <CurrencyPicker v-model="selectedCurrency" />
    </div>

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

    <div class="form-group">
      <label>
        Tags
        <button type="button" class="btn-inline" @click="requestAdd('tag')">+ New</button>
        <button type="button" class="btn-inline" @click="toggleManage('tag')">
          {{ manageMode.tag ? "Close" : "Manage" }}
        </button>
      </label>
      <TagPicker
        v-model="tagIds"
        :tags="allActiveTags"
        empty-hint="No tags yet — add one with + New"
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
      <label for="comment">Comment</label>
      <textarea id="comment" v-model="comment" rows="2" placeholder="Optional note" />
    </div>

    <EditModal
      :open="editModal.open"
      :kind="editModal.kind"
      :item="editModal.item"
      @close="closeEdit"
    />
  </div>
</template>

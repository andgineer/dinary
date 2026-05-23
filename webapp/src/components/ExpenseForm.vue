<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Calendar, ChevronRight } from "lucide-vue-next";
import TagPicker from "./TagPicker.vue";
import ManageList from "./ManageList.vue";
import CurrencyPicker from "./CurrencyPicker.vue";
import IconBtn from "./IconBtn.vue";
import EditModal from "../modals/EditModal.vue";
import InlineCreateRow from "./InlineCreateRow.vue";
import InlineCreateEvent from "./InlineCreateEvent.vue";
import CategoryQuickPicks from "./CategoryQuickPicks.vue";
import CategorySheet from "./CategorySheet.vue";
import { useCatalogStore } from "../stores/catalog.js";
import { useQueueStore } from "../stores/queue.js";
import { useToastStore } from "../stores/toast.js";
import { useCurrencyStore } from "../stores/currency.js";
import { flushQueue } from "../composables/flushQueue.js";
import { useCatalogManage } from "../composables/catalogManage.js";
import { addResultMessage, validateTagName } from "../composables/addResult.js";


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
const categorySheetOpen = ref(false);

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
const newing = ref(null); // 'group' | 'category' | 'tag' | 'event' | null

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
  const gid = catalog.defaultGroupId;
  if (!gid || !activeGroups.value.some((g) => g.id === gid)) return;
  groupId.value = String(gid);
  const defaultCatId = catalog.defaultCategoryForGroup(gid);
  if (defaultCatId && activeCategories.value.some((c) => c.id === defaultCatId)) {
    categoryId.value = String(defaultCatId);
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
  const defaultCatId = catalog.defaultCategoryForGroup(gid);
  if (defaultCatId && catalog.categories(gid).some((c) => c.id === defaultCatId)) {
    categoryId.value = String(defaultCatId);
  } else {
    categoryId.value = "";
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

function selectEvent(ev) {
  if (eventId.value === String(ev.id)) {
    eventId.value = "";
    userEventOverride.value = false;
  } else {
    eventId.value = String(ev.id);
    onEventChanged();
  }
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

function onQuickPick(catId) {
  const cat = catalog.findCategoryById(catId);
  if (!cat) return;
  categoryId.value = String(catId);
  groupId.value = String(cat.group_id);
}

async function init() {
  if (!navigator.onLine) {
    applyDefaultGroupAndCategory();
    applyAutoAttachEventForDate();
    selectedCurrency.value = currency.preferredCode;
    return;
  }
  await catalog.loadIfNeeded();
  if (catalog.lastError) {
    toast.show(`Catalog: ${catalog.lastError.message}`, "error");
  }
  applyDefaultGroupAndCategory();
  applyAutoAttachEventForDate();
  try {
    await currency.loadIfNeeded();
  } catch (err) {
    toast.show(`Currencies: ${err?.message || err}`, "error");
  }
  if (!selectedCurrency.value) {
    selectedCurrency.value = currency.preferredCode;
  }
}

function _buildExpenseDatetime(dateStr) {
  const now = new Date();
  const off = now.getTimezoneOffset();
  const sign = off <= 0 ? "+" : "-";
  const abs = Math.abs(off);
  const hh = String(Math.floor(abs / 60)).padStart(2, "0");
  const mm = String(abs % 60).padStart(2, "0");
  const time = [
    String(now.getHours()).padStart(2, "0"),
    String(now.getMinutes()).padStart(2, "0"),
    String(now.getSeconds()).padStart(2, "0"),
  ].join(":");
  return `${dateStr}T${time}${sign}${hh}:${mm}`;
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
    expense_datetime: _buildExpenseDatetime(date.value),
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

function requestAdd(kind) {
  newing.value = kind;
}

async function handleCreate(kind, value) {
  try {
    let body;
    if (kind === "event") {
      body = value;
    } else if (kind === "category") {
      body = { name: value, group_id: Number(groupId.value) };
    } else {
      body = { name: value };
    }
    const snap = await catalog.add(kind, body);
    const msg = addResultMessage(kind, snap?.status);
    if (msg) toast.show(msg, "info");
    newing.value = null;
  } catch (err) {
    toast.show(err?.message || `Failed to add ${kind}`, "error");
  }
}

function onOnline() {
  void flushQueue();
}

onMounted(() => {
  void init();
  window.addEventListener("online", onOnline);
});

onBeforeUnmount(() => {
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
          <CurrencyPicker v-model="selectedCurrency" @close="currencyPickerOpen = false" />
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

    <!-- Category: quick picks + sheet opener in one card -->
    <div class="category-card">
      <div v-if="catalog.frequentCategories.length > 0" class="category-picks-row">
        <CategoryQuickPicks
          :categories="catalog.frequentCategories"
          @select="onQuickPick"
        />
      </div>
      <div
        class="category-select-row"
        role="button"
        tabindex="0"
        data-testid="category-pick-btn"
        @click="categorySheetOpen = true"
        @keydown.enter="categorySheetOpen = true"
        @keydown.space.prevent="categorySheetOpen = true"
      >
        <span v-if="categoryId">{{ catalog.findCategoryById(Number(categoryId))?.name }}</span>
        <span v-else class="placeholder">Select category…</span>
        <ChevronRight :size="16" class="pick-chevron" aria-hidden="true" />
      </div>
    </div>

    <!-- Event chips -->
    <div class="form-group chips-section">
      <div class="chips-header">
        <span class="chips-label">Event</span>
        <div class="chips-actions">
          <IconBtn icon="plus" tone="accent" label="New event" @click="requestAdd('event')" />
          <IconBtn
            :icon="manageMode.event ? 'x' : 'cog'"
            tone="muted"
            :label="manageMode.event ? 'Close events' : 'Manage events'"
            @click="toggleManage('event')"
          />
        </div>
      </div>
      <InlineCreateEvent
        v-if="newing === 'event'"
        @save="handleCreate('event', $event)"
        @cancel="newing = null"
      />
      <div class="event-chips">
        <button
          v-for="ev in activeEvents"
          :key="ev.id"
          type="button"
          class="event-chip"
          :class="{ 'is-selected': eventId === String(ev.id) }"
          @click="selectEvent(ev)"
        >
          {{ eventLabel(ev) }}
        </button>
        <span v-if="activeEvents.length === 0" class="chips-empty">no active events</span>
      </div>
      <ManageList
        v-if="manageMode.event"
        kind="event"
        :active="activeEvents"
        :inactive="inactiveEventsList"
        :label="eventLabel"
        :pending-id="pendingManageId.event"
        @deactivate="runCatalogAction('event', $event, 'deactivate')"
        @reactivate="runCatalogAction('event', $event, 'reactivate')"
        @delete="runCatalogAction('event', $event, 'remove')"
        @edit="onEdit('event', $event)"
      />
    </div>

    <!-- Tags chips -->
    <div class="form-group chips-section">
      <div class="chips-header">
        <span class="chips-label">Tags</span>
        <div class="chips-actions">
          <IconBtn icon="plus" tone="accent" label="New tag" @click="requestAdd('tag')" />
          <IconBtn
            :icon="manageMode.tag ? 'x' : 'cog'"
            tone="muted"
            :label="manageMode.tag ? 'Close tags' : 'Manage tags'"
            @click="toggleManage('tag')"
          />
        </div>
      </div>
      <InlineCreateRow
        v-if="newing === 'tag'"
        placeholder="New tag name…"
        :validate="validateTagName"
        @save="handleCreate('tag', $event)"
        @cancel="newing = null"
      />
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
      <input
        id="comment"
        v-model="comment"
        type="text"
        class="comment-input"
        placeholder="Comment"
        aria-label="Comment"
        autocomplete="off"
      />
    </div>

    <EditModal
      :open="editModal.open"
      :kind="editModal.kind"
      :item="editModal.item"
      @close="closeEdit"
    />
  </div>

  <CategorySheet
    :open="categorySheetOpen"
    :suggestions="[]"
    @select="onQuickPick($event); categorySheetOpen = false"
    @close="categorySheetOpen = false"
  />
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

/* Category card: quick picks + selector as one grouped panel */
.category-card {
  margin-bottom: 1rem;
  background: var(--field);
  border: 1.5px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}

.category-picks-row {
  padding: 0.6rem 0.6rem 0.5rem;
  border-bottom: 1px solid var(--border);
}

.category-picks-row :deep(.quick-picks) {
  margin-bottom: 0;
}

.category-select-row {
  display: flex;
  align-items: center;
  min-height: 46px;
  padding: 0 0.75rem;
  cursor: pointer;
  font-size: 0.9rem;
  color: var(--text);
  transition: background 0.1s;
}

.category-select-row:hover {
  background: rgba(255, 255, 255, 0.03);
}

.placeholder {
  color: var(--muted);
}

.pick-chevron {
  margin-left: auto;
  flex-shrink: 0;
  color: var(--muted);
}

/* Shared chips section (event + tags) */
.chips-section {
  margin-bottom: 0.75rem;
}

.chips-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.35rem;
}

.chips-label {
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.chips-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.event-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  padding: 0.5rem;
  background: var(--field);
  border-radius: 8px;
  border: 1px solid var(--border);
  min-height: 40px;
  align-items: center;
}

.event-chip {
  padding: 0.25rem 0.65rem;
  background: var(--surface);
  border: none;
  border-radius: 999px;
  font-size: 0.8rem;
  cursor: pointer;
  color: var(--text);
  white-space: nowrap;
  width: auto;
  margin-bottom: 0;
  transition: background 0.12s, color 0.12s;
}

.event-chip.is-selected {
  background: var(--accent);
  color: #fff;
}

.chips-empty {
  font-size: 0.8rem;
  color: var(--muted);
  font-style: italic;
}

/* Comment single-line input */
.comment-input {
  display: block;
  width: 100%;
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.55rem 0.75rem;
  font-size: 0.9rem;
  color: var(--text);
  margin-bottom: 0;
}

.comment-input:focus {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
  border-color: transparent;
}
</style>

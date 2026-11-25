<script setup>
import { onMounted, ref, watch } from "vue";
import { ArrowDown, ArrowUp, ChevronDown } from "lucide-vue-next";
import BreakdownPanel from "../components/BreakdownPanel.vue";
import BreakdownRow from "../components/BreakdownRow.vue";
import { trendKey, useAnalyticsStore } from "../stores/analytics.js";
import { useOnline } from "../composables/useOnline.js";
import { useToastStore } from "../stores/toast.js";

const BLOCK_HEADINGS = { category: "BY CATEGORY", event: "BY EVENT", chain: "BY SHOP", item: "BY ITEM" };

const store = useAnalyticsStore();
const { isOnline } = useOnline();
const toast = useToastStore();
const loadFailed = ref(false);
const expanded = ref(null);
const detailErrors = ref({});
const detailLoading = ref({});

async function loadSummary() {
  loadFailed.value = false;
  try {
    await store.loadIfNeeded();
  } catch {
    loadFailed.value = true;
    toast.show("Couldn't load stats", "error");
  }
}

onMounted(() => {
  if (isOnline.value) loadSummary();
});

function eventTarget(ev) {
  return { type: "event", key: ev.id, load: () => store.loadEventDetail(ev.id) };
}

function trendTarget(t) {
  return { type: "trend", key: trendKey(t.kind, t.id), load: () => store.loadTrendDetail(t.kind, t.id) };
}

function isExpanded(type, key) {
  return expanded.value?.type === type && expanded.value.key === key;
}

function toggle(target) {
  expanded.value = isExpanded(target.type, target.key) ? null : target;
}

function cachedDetail(type, key) {
  return type === "event" ? store.eventDetails[key] : store.trendDetails[key];
}

async function ensureDetail(target) {
  const slot = `${target.type}-${target.key}`;
  if (cachedDetail(target.type, target.key) || detailLoading.value[slot] || !isOnline.value) return;
  detailLoading.value = { ...detailLoading.value, [slot]: true };
  detailErrors.value = { ...detailErrors.value, [slot]: false };
  try {
    await target.load();
  } catch {
    detailErrors.value = { ...detailErrors.value, [slot]: true };
  } finally {
    detailLoading.value = { ...detailLoading.value, [slot]: false };
  }
}

watch([expanded, () => store.eventDetails, () => store.trendDetails, isOnline], () => {
  if (expanded.value !== null) ensureDetail(expanded.value);
});

function trendDetail(t) {
  return store.trendDetails[trendKey(t.kind, t.id)];
}

function isEmpty(type, detail) {
  if (type === "event") return !detail.categories.length;
  return detail.blocks.every((b) => !b.rows.length);
}

function detailState(type, key) {
  const slot = `${type}-${key}`;
  const detail = cachedDetail(type, key);
  if (detail) return isEmpty(type, detail) ? "empty" : "ready";
  if (detailLoading.value[slot]) return "loading";
  if (detailErrors.value[slot]) return "error";
  if (!isOnline.value) return "offline";
  return "loading";
}

function eventBarWidth(categories, share) {
  const top = categories[0]?.share ?? 0;
  return top > 0 ? share / top : 0;
}

function directionColor(direction) {
  return direction === "up" ? "var(--up)" : "var(--down)";
}

function reasonLine(reason) {
  return `mostly ${reason.names.join(", ")}`;
}

function rowLabel(row) {
  if (row.label !== null) return row.label;
  return row.count !== null ? `other (${row.count})` : "no event";
}

function rowSublabel(t, block, row) {
  if (t.kind !== "tag" || block.dimension !== "category" || row.label === null) return null;
  return row.sublabel ?? "No group";
}

function headerDirection(detail) {
  return detail.delta === "0" ? null : detail.direction;
}

function rowDirection(row) {
  return row.share > 0 ? row.direction : null;
}
</script>

<template>
  <div class="analytics">
    <!-- loading skeleton when no cached data yet -->
    <div v-if="!store.summary && store.loading" class="skeleton-wrap">
      <div class="skeleton skeleton-hero" />
      <div class="skeleton-row">
        <div class="skeleton skeleton-card" />
        <div class="skeleton skeleton-card" />
        <div class="skeleton skeleton-card" />
      </div>
    </div>

    <template v-else-if="store.summary">
      <!-- SUMMARY section -->
      <div class="eyebrow">
        <span class="eyebrow-label" style="color: var(--stat)">SUMMARY</span>
        <span class="eyebrow-hint">year to date</span>
      </div>

      <!-- Hero: savings -->
      <div class="stat-card stat-hero">
        <span class="stat-label">SAVED THIS YEAR</span>
        <div class="stat-value-row">
          <span class="stat-value" style="color: var(--income)">
            {{ store.summary.ytd_savings }}
          </span>
          <span class="stat-currency">{{ store.summary.currency }}</span>
        </div>
        <span class="stat-sub">
          {{ store.summary.savings_rate }} savings rate · income − expenses
        </span>
      </div>

      <!-- 3-card row -->
      <div class="stat-row">
        <div class="stat-card stat-small">
          <span class="stat-label">THIS MONTH</span>
          <div class="stat-value-row">
            <span class="stat-value">{{ store.summary.this_month_total }}</span>
            <span class="stat-currency">{{ store.summary.currency }}</span>
          </div>
        </div>
        <div class="stat-card stat-small">
          <span class="stat-label">LAST MONTH</span>
          <div class="stat-value-row">
            <span class="stat-value">{{ store.summary.last_month_total }}</span>
            <span class="stat-currency">{{ store.summary.currency }}</span>
          </div>
        </div>
        <div class="stat-card stat-small">
          <span class="stat-label">YTD SPENT</span>
          <div class="stat-value-row">
            <span class="stat-value">{{ store.summary.ytd_total }}</span>
            <span class="stat-currency">{{ store.summary.currency }}</span>
          </div>
        </div>
      </div>

      <!-- TRENDS (only when data present) -->
      <template v-if="store.trends?.length">
        <div class="eyebrow">
          <span class="eyebrow-label" style="color: var(--stat)">TRENDS</span>
          <span class="eyebrow-hint">last 3 months vs the 3 before</span>
        </div>
        <div class="trends-list">
          <div
            v-for="t in store.trends"
            :key="trendKey(t.kind, t.id)"
            class="list-row trend-row"
            :class="{ 'row-expanded': isExpanded('trend', trendKey(t.kind, t.id)) }"
          >
            <button
              type="button"
              class="row-toggle trend-toggle"
              :aria-expanded="isExpanded('trend', trendKey(t.kind, t.id)) ? 'true' : 'false'"
              :aria-controls="`trend-detail-${trendKey(t.kind, t.id)}`"
              @click="toggle(trendTarget(t))"
            >
              <div class="row-left">
                <span class="trend-name">{{ t.basket_name }}</span>
                <span v-if="t.reason" class="trend-reason">{{ reasonLine(t.reason) }}</span>
              </div>
              <span class="trend-pct" :style="{ color: directionColor(t.direction) }">
                <ArrowUp v-if="t.direction === 'up'" :size="11" stroke-width="3" aria-hidden="true" />
                <ArrowDown v-else :size="11" stroke-width="3" aria-hidden="true" />
                {{ t.pct }}
              </span>
              <ChevronDown class="row-chevron" :size="16" aria-hidden="true" />
            </button>

            <BreakdownPanel
              v-if="isExpanded('trend', trendKey(t.kind, t.id))"
              :id="`trend-detail-${trendKey(t.kind, t.id)}`"
              class="trend-detail"
              :state="detailState('trend', trendKey(t.kind, t.id))"
              empty-text="No spend in either period"
            >
              <template v-if="trendDetail(t)">
                <div class="detail-block trend-summary">
                  <span class="detail-heading">3 MONTHS BEFORE → LAST 3 MONTHS</span>
                  <BreakdownRow
                    :label="`${trendDetail(t).prior} → ${trendDetail(t).recent}`"
                    :amount="trendDetail(t).delta"
                    :currency="trendDetail(t).currency"
                    :direction="headerDirection(trendDetail(t))"
                  />
                </div>
                <div
                  v-for="block in trendDetail(t).blocks"
                  :key="block.dimension"
                  class="detail-block"
                  :data-dimension="block.dimension"
                >
                  <div class="detail-heading-row">
                    <span class="detail-heading">{{ BLOCK_HEADINGS[block.dimension] }}</span>
                    <span v-if="block.receipt_backed_only" class="detail-caption">
                      receipts only · {{ block.prior }} → {{ block.recent }} · {{ block.delta }}
                    </span>
                  </div>
                  <BreakdownRow
                    v-for="(row, i) in block.rows"
                    :key="row.key ?? `${row.count === null ? 'none' : 'other'}-${i}`"
                    :label="rowLabel(row)"
                    :sublabel="rowSublabel(t, block, row)"
                    :amount="row.delta"
                    :detail="`${row.prior} → ${row.recent}`"
                    :width="row.share"
                    :direction="rowDirection(row)"
                  />
                </div>
              </template>
            </BreakdownPanel>
          </div>
        </div>
      </template>

      <div v-if="store.events.length" class="events-list">
        <div
          v-for="ev in store.events"
          :key="ev.id"
          class="list-row event-row"
          :class="{ 'event-open': ev.open, 'row-expanded': isExpanded('event', ev.id) }"
        >
          <button
            type="button"
            class="row-toggle event-toggle"
            :aria-expanded="isExpanded('event', ev.id) ? 'true' : 'false'"
            :aria-controls="`event-detail-${ev.id}`"
            @click="toggle(eventTarget(ev))"
          >
            <div class="row-left">
              <div class="event-title-row">
                <span v-if="ev.open" class="event-dot" aria-hidden="true" />
                <span class="event-name">{{ ev.name }}</span>
                <span v-if="ev.open" class="event-pill">OPEN</span>
              </div>
              <span class="event-dates">{{ ev.date_range }}</span>
            </div>
            <span class="event-total">
              {{ ev.total }}
              <span class="event-currency">{{ ev.currency }}</span>
            </span>
            <ChevronDown class="row-chevron" :size="16" aria-hidden="true" />
          </button>

          <BreakdownPanel
            v-if="isExpanded('event', ev.id)"
            :id="`event-detail-${ev.id}`"
            class="event-detail"
            :state="detailState('event', ev.id)"
          >
            <div class="detail-block">
              <span class="detail-heading">BY CATEGORY</span>
              <BreakdownRow
                v-for="c in store.eventDetails[ev.id].categories"
                :key="c.category_id"
                :label="c.category_name"
                :sublabel="c.group_name ?? 'No group'"
                :amount="c.total"
                :currency="c.currency"
                :width="eventBarWidth(store.eventDetails[ev.id].categories, c.share)"
              />
            </div>
            <div class="detail-block">
              <span class="detail-heading">LAST 7 DAYS WITH SPEND</span>
              <BreakdownRow
                v-for="d in store.eventDetails[ev.id].days"
                :key="d.date"
                class="detail-day"
                :label="d.date_label"
                :amount="d.total"
                :currency="d.currency"
              />
            </div>
          </BreakdownPanel>
        </div>
      </div>

      <div v-else class="events-empty">
        <span>No events in the last 12 months</span>
      </div>
    </template>

    <!-- no cache + offline -->
    <div v-else-if="!isOnline" class="empty-state">
      <span>Offline — no cached data</span>
    </div>

    <div v-else-if="loadFailed" class="empty-state">
      <span>Couldn't load stats</span>
    </div>
  </div>
</template>

<style scoped>
.analytics {
  display: flex;
  flex-direction: column;
  gap: 0;
}

/* ── Eyebrow ── */
.eyebrow {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 0.25rem;
  margin-bottom: 0.6rem;
  margin-top: 1.375rem;
}
.eyebrow:first-child { margin-top: 0; }
.eyebrow-label {
  font-size: 0.6875rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
}
.eyebrow-hint {
  margin-left: auto;
  font-size: 0.7rem;
  color: var(--muted);
}

/* ── Stat cards ── */
.stat-hero {
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.18), var(--field));
  border: 1px solid rgba(129, 140, 248, 0.35);
  border-radius: 14px;
  padding: 1rem 1.1rem;
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 8px;
}

.stat-row {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 8px;
  margin-bottom: 0;
}

.stat-small {
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.75rem 0.8rem;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.stat-label {
  font-size: 0.625rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
}

.stat-hero .stat-label {
  font-size: 0.7rem;
  color: var(--income);
}

.stat-value-row {
  display: flex;
  align-items: baseline;
  gap: 5px;
  flex-wrap: wrap;
}

.stat-value {
  font-family: var(--font-num);
  font-weight: 600;
  font-size: 1.15rem;
  line-height: 1.05;
  color: var(--text);
}

.stat-hero .stat-value {
  font-size: 2rem;
}

.stat-currency {
  font-family: var(--font-num);
  font-size: 0.7rem;
  color: var(--muted-2);
}

.stat-hero .stat-currency {
  font-size: 0.85rem;
}

.stat-sub {
  font-size: 0.78rem;
  color: var(--muted);
}

/* ── Trends and events ── */
.trends-list,
.events-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.events-list,
.events-empty {
  margin-top: 1.375rem;
}

.list-row {
  background: var(--field);
  border-radius: 10px;
  border: 1px solid var(--border);
  border-left-width: 3px;
  border-left-color: transparent;
}

.row-toggle {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 0.7rem 0.85rem;
  background: none;
  border: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.row-chevron {
  flex-shrink: 0;
  color: var(--muted-2);
  transition: transform 0.15s ease;
}

.row-expanded .row-chevron { transform: rotate(180deg); }

.trend-name {
  display: block;
  font-size: 0.9375rem;
  font-weight: 600;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.trend-reason {
  display: block;
  margin-top: 3px;
  font-size: 0.72rem;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.trend-pct {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
  font-family: var(--font-num);
  font-weight: 600;
  font-size: 0.85rem;
}

/* ── Breakdown content ── */
.detail-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.trend-summary :deep(.detail-name) {
  font-family: var(--font-num);
}

.detail-heading-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.detail-heading {
  font-size: 0.625rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
}

.detail-caption {
  font-family: var(--font-num);
  font-size: 0.68rem;
  color: var(--muted-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.event-open {
  border-left-color: var(--stat);
}

.row-left {
  min-width: 0;
  flex: 1;
}

.event-title-row {
  display: flex;
  align-items: center;
  gap: 7px;
}

.event-dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--stat);
  box-shadow: 0 0 0 3px rgba(129, 140, 248, 0.25);
  flex-shrink: 0;
}

.event-name {
  font-size: 0.9375rem;
  font-weight: 600;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.event-row:not(.event-open) .event-name { color: var(--muted); }

.event-pill {
  font-size: 0.58rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  color: var(--stat);
  background: rgba(129, 140, 248, 0.15);
  border-radius: 999px;
  padding: 1px 7px;
  flex-shrink: 0;
}

.event-dates {
  font-family: var(--font-num);
  font-size: 0.72rem;
  color: var(--muted);
  margin-top: 3px;
  display: block;
}

.event-row:not(.event-open) .event-dates { color: var(--muted-2); }

.event-total {
  font-family: var(--font-num);
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--text);
  flex-shrink: 0;
}

.event-row:not(.event-open) .event-total { color: var(--muted); }

.event-currency {
  font-size: 0.7rem;
  color: var(--muted-2);
}

/* ── Skeleton ── */
.skeleton-wrap { display: flex; flex-direction: column; gap: 8px; }
.skeleton-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }

.skeleton {
  background: var(--field);
  border-radius: 10px;
  animation: pulse 1.4s ease-in-out infinite;
}
.skeleton-hero { height: 96px; border-radius: 14px; }
.skeleton-card { height: 72px; }

@keyframes pulse {
  0%, 100% { opacity: 0.5; }
  50% { opacity: 1; }
}

/* ── Empty ── */
.events-empty,
.empty-state {
  text-align: center;
  font-size: 0.85rem;
  color: var(--muted);
  padding: 2rem 0;
}
</style>

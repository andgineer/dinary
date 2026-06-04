<script setup>
import { onMounted } from "vue";
import { ArrowDown, ArrowUp } from "lucide-vue-next";
import { useAnalyticsStore } from "../stores/analytics.js";
import { useOnline } from "../composables/useOnline.js";

const store = useAnalyticsStore();
const { isOnline } = useOnline();

onMounted(() => {
  if (isOnline.value) store.fetchAll();
});
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
      <div v-if="store.trends?.length" class="trends-wrap">
        <div
          v-for="t in store.trends"
          :key="t.basket_name"
          class="trend-chip"
        >
          <span class="trend-name">{{ t.basket_name }}</span>
          <span
            class="trend-pct"
            :style="{ color: t.direction === 'up' ? 'var(--up)' : 'var(--down)' }"
          >
            <ArrowUp v-if="t.direction === 'up'" :size="11" stroke-width="3" aria-hidden="true" />
            <ArrowDown v-else :size="11" stroke-width="3" aria-hidden="true" />
            {{ t.pct }}
          </span>
        </div>
      </div>

      <div v-if="store.events.length" class="events-list">
        <div
          v-for="ev in store.events"
          :key="ev.id"
          class="event-row"
          :class="{ 'event-open': ev.open }"
        >
          <div class="event-left">
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

/* ── Trends ── */
.trends-wrap {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 1.375rem;
}

.trend-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 0.35rem 0.6rem;
  background: var(--field);
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 0.78rem;
  color: var(--text);
}

.trend-name { color: var(--text); }

.trend-pct {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-family: var(--font-num);
  font-weight: 600;
  font-size: 0.74rem;
}

/* ── Events ── */
.events-list,
.events-empty {
  margin-top: 1.375rem;
}

.events-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.event-row {
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--field);
  border-radius: 10px;
  border: 1px solid var(--border);
  border-left-width: 3px;
  border-left-color: transparent;
  padding: 0.7rem 0.85rem;
}

.event-open {
  border-left-color: var(--stat);
}

.event-left {
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

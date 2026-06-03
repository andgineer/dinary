# Analytics — PWA embedded view

## Scope

A dedicated `/analytics` route in the existing Vue PWA. Shows a summary of the
ledger drawn from the dinary server. Complements the standalone `dinary-analytics`
app (`analytics-ai.md`) — the PWA view is always-accessible and mobile-friendly;
the standalone app is where deep exploration and AI interaction happen.

Explicitly out of scope: OLTP writes, sheet logging, imports, migrations — those
stay in the existing service layer.

## Data source

New FastAPI endpoints reading dinary SQLite directly. DuckDB is not used on the
server (1 GB RAM constraint — see `architecture.md`). Queries are simple GROUP BY
aggregations that SQLite handles without issue at the expected data volume.

## Config

A minimal `analytics_pwa_config` table in dinary SQLite stores display preferences
for the PWA view:

- Which tag buckets appear as separate columns (vs folded into "baseline").
- Default time range (current year / last 12 months / all time).
- Pinned events to highlight.

This table is written by the `dinary-analytics` standalone app via `PUT
/api/analytics/config`. The server never writes it. The same auth as the rest of
the API applies.

## First dashboards

1. **Events** — all events (trips, relocation, etc.) with total cost in accounting
   currency, sorted by date descending.
2. **Tag buckets** — spending by tag group: each configured bucket as its own bar
   or column, remaining expenses as "baseline". Yearly or monthly granularity.
3. **Monthly trend** — total expenses per month for the current year, broken down
   by category group.

## Implementation outline

- New Vue route `/analytics`, new `AnalyticsView.vue` component.
- New FastAPI router `api/analytics.py`.
- New SQL: `db/sql/analytics_events.sql`, `db/sql/analytics_tags.sql`,
  `db/sql/analytics_monthly.sql`.
- New migration: `analytics_pwa_config` table (`key TEXT PK`, `value JSON`,
  `updated_at TIMESTAMP`).
- Endpoints: `GET /api/analytics/config`, `PUT /api/analytics/config`,
  `GET /api/analytics/events`, `GET /api/analytics/tags`, `GET /api/analytics/monthly`.

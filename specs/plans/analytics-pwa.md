# Analytics — PWA embedded view

## Scope

A dedicated `/analytics` route in the existing Vue PWA. Shows a summary of the
ledger drawn from the dinary server. Complements the standalone `dinary-analytics`
app (`analytics-ai.md`) — the PWA view is always-accessible and mobile-friendly;
the standalone app is where deep exploration and AI interaction happen.

Out of scope: OLTP writes, sheet logging, imports, migrations.

## Data source

New FastAPI endpoints reading dinary SQLite directly. DuckDB is not used on the
server (1 GB RAM constraint — see `architecture.md`). All queries are plain SQLite
GROUP BY aggregations.

## Default views (zero configuration)

These views are always present regardless of whether analytics-ai has ever been run.

1. **Monthly trend** — total expenses per month for the selected year, broken down
   by category group. Query: expenses JOIN categories JOIN category_groups,
   GROUP BY year_month, group_name.

2. **Events** — all events with total cost in accounting currency, sorted by date
   descending. Query: expenses JOIN events, GROUP BY event_id.

## Config-driven basket views

When the user has run analytics-ai and saved one or more Analytics Views, those
views appear as additional tabs in `/analytics`. The PWA has no view editor — the
standalone app is the only configuration tool.

Config is written by analytics-ai via `PUT /api/analytics/config` and stored in
`analytics_pwa_config` as a JSON blob under key `views`. The server executes basket
assignment on request using a parameterised SQLite query; no basket logic lives in
Python. Basket assignment: for each expense, check event triggers first, then tag
triggers, first match wins, unmatched go to the default basket.

## Config table

`analytics_pwa_config` in dinary SQLite:

```
key        TEXT  PRIMARY KEY
value      JSON
updated_at TIMESTAMP
```

Only key in use: `views` — JSON array of view config objects (same schema as
stored in analytics.db, see `analytics-ai.md`). The server never writes this
table; only `PUT /api/analytics/config` does.

## Implementation outline

**Backend**

- New FastAPI router `api/analytics.py`.
- New migration: `analytics_pwa_config` table.
- `GET /api/analytics/config` — returns current `analytics_pwa_config` rows as JSON.
- `PUT /api/analytics/config` — replaces one or more keys; called by analytics-ai only.
- `GET /api/analytics/monthly?year=<year>` — monthly trend data.
- `GET /api/analytics/events` — events with totals.
- `GET /api/analytics/view/<view_id>?year=<year>` — executes basket assignment for
  the named view config stored in `analytics_pwa_config.views`, returns
  (basket_name, year_month, group_name, amount) rows.
- New SQL files: `db/sql/analytics_monthly.sql`, `db/sql/analytics_events.sql`,
  `db/sql/analytics_view.sql` (parameterised basket assignment).

**Frontend**

- New Vue route `/analytics`, new `AnalyticsView.vue`.
- On load: fetches config + default view data in parallel.
- Tab bar: "Monthly" tab + "Events" tab always present; one tab per basket view
  from config (if any).
- Period selector (year) applies to all tabs.
- Chart library: to be decided at implementation time — keep it lightweight.

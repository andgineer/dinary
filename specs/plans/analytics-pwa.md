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

## The analytics page

See [design handoff](design_handoff_nav_and_analytics/README.md) — Part 2 (Sketch A)
for pixel-level layout, component specs, and acceptance checklist.

A single `/analytics` page. No tabs. Two modes depending on whether a basket config
has been pushed from `dinary-analytics`.

**Always shown:**

1. **Period cards** — YTD savings (hero card with savings rate as subtitle) + three
   equal cards: current month total, last completed month total, year-to-date spent.
   Savings rate = YTD savings / YTD income.

2. **Events** — all events from the last 12 months (open and closed), sorted by
   date_from descending, each showing its total cost in accounting currency. Open
   events are visually distinguished from closed ones.

**Always shown when data is sufficient:**

3. **Basket trends** — top-5 category groups and tags ranked by % change between
   the last 3 months and the 3 months before that. Items with
   `MAX(recent, prior) < AVG * 0.15` are excluded as noise (near-empty groups,
   infrequently-used tags). Requires no configuration — generated automatically
   from existing categories and tags.

## Config mechanism

`analytics_pwa_config` table in dinary SQLite:

```
key        TEXT  PRIMARY KEY
value      JSON
updated_at TIMESTAMP
```

Key `active_view` — the view config object from analytics-ai (same schema as
`view:<uuid>` in analytics.db, see `analytics-ai.md`) that the server uses to
assign expenses to baskets when computing trends. Written only by
`PUT /api/analytics/config` from `dinary-analytics` when the user saves a view.
The dinary server never writes this table.

## Implementation outline

**Backend**

- New FastAPI router `api/analytics.py`.
- `GET /api/analytics/summary` — single endpoint, single page load. Response:
  ```
  summary: { this_month_total, last_month_total, ytd_total, ytd_savings,
             savings_rate, currency }        # amounts preformatted "156 000"
  events:  [{ id, name, date_range, total, currency, open }]  # date_range preformatted
  trends:  [{ basket_name, direction, pct }] | null           # null when insufficient data
  ```
- New SQL files: `db/sql/analytics_summary.sql`, `db/sql/analytics_ytd_income.sql`,
  `db/sql/analytics_events.sql`, `db/sql/analytics_auto_trends.sql`.

**Frontend**

- New Vue route `/analytics`, new `AnalyticsView.vue`.
- On load: single fetch to `GET /api/analytics/summary`.
- Renders basket trends row only when response includes trend data.
- No tabs, no period selector.
- Chart library: to be decided at implementation time — keep it lightweight.

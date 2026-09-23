# PWA analytics view

A dedicated `/analytics` route in the Vue PWA. Read-only summary of the ledger.
Complements the standalone `dinary-analytics` desktop app — the PWA view is
always-accessible and mobile-friendly; the desktop app is where deep exploration
and AI interaction happen.

## Page content

A single page, no tabs. Three sections:

1. **Period cards** — YTD savings as a hero card (with savings rate subtitle) + three
   equal cards: current month total, last completed month total, year-to-date spent.
   Savings rate = YTD savings / YTD income.

2. **Events** — all events from the last 12 months (open and closed), sorted by
   date_from descending, each showing its total cost in accounting currency. Open
   events are visually distinguished from closed ones.

3. **Basket trends** — top-5 category groups and tags ranked by absolute % change
   between the last 3 months and the 3 months before that. Threshold filter:
   items with `MAX(recent, prior) < per-kind AVG * 0.15` are excluded as noise.
   Groups and tags are filtered against their own kind average so tag amounts are
   not swamped by group amounts. Omitted entirely when data is insufficient.

## Data source

`GET /api/analytics/summary` — single endpoint, single page load:

```
summary: { this_month_total, last_month_total, ytd_total, ytd_savings,
           savings_rate, currency }       # amounts preformatted "156 000"
events:  [{ id, name, date_range, total, currency, open }]  # date_range preformatted
trends:  [{ basket_name, direction, pct }] | null
```

All queries are plain SQLite GROUP BY aggregations. DuckDB is not used on the
server (1 GB RAM constraint).

## Client cache

The PWA caches the summary and shows it instantly on open. It refetches when the
cache is older than 24 hours, or when it has been marked dirty: an expense,
income or receipt was added, edited or deleted, or a catalog change altered a
name or entry the page shows (an event, category group or tag changed, the
category template switched, a category moved into a different group, or a
category was activated, which can place it in a group). A fetch taken while the server is still processing receipts
does not count as fresh, so the page keeps refetching on open until processing
finishes; a receipt that has permanently failed does not hold it open. Trends
change month to month, so nothing finer than this is needed.

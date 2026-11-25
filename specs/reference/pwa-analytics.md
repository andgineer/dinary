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

   Tapping an event expands it in place into its own breakdown, fetched on
   demand:
   - **By category** — every category the event's expenses were booked to, largest
     first, each with its amount and a bar proportional to the largest one. The
     breakdown covers the whole event and adds up to the event total: expenses in
     a category that no longer belongs to any group are still listed, under a
     neutral "no group" label.
   - **By recent days** — the spend of the event's most recent days that have any
     spend, capped at seven and labelled as such, so for a longer event it does
     not add up to the event total. A day is the local calendar date the expense
     was recorded with, not the UTC date.

   An event with no expenses shows a single "no expenses yet" line. Opened
   offline with nothing loaded, or after a failed request, the breakdown says so
   instead of showing a loading placeholder.

3. **Basket trends** — top-5 category groups and tags ranked by absolute % change
   between the last 3 months and the 3 months before that. Threshold filter:
   items with `MAX(recent, prior) < per-kind AVG * 0.15` are excluded as noise.
   Groups and tags are filtered against their own kind average so tag amounts are
   not swamped by group amounts. Omitted entirely when data is insufficient.
   A group and a tag may share a name; each trend is identified by its kind and
   basket, never by its name.

   Each trend is a row with its name, the percentage coloured by direction and a
   one-line reason underneath ("mostly 2026-07 Pržno", "mostly фрукты, кафе").
   The reason names only contributors whose change goes the same way as the
   basket's change, and never the spend outside any event:
   - the largest event, when it accounts for at least half of the basket's
     absolute change;
   - otherwise the largest category, when it accounts for at least half;
   - otherwise the two largest categories;
   - with no such contributor the row shows the percentage alone.

   A group that has a single category is only ever explained by an event.

   Tapping a trend expands it in place into a breakdown of the change, fetched
   on demand. Only one trend or event is expanded at a time: opening one closes
   the other. The breakdown starts with both window totals and the signed
   difference, then shows these blocks:
   - **By category** — always present. For a tag basket each category is labelled
     with its group, or "No group".
   - **By event** — spend outside any event is its own "no event" row. Omitted
     when nothing in the basket carries an event in either window.
   - **By shop** and **By item** — receipt-backed spend only, labelled "receipts
     only" and each with its own subtotal, so they are not expected to add up to
     the header. Both are present only when the basket has receipt-backed spend in
     both windows.

   With receipts in one window only, every shop and item would read as new, which
   reflects the start of receipt scanning, not a change in spending.

   Every row shows its signed change, both window amounts and a bar proportional
   to the largest change in its block, coloured by direction. Rows are ordered by
   absolute change. A block shows its four largest rows and folds the rest into one
   "other (n)" row; a block with five rows shows all five. The "no event" row is
   never folded. The category block and the event block therefore each add up to
   the header difference. The breakdown uses exactly the windows of the trend
   ranking, so its header always matches the percentage on the row.

   Opened offline with nothing loaded, or after a failed request, the breakdown
   says so instead of showing a loading placeholder.

## Data source

`GET /api/analytics/summary` returns the whole page in a single request: the
period totals, the YTD savings and savings rate in the accounting currency; every
event of the last 12 months with its date range, total and whether it is open;
and the trends, or nothing when data is insufficient. Each trend carries its
basket's kind and identity, its name, direction, percentage and its one-line
reason, if any: whether it names an event or categories, and the names.
Amounts and date ranges are preformatted.

`GET /api/analytics/events/{event_id}` returns one event's breakdown for the
drill-down, fetched only when the event is expanded: the same
event header as in the summary, the per-category amounts with each category's
share of the event total, and the per-day amounts for the most recent days with
spend. An unknown event is a 404.

`GET /api/analytics/trends/{kind}/{basket_id}` returns one trend's breakdown,
fetched only when the trend is expanded: the basket's name, direction and
percentage, both window totals and the signed difference, and the blocks above,
each with its own subtotal and rows with both window amounts, the signed change
and the bar share. Amounts are preformatted; a negative change carries a true
minus sign. The client supplies the "other" and "no event" captions. A kind
other than group or tag is rejected, and an unknown basket or an inactive tag is
a 404. A basket requested directly with no prior spend has no percentage.

All queries are plain SQLite GROUP BY aggregations. DuckDB is not used on the
server (1 GB RAM constraint).

## Client cache

The PWA caches the summary and shows it instantly on open. It refetches when the
cache is older than 24 hours, or when it has been marked dirty: an expense,
income or receipt was added, edited or deleted, or a catalog change altered a
name or entry the page shows (an event, category group or tag changed, the
category template switched, a category was renamed or moved into a different
group, or a category was activated, which can place it in a group). A fetch
taken while the server is still processing receipts does not count as fresh, so
the page keeps refetching on open until processing finishes; a receipt that has
permanently failed does not hold it open. Trends change month to month, so
nothing finer than this is needed. Event and trend breakdowns are kept in memory
only, never persisted, and are dropped whenever the summary is refetched; a
breakdown requested before such a refetch landed is fetched again rather than
kept. Cached data the current page cannot use is ignored and refetched.

A failed refetch shows an error toast and leaves the cache dirty, so the next
open retries. Cached figures stay on screen; with nothing cached the page shows
an error line instead of staying blank.

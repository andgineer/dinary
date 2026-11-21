# Plan: reasons behind a basket trend in the PWA

## Why

The Analytics screen shows up to five trend chips such as "Еда ↑37%". A chip says that a
category group or a tag changed between the last three months and the three before, and
nothing about why. The ledger already holds the answer along four dimensions: the category
inside the group, the event, the shop chain and the receipt line item.

Measured on the local ledger copy (data up to 2026-09-12, windows anchored on that date):

- "Жильё" rose by 874 EUR. By event the same change reads as +1 211 EUR on the trip
  "2026-07 Pržno", −1 098 EUR on "релокация-в-Сербию" ending, and +940 EUR outside any event.
  By category it is almost all "аренда" (+734 EUR). The event view is the real explanation.
- "Еда" rose by 533 EUR: "фрукты" +268, "кафе" +251, "деликатесы" +157, "продукты" −212.
  By event, "2026-07 Pržno" alone is +580 EUR.
- 30% of the recent window's money carries an event (2 218 of 7 377 EUR), so the event
  dimension matters for most large moves.
- Receipt-backed expenses are 19% of the recent window's money (1 379 of 7 377 EUR) and 4% of
  the prior one (188 of 5 102 EUR), because receipt scanning started in May 2026. Every
  receipt-backed expense maps to exactly one receipt item (939 items, none without a
  normalised name) and every store has a chain (14 stores, 14 chains).

## Target behaviour

- Each trend shows a one-line reason next to its percentage: "Жильё ↑68% · mostly 2026-07 Pržno",
  "Еда ↑37% · mostly 2026-07 Pržno", "Медицина ↓48% · mostly врач".
- Tapping a trend expands it in place, the same way an event expands, into a breakdown of the
  change, fetched on demand:
  - a header with both window totals and the signed difference;
  - **By category** (for a tag basket: by category, labelled with its group);
  - **By event**, with spend outside any event as its own "no event" row;
  - **By shop** and **By item**, covering receipt-backed spend only.
- Every row shows the signed change and both window amounts. Rows are ordered by the absolute
  change. A block shows its four largest rows and folds the rest into one "other" row, so the
  category block and the event block each add up to the header difference.
- Only one trend or event is expanded at a time.

## Design

### Windows

The breakdown uses exactly the window expressions of `analytics_auto_trends.sql`
(`date(e.datetime) >= date('now', '-3 months')` for recent, the three months before for
prior). The chip percentage and the breakdown must come from the same rows, otherwise the
blocks do not add up to the chip. The window predicate is repeated verbatim in each new SQL
file; a test asserts the header of the breakdown equals the summary's numbers for the same
basket.

### Basket identity

`analytics_auto_trends.sql` already selects `kind` (`'group'` or `'tag'`) and the basket `id`;
the endpoint drops both. The summary starts returning them, because a tag and a group can share
a name and the drill-down needs an identity. The Vue list key becomes `kind-id`.

### Drivers

One SQL file per dimension, each taking `(kind, basket_id)` and filtering expenses with the
same predicate:

```sql
WHERE date(e.datetime) >= date('now', '-6 months')
  AND (
        (:kind = 'group' AND c.group_id = :basket_id)
     OR (:kind = 'tag'   AND EXISTS (SELECT 1 FROM expense_tags et
                                     WHERE et.expense_id = e.id AND et.tag_id = :basket_id))
      )
```

| File | Groups by | Notes |
|---|---|---|
| `analytics_trend_header.sql` | — | name, recent, prior for one basket; no row → 404. Tag must be active, as in the trends query |
| `analytics_trend_categories.sql` | `c.id, c.name, g.name` | `LEFT JOIN category_groups` so a tag basket lists categories without a group |
| `analytics_trend_events.sql` | `ev.id, ev.name` | `LEFT JOIN events`; `event_id IS NULL` is the "no event" row |
| `analytics_trend_chains.sql` | `sc.id, sc.name` | `JOIN stores s ON s.id = e.store_id JOIN shop_chains sc ON sc.id = s.chain_id`, `e.receipt_id IS NOT NULL` |
| `analytics_trend_items.sql` | `ri.name_normalized` | `JOIN receipt_items ri ON ri.expense_id = e.id`; sums `e.amount`, not `ri.total_price`, to stay in the accounting currency |

Each returns `(key, label, sublabel, recent, prior)` ordered by `ABS(recent - prior) DESC, label`.

Folding into "other" happens in Python (`_fold_drivers(rows, keep=4)`): the first four rows are
kept, the rest are summed into one row with `label=None`, `count=<n folded>`. A block with
five rows shows all five rather than four plus an "other" of one.

Block visibility:

- **By category** is always present.
- **By event** is omitted when every expense of the basket in both windows has no event.
- **By shop** and **By item** are present only when the basket has receipt-backed spend in
  **both** windows. With receipts in one window only, every shop and item would read as "new",
  which describes the start of scanning, not spending. Both blocks carry
  `receipt_backed_only: true` and their own recent/prior subtotal, and the UI labels them
  "receipts only" so nobody expects them to add up to the header.

### One-line reason

Computed on the server for each of the (at most five) trends in the summary, from the category
and event rows of that basket. Only contributors whose change has the same sign as the
basket's change are candidates; the "no event" row is never a candidate.

1. The largest event contributor accounts for at least half of the basket's absolute change →
   that event.
2. Otherwise the largest category contributor accounts for at least half → that category.
3. Otherwise → the two largest category contributors.
4. No candidate → no reason; the chip shows the percentage alone.

For a group that has a single category the category rule would restate the group, so only the
event rule applies to it.

The summary carries `reason: {"kind": "event" | "category", "names": [...]} | null`; the client
renders `mostly <names joined by ", ">`. This costs two extra aggregate queries per trend, ten
at most, each over six months of one basket.

### API

`GET /api/analytics/summary` — each trend becomes:

```
{ kind, id, basket_name, direction, pct, reason }
```

`GET /api/analytics/trends/{kind}/{basket_id}` — `kind` is a `Literal["group", "tag"]` path
parameter (anything else is a 422), unknown basket is a 404:

```
{
  kind, id, name, direction, pct, currency,
  recent, prior, delta,                       # preformatted, delta signed: "+533" / "−260"
  blocks: [
    { dimension: "category" | "event" | "chain" | "item",
      receipt_backed_only: bool,
      recent, prior, delta,                   # the block's own subtotal
      rows: [ { key, label, sublabel, recent, prior, delta, direction, share, count } ] }
  ]
}
```

`share` is `abs(row delta) / max abs(row delta) in the block`, for the bar width. `label` is
`null` for the "other" row (`count` says how many rows it folds) and for the "no event" row
(`key` is `null`, `count` is `null`); the client supplies both captions. A basket whose prior
total is zero cannot appear in the summary but can be requested directly: `pct` is then `null`.

### Client

- `webapp/src/api/analytics.js`: `fetchTrendDetail(kind, id)`.
- `webapp/src/stores/analytics.js`: `trendDetails` keyed by `kind-id`, `loadTrendDetail(kind, id)`
  with the same generation guard and request de-duplication as `loadEventDetail`; cleared on a
  summary refetch. The two loaders share one helper parameterised by the fetch function and the
  target ref instead of a copy. The persisted data key moves to `dinary:analytics:v2`, since a
  cached v1 trend has no identity to expand; `useStaleCache` already refetches when the data
  key is empty.
- `webapp/src/views/AnalyticsView.vue`: the chip wrap becomes a list of rows built like the
  event rows (name, reason line underneath, percentage on the right, chevron). The expanded
  state becomes one value shared with events (`{type, key}`), so opening a trend closes an
  open event. The loading / offline / error / ready states and the skeleton reuse the event
  panel's markup; extract the panel shell and the bar row into `components/BreakdownPanel.vue`
  and `components/BreakdownRow.vue` and switch the event panel to them, rather than a second
  copy in a view that is already 613 lines.
- Row colours follow `--up` / `--down` as the chips do today.

## Open points, with the recommended answer

- **Reason on the row, or only after a tap?** On the row. The point of the feature is seeing
  the reason without asking, and the rule above names something only when one contributor
  explains at least half of the change, or names the top two. The price is that five chips in
  two lines become five rows.
- **Should a category split caused by starting to scan receipts be filtered out?** No action
  needed. A purchase that used to be one "продукты" total and is now itemised into "фрукты"
  shows as "продукты" down and "фрукты" up without any change in behaviour. Scanning started in
  May 2026, so from mid-November 2026 both windows are scanned and the effect is gone; the
  group total was never affected.
- **Individual large expenses as a fifth block?** Not in this plan. The category and event
  blocks already point at them ("аренда +734"), and the expense list in Review shows the rows.

## Changes

| File | Change |
|---|---|
| `src/dinary/db/sql/analytics_trend_{header,categories,events,chains,items}.sql` | new, as in the table above |
| `src/dinary/api/analytics.py` | summary trends gain `kind`, `id`, `reason`; new `GET /api/analytics/trends/{kind}/{basket_id}`; helpers `_fmt_signed`, `_fold_drivers`, `_trend_block`, `_trend_reason`. Keep each helper small; no complexity suppressions |
| `webapp/src/api/analytics.js` | `fetchTrendDetail` |
| `webapp/src/stores/analytics.js` | `trendDetails`, `loadTrendDetail`, shared detail loader, data key `v2` |
| `webapp/src/components/BreakdownPanel.vue`, `BreakdownRow.vue` | new; used by events and trends |
| `webapp/src/views/AnalyticsView.vue` | trend rows with reason line, shared expanded state, trend panel |
| `specs/reference/pwa-analytics.md` | section 3 describes the reason line and the breakdown (blocks, add-up rule, receipts-only rule, both-windows rule); data source lists the new endpoint; cache paragraph covers trend breakdowns |
| `specs/ui/components.md` | `AnalyticsView` row: trend rows instead of chips; the two new components |

## Tests

All dates relative to `date.today()`, as the existing trend test does; nothing hardcoded.

`tests/api/test_api_analytics.py`:

- summary trends carry `kind`, `id`; a tag and a group with the same name both appear and stay
  distinguishable;
- reason: event rule (one event ≥ half), category rule, top-two rule, no same-sign contributor
  → `null`, "no event" never named, single-category group never names its own category;
- detail header equals the summary's `pct` and direction for the same basket;
- category block and event block each add up to the header delta, including with an "other"
  row (six categories → four rows plus "other" with `count == 2`; five → five rows);
- "no event" row present with `key is None`; event block omitted when no expense has an event;
- chain and item blocks omitted with receipts in one window only, present with receipts in
  both, flagged `receipt_backed_only`, subtotal equal to the receipt-backed delta;
- tag basket: categories listed with their group, a category without a group still counted;
- a falling basket: negative deltas, `direction == "down"`, sign formatted with the minus;
- unknown basket → 404, inactive tag → 404, bad kind → 422, zero prior → `pct is None`.

`webapp/tests/store-analytics.test.js`: `loadTrendDetail` caches, de-duplicates concurrent
calls, refetches when a summary refetch lands mid-flight, is cleared by a summary refetch; a
v1 cache with a fresh timestamp triggers a refetch.

`webapp/tests/component-analytics-view.test.js`: reason line rendered and absent for `null`;
tap expands and fetches once; opening a trend closes an open event; loading, offline, error
states; "other", "no event" and "receipts only" captions; bar width from `share`.

`webapp/tests/component-breakdown-panel.test.js`, `component-breakdown-row.test.js`: the
extracted components; the existing event-detail tests keep passing unchanged against them.

## Verification

1. `uv run inv pre`, `uv run pytest`, `cd webapp && npm test` — all green, no `ECONNREFUSED`
   block in the frontend output.
2. `uv run uvicorn dinary.main:app --host 127.0.0.1 --port <free port>` against the local
   ledger; on the Analytics tab check that "Жильё" names the trip and that its category and
   event blocks each add up to the header difference.

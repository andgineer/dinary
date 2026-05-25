# Handoff: Add Income

## Overview

A new top-level view in the Dinary webapp for recording **monthly incomes**
(salary, refunds, freelance payments, gifts). The view sits between **Add
expense** and **Review** in the segmented header. It has two parts:

1. A compact form at the top (amount + currency + month/year) for entering a
   new income entry. One row per calendar month — trying to POST for a month
   that already has a row returns **409**; to change it, use the edit sheet
   (PATCH).
2. An infinite-scroll list of past monthly incomes below, sorted newest-first,
   grouped by year.

Offline: read-only. No queueing (incomes are not queued offline).

## About the Design Files

`Add Income.html` in this bundle is a **design reference** — a self-contained
standalone HTML prototype. It is not production code and should not be copied
into the app directly.

The task is to **recreate these designs inside the existing Dinary webapp
(`dinary/webapp/`)**, which is a Vue 3 + Pinia + Vite SPA, reusing the existing
components and patterns wherever possible.

To preview the prototype: open `Add Income.html` in a browser.

## Fidelity

**High-fidelity.** Colors, typography, spacing, and layout exactly match the
existing Dinary v0.9 design tokens in `dinary/webapp/src/assets/base.css`.
Reuse the existing CSS variables — do not introduce new values.

---

## Data Model

### Existing `income` table — unchanged

```sql
-- from 0001_initial_schema.sql — do not touch
CREATE TABLE income (
    year   INTEGER NOT NULL,
    month  INTEGER NOT NULL,
    amount DECIMAL(12,2) NOT NULL,   -- stored in accounting_currency (EUR)
    PRIMARY KEY (year, month),
    CHECK (month BETWEEN 1 AND 12)
);
```

`amount` is always in `settings.accounting_currency`. The server converts the
user-entered value on write; `currency_original`/`amount_original` are **not**
stored (conversion is one-way, same pattern as `expenses.amount`).

### New migration `0005_income_logging`

Add only the logging-jobs queue table:

```sql
-- 0005_income_logging.sql
CREATE TABLE income_logging_jobs (
    year        INTEGER NOT NULL,
    month       INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    claimed_at  TIMESTAMP,
    last_error  TEXT,
    PRIMARY KEY (year, month),
    FOREIGN KEY (year, month) REFERENCES income (year, month) ON DELETE CASCADE,
    CHECK (status IN ('pending', 'in_progress', 'poisoned'))
);
```

Rollback: `DROP TABLE IF EXISTS income_logging_jobs;`

### Currency conversion on write

For both POST and PATCH the server:

1. Takes `amount_original` (Decimal > 0) and `currency_original` (string).
2. If `currency_original.upper() == settings.accounting_currency.upper()`,
   stores `amount_original` directly.
3. Otherwise calls `get_rate(con, today, currency_original,
   settings.accounting_currency, offline=True)` and multiplies.
4. Stores the result in `income.amount`.

This is identical to `create_expense_sync` in
`src/dinary/api/controllers/expenses.py`.

---

## Backend Files

New files:

```
src/dinary/
├── db/
│   ├── migrations/
│   │   ├── 0005_income_logging.sql          ← NEW
│   │   └── 0005_income_logging.rollback.sql ← NEW
│   ├── sql/
│   │   ├── list_incomes.sql                 ← NEW
│   │   ├── insert_income.sql                ← NEW
│   │   └── delete_income.sql                ← NEW
│   └── income.py                            ← NEW  (DB layer)
├── api/
│   ├── controllers/
│   │   └── income.py                        ← NEW  (business logic + Pydantic models)
│   └── income.py                            ← NEW  (FastAPI router)
├── background/
│   └── sheet_logging/
│       └── income_sheet_logging.py          ← NEW  (Income-tab drain)
└── main.py                                  ← MODIFIED (register income router;
                                                         import + include_router)
tests/
└── test_income.py                           ← NEW
```

Frontend:

```
dinary/webapp/src/
├── api/income.js             ← NEW
├── components/
│   ├── IncomeForm.vue        ← NEW
│   ├── IncomeRow.vue         ← NEW
│   ├── IncomeEditSheet.vue   ← NEW
│   └── HeaderSegmented.vue   ← MODIFIED (add income tab)
├── stores/income.js          ← NEW
├── views/IncomeView.vue      ← NEW
└── App.vue                   ← MODIFIED (route + offline message)
```

---

## Screens / Views

The prototype shows **7 phone states** + 1 cache-flow diagram. All run inside a
390×780 iOS frame, dark mode.

### A · Idle (cache fresh, online)

- **Header** (sticky, `var(--surface)`, 1px bottom border `var(--surface-2)`):
  - Left: `Dinary v0.9` title + queued badge if applicable.
  - Right: `<HeaderSegmented>` with **4** segments: `Add` (red), **`Income`
    (green, new)**, `Review`, `LLM`. Income segment uses `--success` for its
    active/inactive states the way Add uses `--accent`.
- **Form** (`<IncomeForm>`):
  - Card surface (`var(--surface)`, radius 12, padding 1.25rem).
  - **Hero row**: currency pill (left) + amount input (flex 1) + **month
    picker** (right), gap 8px. Layout mirrors `ExpenseForm.vue` `.hero-row`.
  - Currency pill: `var(--success)` background, `#04140a` text, 0.3rem
    0.6rem padding, radius 8, 0.78rem 700 `var(--font-num)`.
  - Amount input: 64px tall, 2rem 500 `var(--font-num)`, right-aligned, 1px
    bottom border (`var(--border)` resting → `var(--success)` focused).
  - **Month picker**: Calendar icon (14px) + `<input type="month">` native
    input, 0.8rem `--muted`. Default = current YYYY-MM. Replaces the
    day-level date picker from the original prototype — income is per month.
  - No comment field — the `income` table has no comment column.
- **Section header** "INCOMES" (0.6875rem 700, letter-spacing 0.07em,
  `var(--success)`) + count badge + age note + refresh icon button on right.
- **Year bucket**: small uppercase year label left, per-currency totals right
  in `var(--font-num)`. Not sticky — plain header per group.
- **Income row** (`<IncomeRow>`):
  - 4px left border `var(--success)`, radius `0 10px 10px 0`.
  - Two lines: top = month name + year (e.g. `May 2026`) on the left + amount
    on the right (`+540 EUR`, the `+` and number in `var(--success)`, the
    currency code in `var(--muted)`) — displayed amount is
    `income.amount` in `settings.accounting_currency`.
  - Bottom = `var(--font-num)` 0.72rem repeating the YYYY-MM for
    scan-readability, e.g. `2026-05`.
  - Swipe-left reveals a green Edit panel (via `useSwipeRow`).
- **Action bar** (sticky bottom): one big green Save button. No QR button.

### B · Amount focused + keyboard

Same screen with green caret/border and iOS keyboard up. `<KeyboardSaveBar>`
floats above keyboard (reuse as-is; pass green styling via prop). Action bar
hidden behind keyboard.

### C · Currency picker open

Tapping the currency pill opens `<CurrencyPicker>` in a popover below it
(same dimensions and shadow as `ExpenseForm.vue`'s `.currency-picker-wrap`).

### D · Row swiped (reveal Edit)

One row offset 84px left, exposing the green `Edit` panel. Identical
mechanism to `ExpenseRow.vue` via `useSwipeRow`.

### E · Edit sheet

Bottom-sheet modal, same chrome as `<ExpenseEditSheet>`:
- Scrim + sheet sliding up from bottom.
- 36×4 drag handle, eyebrow `EDIT INCOME`, X button top-right.
- Hero row: currency pill + amount input + month display (read-only — year
  and month cannot change via edit; only amount and currency can).
- No comment field.
- Footer: outline Delete button (red text `#fca5a5`) + filled Save button
  (`var(--success)`).

### F · Empty (first run)

Form on top, empty-state card: 44×44 green circle with `TrendingUp` icon,
"No incomes yet" heading (0.95rem 600 `--text`), one-line subtitle
(0.82rem `--muted`, max-width 240px). Dashed border (`var(--border)`),
`var(--field)` bg, 3rem vertical padding.

### G · Offline (read-only)

- The standard offline banner in `App.vue` shows
  `"Offline — incomes can't be added or edited"` when `tab === 'income'`.
  Add this case to the existing `offlineMessage` computed in `App.vue`.
- The form card renders at 0.55 opacity with `pointerEvents: none`.
- Save button switches to `var(--surface-2)` bg + `var(--muted)` text,
  `cursor: not-allowed`, no shadow.
- Row swipe still works visually but the Edit panel renders in
  `var(--surface-2)` instead of `--success`; tapping it shows a toast
  `Not available offline`.

---

## Interactions & Behavior

### Form

- **Validate on save**: amount must parse as `Number > 0`. Toast
  `Enter a valid amount` on failure (existing toast store).
- **Default currency**: `useCurrencyStore().preferredCode`.
- **Default month**: current YYYY-MM (`new Date().toISOString().slice(0,7)`).
- **Submit flow**:
  1. Parse `year` and `month` (int) from the month picker value.
  2. `POST /api/incomes` with `{ year, month, amount_original, currency_original }`.
  3. Server converts currency and inserts. Returns `409` if the month already
     has an entry — show toast `Income for this month already exists`.
  4. On success: `currency.setLastUsed(code)`, reset form (keep month =
     current), call `incomeStore.reset()` + `incomeStore.loadNextPage()` to
     refresh the list.
  5. On failure (non-409): toast the error, keep form contents.

### List

- **Initial load**: hydrate from `localStorage` synchronously. If `isStale()`
  (dirty OR >24h old), call `loadNextPage()`.
- **Pagination**: 20 per page, infinite scroll via `IntersectionObserver`
  (copy pattern from `ReviewView.vue` — sentinel `<div>` at bottom,
  `rootMargin: '120px'`, only fires if `!loading && hasMore && isOnline`).
- **Sort**: server returns descending by `year DESC, month DESC`.
- **Year grouping**: derived on the fly via `computed` from the flat items
  array — do not store grouped data.
- **Refresh icon**: `reset()` + `loadNextPage()`. Disabled if offline or
  loading.

### Swipe / edit

- Reuse `useSwipeRow({ panelWidth: 84, commitOver: 60, onPrimary: () => emit('tap') })`.
- Tap or commit-swipe → open `<IncomeEditSheet :open :income>`.
- On save → `PATCH /api/incomes/{year}/{month}` (only
  `amount_original`/`currency_original`), then `reset()` + `loadNextPage()`.
- On delete → confirm dialog, `DELETE /api/incomes/{year}/{month}`, then
  `reset()` + `loadNextPage()`.
- Offline: tapping row or Edit → toast `Not available offline`.

### Header tab

```html
<button class="seg-btn seg-income" :class="{ active: tab === 'income' }"
        @click="$emit('update:tab', 'income')">
  <TrendingUp :size="20" />
</button>
```

New CSS: `.seg-income` mirrors `.seg-add` dimensions (50×38) but uses
`--success` for bg/shadow/active. Route in `App.vue`:
`<IncomeView v-else-if="tab === 'income'" />`.

---

## State Management

New Pinia store: `stores/income.js`. Modeled on `stores/review.js`.

```js
// stores/income.js
import { defineStore } from "pinia";
import { ref } from "vue";
import { listIncomes, createIncome, updateIncome, deleteIncome } from "../api/income.js";
import { useStaleCache } from "../composables/useStaleCache.js";
import { useToastStore } from "./toast.js";

const CACHE_KEY   = "dinary:income:v1";
const DIRTY_KEY   = "dinary:income:dirty";
const FETCHED_KEY = "dinary:income:fetchedAt";

export const useIncomeStore = defineStore("income", () => {
  const { dirtyFlag, lastFetchedAt, markDirty, stampFresh,
          isStale, readCache, writeCache, clearCache } = useStaleCache({
    dirtyKey: DIRTY_KEY, fetchedKey: FETCHED_KEY, dataKey: CACHE_KEY,
  });

  const cached    = readCache() || {};
  const items     = ref(cached.items   ?? []);
  const hasMore   = ref(cached.hasMore ?? true);
  const page      = ref(cached.page    ?? 0);
  const loading   = ref(false);
  const fromCache = ref(Array.isArray(cached.items) && cached.items.length > 0);
  const openRowId = ref(null);

  function _persist() {
    writeCache({ items: items.value, hasMore: hasMore.value, page: page.value });
  }

  async function loadIfNeeded() {
    if (isStale()) { reset(); await loadNextPage(); }
  }

  async function loadNextPage() { /* paginate, dedupe by year+month, stampFresh on success */ }

  // All writes reset and reload the list after server confirms.
  async function add(payload)        { /* POST, on 409 toast "already exists"; on success reset()+loadNextPage() */ }
  async function patch(year, month, payload) { /* PATCH /{year}/{month}, on success reset()+loadNextPage() */ }
  async function remove(year, month) { /* DELETE /{year}/{month}, on success reset()+loadNextPage() */ }
  function setOpenRow(key)           { openRowId.value = key; }  // key = `${year}-${month}`
  function reset()                   { /* zero items/page/hasMore, clearCache */ }

  return { items, hasMore, page, loading, fromCache, openRowId,
           dirtyFlag, lastFetchedAt,
           loadIfNeeded, loadNextPage, add, patch, remove, setOpenRow, reset };
});
```

### Cache rules

| Trigger | Action |
|---|---|
| View opens, cache empty / stale (>24h) / dirty | Fetch page 1 |
| View opens, cache fresh and not dirty | Render from cache, no fetch |
| User creates income (POST 2xx) | `reset()` + `loadNextPage()` |
| User edits income (PATCH 2xx) | `reset()` + `loadNextPage()` |
| User deletes income (DELETE 2xx) | `reset()` + `loadNextPage()` |
| User taps refresh icon | `reset()` + `loadNextPage()` |
| Offline | Render cache; disable form and edit; do not queue |

---

## API

### `Income` shape

```ts
{
  year:   number,   // e.g. 2026
  month:  number,   // 1–12
  amount: number,   // always in accounting_currency (EUR)
}
```

No `id` — the natural key `(year, month)` is used in all path parameters.
No `currency_original`, `amount_original`, or `comment` — the table does not
store them.

### Endpoints

```
GET    /api/incomes?page=1&page_size=20
       → { items: Income[], has_more: bool }
       Ordered year DESC, month DESC.
       No `total` field.

POST   /api/incomes
       body: { year: int, month: int,
               amount_original: number, currency_original: string }
       → Income (201)
       409 if (year, month) already exists.
       Server converts currency to accounting_currency via NBS rate.
       Enqueues income_logging_jobs row on success.

PATCH  /api/incomes/{year}/{month}
       body: { amount_original?: number, currency_original?: string }
       → Income
       404 if row not found.
       Recomputes amount when amount_original or currency_original changes.
       Replaces income_logging_jobs row (upsert) on success.

DELETE /api/incomes/{year}/{month}
       → 204
       404 if row not found.
       income_logging_jobs row deleted via CASCADE.
```

### `main.py` change

```python
from dinary.api import income          # add to existing import block
# …
app.include_router(income.router)      # add after existing include_router calls
```

### Remove `total` from `GET /api/expenses`

The `list_expenses_sync` function currently returns `"total": total` in its
dict. Remove that key. Update `ReviewView.vue` / `review.js` if they consume
it (grep for `\.total` and `data.total`).

---

## Google Sheets — "Income" tab

When `settings.sheet_logging_enabled` is true, each successful income write
enqueues a row in `income_logging_jobs`. The existing background drain task in
`background/sheet_logging/task.py` calls `drain_income_pending()` from
`income_sheet_logging.py` on each sweep (add the call after the existing
`drain_pending()` call).

### "Income" worksheet column layout

Rows are written to a worksheet named **"Income"** in the same logging
spreadsheet (created automatically if absent). One sheet row per calendar
month per year.

| Col | Content | Notes |
|-----|---------|-------|
| A | First day of the income's month (`YYYY-MM-DD`, `USER_ENTERED`) | Underlying date serial retains the year; same as expense col A |
| B | App-currency amount (RSD) | If `accounting_currency == app_currency` use `income.amount` verbatim; otherwise convert at NBS rate for the first day of that month |
| C | EUR formula `=IF(E{r}="","",B{r}/E{r})` | Sheet-side approximation |
| D | Manual EUR↔RSD rate (set-if-missing) | Same as expense col H |
| E | Month number 1–12 (literal) | Fast month scan |
| F | Idempotency marker — `"{year}-{month}"` string | Before each write check col F; if already equal skip the write |

### Drain logic (`income_sheet_logging.py`)

1. Find the worksheet named `"Income"`; create it (as a new tab) if absent.
2. For each pending `income_logging_jobs` row, load the matching `income` row.
3. Identify the target sheet row: search col A for the first-of-month date
   (year-aware, using the same `fetch_row_years` helper the expense drain
   uses).
4. If found: update cols B, D (set-if-missing), F in one batch write.
5. If not found: append a new row.
6. Idempotency check: if col F already equals `"{year}-{month}"` and col B
   already holds the same amount, mark job done without writing.
7. Errors follow the same circuit-breaker pattern as `drain_pending`
   (`_is_transient`, exponential back-off, poison on permanent error).

---

## Backend Tests (`tests/test_income.py`)

Required for every new function. Minimum coverage:

- `insert_income`: happy path, duplicate (year, month) raises expected error.
- `update_income`: amount recompute, 404 on unknown row.
- `delete_income`: happy path, 404 on unknown row.
- `list_incomes`: pagination, `has_more` boundary, descending sort.
- API via `TestClient`: `POST /api/incomes` (201 + 409), `PATCH`, `DELETE`,
  `GET`.
- Currency conversion: passthrough when currency == accounting_currency;
  NBS-rate path mocked.
- Income-sheet drain: append-new-row path, update-existing-row path,
  idempotency skip.

---

## Design Tokens

All from `dinary/webapp/src/assets/base.css`. Income view uses:

| Token | Used for |
|---|---|
| `--success` `#22c55e` | All income accents (pill, button, row border, amount, segment) |
| `#04140a` | Text on `--success` buttons/pills |
| `--bg` | App background |
| `--surface` | Header, form card, edit sheet |
| `--surface-2` | Disabled buttons, offline edit panel |
| `--text` | Primary text |
| `--muted` | Secondary text, currency code |
| `--muted-2` | Italic placeholder, cache age |
| `--warning` `#f59e0b` | Offline banner accent |
| `--field` | Count badge |
| `--border` | All hairlines |
| `--border-strong` | Sheet drag handle, popover border |
| `--font-num` | Amounts, currency code, month/year |

---

## Assets

No new assets. Icons (all in `lucide-vue-next`):

`TrendingUp`, `Save`, `Calendar`, `Pencil`, `Trash2`, `X`, `RefreshCw`, `WifiOff`

---

## Checklist for the implementer

- [ ] Migration `0005_income_logging.sql` + rollback: add `income_logging_jobs`
      table only (income table unchanged)
- [ ] `src/dinary/db/income.py`: `insert_income`, `update_income`,
      `delete_income`, `list_incomes`, `get_income_by_year_month`
- [ ] `src/dinary/api/controllers/income.py`: Pydantic models +
      controller functions with currency conversion (same pattern as
      `create_expense_sync`) + `income_logging_jobs` enqueueing
- [ ] `src/dinary/api/income.py`: FastAPI router with the 4 endpoints
- [ ] `src/dinary/main.py`: import and `include_router(income.router)`
- [ ] `src/dinary/background/sheet_logging/income_sheet_logging.py`:
      `drain_income_pending()` writing to the "Income" worksheet
- [ ] Wire `drain_income_pending()` into `sheet_logging/task.py`'s sweep loop
- [ ] `tests/test_income.py`: all backend tests (see section above)
- [ ] Remove `total` from `list_expenses_sync` return dict; grep for
      consumers in frontend and remove
- [ ] `dinary/webapp/src/api/income.js`: `listIncomes`, `createIncome`,
      `updateIncome`, `deleteIncome` via existing `_request.js`
- [ ] `dinary/webapp/src/stores/income.js`: Pinia store with 24h cache, reset
      after every write
- [ ] `dinary/webapp/src/components/IncomeForm.vue`: currency pill + amount +
      month picker (`type="month"`); no comment field
- [ ] `dinary/webapp/src/components/IncomeRow.vue`: green left border, swipe,
      two-line layout (month name + year | accounting-currency amount)
- [ ] `dinary/webapp/src/components/IncomeEditSheet.vue`: currency pill +
      amount + read-only month display, Delete + Save footer; no comment field
- [ ] `dinary/webapp/src/views/IncomeView.vue`: form + list + observer
      pagination
- [ ] `HeaderSegmented.vue`: 4th `income` segment (`--success` variant of
      `.seg-add`)
- [ ] `App.vue`: add `income` route + add `tab === 'income'` case to
      `offlineMessage` → `"Offline — incomes can't be added or edited"`
- [ ] Disable form + swipe-edit when `!isOnline`
- [ ] Verify: open view twice offline → no network call; after write → list
      refetches; after >24h → single fetch on open; POST for existing month →
      409 toast

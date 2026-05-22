# Handoff: Add Income

## Overview

A new top-level view in the Dinary webapp for recording **incomes** (salary,
refunds, freelance payments, gifts). The view sits between **Add expense** and
**Review** in the segmented header. It has two parts:

1. A compact form at the top (amount + currency + comment) for entering a new income.
2. An infinite-scroll list of past incomes below, grouped by month, with
   swipe-to-edit on each row.

Offline, the view behaves like Review: read-only, no queueing.

## About the Design Files

`Add Income.html` in this bundle is a **design reference** — a self-contained
standalone HTML prototype. It is not production code and should not be copied
into the app directly.

The task is to **recreate these designs inside the existing Dinary webapp
(`dinary/webapp/`)**, which is a Vue 3 + Pinia + Vite SPA, reusing the existing
components and patterns wherever possible. Most of the building blocks already
exist — this is mostly composition + a new Pinia store.

To preview the prototype: open `Add Income.html` in a browser. The canvas is
zoomable / pannable; double-click any phone artboard to focus it.

## Fidelity

**High-fidelity.** Colors, typography, spacing, and layout exactly match the
existing Dinary v0.9 design tokens in `dinary/webapp/src/assets/base.css`.
Reuse the existing CSS variables (`--success`, `--surface`, `--field`,
`--border`, `--font-num`, etc.) — do not introduce new values.

## Where it lives in the codebase

```
dinary/webapp/src/
├── api/income.js             ← NEW
├── components/
│   ├── IncomeForm.vue        ← NEW
│   ├── IncomeRow.vue         ← NEW
│   ├── IncomeEditSheet.vue   ← NEW
│   ├── CurrencyPicker.vue    ← REUSED as-is
│   ├── KeyboardSaveBar.vue   ← REUSED as-is
│   └── HeaderSegmented.vue   ← MODIFIED (add income tab)
├── composables/useStaleCache.js  ← REUSED as-is
├── stores/income.js          ← NEW
├── views/IncomeView.vue      ← NEW
└── App.vue                   ← MODIFIED (route to IncomeView)
```

## Screens / Views

The prototype shows **7 phone states** + 1 cache-flow diagram. All run inside a
390×780 iOS frame, dark mode.

### A · Idle (cache fresh, online)
The default render when the cache is &lt;24h old and not dirty.

- **Header** (sticky, `var(--surface)`, 1px bottom border `var(--surface-2)`):
  - Left: `Dinary v0.9` title (1.25rem, 600 weight) + queued badge if applicable.
  - Right: `<HeaderSegmented>` with **4** segments now: `Add` (red), **`Income`
    (green, new)**, `Review`, `LLM`. The income segment uses `--success` for
    its active/inactive states the way Add uses `--accent`.
- **Form** (`<IncomeForm>`):
  - Card surface (`var(--surface)`, radius 12, padding 1.25rem).
  - **Hero row**: currency pill (left) + amount input (flex 1) + date (right),
    gap 8px. Layout identical to `ExpenseForm.vue` `.hero-row`.
  - Currency pill: 0.3rem 0.6rem padding, `var(--success)` background,
    `#04140a` text, radius 8, font 0.78rem 700 in `var(--font-num)`.
    **Note:** the expense pill uses `var(--accent)`; here we swap to
    `var(--success)` so the screen reads positive.
  - Amount input: 64px tall, font 2rem 500 in `var(--font-num)`, right-aligned,
    1px bottom border (`var(--border)` resting → `var(--success)` focused —
    again, swap from `--accent`).
  - Date: 14px Calendar icon + native date input, 0.8rem in `--muted`.
  - **Comment row**: full-width `<input>` with placeholder `Comment (optional)`.
    Field surface (`var(--field)`), 1px `var(--border)`, radius 8, padding
    0.55rem 0.75rem, 0.9rem font.
- **Section header** "INCOMES" (0.6875rem 700, letter-spacing 0.07em,
  `var(--success)`) + count badge (1px pill, `var(--field)` bg) + age note
  (`updated 3h ago` in `var(--muted-2)`) + refresh icon button on the right.
- **Month bucket**: small uppercase label on the left, per-currency totals on
  the right in `var(--font-num)`. Sticky? **No** — just a header per group.
- **Income row** (`<IncomeRow>`):
  - 4px left border in `var(--success)`, radius `0 10px 10px 0`.
  - Two lines: top = comment (or italic "no comment" placeholder) + amount on
    the right (`+540 EUR`, the `+` and number in `var(--success)`, the currency
    code in `var(--muted)`).
  - Bottom = full date `18 May, 2026` in `var(--font-num)` 0.72rem.
  - Swipe-left reveals a green Edit panel (same gesture/animation as
    `ExpenseRow.vue` via `useSwipeRow`).
- **Action bar** (sticky bottom): one big green Save button. **No QR button**
  here (incomes have no receipts).

### B · Amount focused + keyboard
Same screen but the amount input has the green caret/border and the iOS
keyboard is up. The standard `<KeyboardSaveBar>` floats above the keyboard
(component already exists — reuse it; just pass green styling via prop or
overrride). Action bar is hidden behind the keyboard.

### C · Currency picker open
Tapping the green currency pill opens `<CurrencyPicker>` below it in a popover
(absolute, top `calc(100% + 6px)`, `var(--surface)` bg, 1px
`var(--border-strong)`, radius 10, padding 0.6rem, min-width 260px, the same
shadow as `ExpenseForm.vue`'s `.currency-picker-wrap`). Selected code is
highlighted in `var(--success)` (the picker component already handles this —
it uses `--accent`; either parameterize it or rely on it staying red and just
visually reading as "selected").

### D · Row swiped (reveal Edit)
One row is offset 84px left, exposing the green `Edit` panel underneath.
Identical mechanism to `ExpenseRow.vue` — extract that swipe logic via
`useSwipeRow` composable, which already exists.

### E · Edit sheet
Bottom-sheet modal, same chrome as `<ExpenseEditSheet>`:
- Scrim (`rgba(0,0,0,0.55)`) + sheet sliding up from the bottom.
- 36×4 drag handle, eyebrow `EDIT INCOME`, X button top-right.
- Hero row reused (currency pill + amount + date).
- Comment field.
- Footer: outline Delete button (red text `#fca5a5`) + filled Save button
  (`var(--success)`).
- No category / tags / event blocks (incomes don't have them).

### F · Empty (first run)
Form on top, then an empty-state card: 44×44 green circle with the trend-up
icon, "No incomes yet" heading (0.95rem 600 `--text`), one-line subtitle
(0.82rem `--muted`, max-width 240px). Dashed border (`var(--border)`),
`var(--field)` background, 3rem vertical padding.

### G · Offline (read-only)
- The standard offline banner from `App.vue` shows
  `Offline — incomes can't be added or edited`.
- Above the form, an amber callout (`rgba(245,158,11,0.06)` bg + 0.25 alpha
  border): wifi-off icon + "Offline — showing cached incomes from
  **22 May, 09:14**. New incomes can't be added until you're back online."
- The form card is rendered at 0.55 opacity with `pointerEvents: none`.
- The Save button switches to `var(--surface-2)` bg + `var(--muted)` text,
  `cursor: not-allowed`, no shadow.
- Row swipe still works visually but the Edit panel renders in
  `var(--surface-2)` instead of `--success`, and tapping it shows a toast
  `Not available offline` (mirror `ReviewView.vue`).

## Interactions & Behavior

### Form
- **Validate on save**: amount must parse as `Number > 0`. Show toast
  `Enter a valid amount` on failure (existing toast store).
- **Default currency**: `useCurrencyStore().preferredCode` (whatever the user
  last picked across the app — same source as expenses).
- **Default date**: today's ISO date (`new Date().toISOString().slice(0,10)`).
- **Submit flow**:
  1. POST to `/api/incomes` with `{ amount, currency, comment, date }`.
  2. On success: prepend the returned row into the store's cached array,
     `currency.setLastUsed(code)`, reset the form (keep date = today),
     `markDirty()` then `stampFresh()` on the next list refetch.
  3. On failure: toast the error message, keep form contents.
- **Auto-flush queue**: NOT applicable — incomes are not queued offline.

### List
- **Initial load**:
  - Hydrate from `localStorage` cache synchronously (same pattern as
    `stores/review.js` — read inside the `defineStore` factory).
  - If `isStale()` (dirty OR &gt;24h old), call `loadNextPage()`.
- **Pagination**: 20 per page, infinite scroll via `IntersectionObserver`
  (copy the pattern in `ReviewView.vue` exactly — sentinel `<div>` at the
  bottom, `rootMargin: '120px'`, only fires if `!loading && hasMore && isOnline`).
- **Sort**: server returns descending by date; client preserves order.
- **Month grouping**: derived on the fly via `computed` — do not store grouped
  data, store the flat array.
- **Refresh icon**: `reset()` + `loadNextPage()`. Disabled if offline or
  already loading.

### Swipe / edit
- Reuse `useSwipeRow({ panelWidth: 84, commitOver: 60, onPrimary: () => emit('tap') })`.
- Tap or commit-swipe → open `<IncomeEditSheet :open :income>`.
- On save → PATCH `/api/incomes/:id`, patch the cached row in place, refresh
  the affected month's total (computed re-runs).
- On delete → confirm dialog, DELETE, splice from cached array.
- Offline: tapping the row or Edit button → toast `Not available offline`.

### Header tab
- Add a fourth segment in `HeaderSegmented.vue` between `Add` and `Review`:
  ```
  <button class="seg-btn seg-income" :class="{ active: tab === 'income' }"
          @click="$emit('update:tab', 'income')">
    <TrendingUp :size="20" />
  </button>
  ```
- New CSS: `.seg-income` mirrors `.seg-add` dimensions (50×38) but uses
  `--success` instead of `--accent` for bg/shadow/active.
- Route in `App.vue`: `<IncomeView v-else-if="tab === 'income'" />`.

## State Management

New Pinia store: `stores/income.js`. Lift it almost verbatim from
`stores/review.js` (the expenses half), dropping rules/doubtful concepts.

```js
// stores/income.js
import { defineStore } from "pinia";
import { ref } from "vue";
import { listIncomes, createIncome, updateIncome, deleteIncome } from "../api/income.js";
import { useStaleCache } from "../composables/useStaleCache.js";
import { useToastStore } from "./toast.js";

const CACHE_KEY    = "dinary:income:v1";
const DIRTY_KEY    = "dinary:income:dirty";
const FETCHED_KEY  = "dinary:income:fetchedAt";

export const useIncomeStore = defineStore("income", () => {
  const { dirtyFlag, lastFetchedAt, markDirty, stampFresh, bumpFetchTime,
          isStale, readCache, writeCache, clearCache } = useStaleCache({
    dirtyKey: DIRTY_KEY, fetchedKey: FETCHED_KEY, dataKey: CACHE_KEY,
  });

  const cached = readCache() || {};
  const items    = ref(cached.items   ?? []);
  const hasMore  = ref(cached.hasMore ?? true);
  const page     = ref(cached.page    ?? 0);
  const loading  = ref(false);
  const fromCache = ref(Array.isArray(cached.items) && cached.items.length > 0);
  const openRowId = ref(null);

  function _persist() { writeCache({ items: items.value, hasMore: hasMore.value, page: page.value }); }

  async function loadIfNeeded() {
    if (isStale()) { reset(); await loadNextPage(); }
  }

  async function loadNextPage() { /* paginate, dedupe by id, stampFresh on success */ }
  async function add(payload)    { /* POST, prepend to items, markDirty, _persist */ }
  async function patch(id, payload) { /* PATCH, replace in place, _persist */ }
  async function remove(id)      { /* DELETE, splice, _persist */ }
  function setOpenRow(id)        { openRowId.value = id; }
  function reset()               { /* zero out + clearCache */ }

  return { items, hasMore, page, loading, fromCache, openRowId,
           dirtyFlag, lastFetchedAt,
           loadIfNeeded, loadNextPage, add, patch, remove, setOpenRow, reset };
});
```

### Cache rules (this is the load-bearing logic)

The list is the only thing fetched. Cache it aggressively:

| Trigger | Action |
|---|---|
| View opens, cache empty or stale (`>24h` old) or dirty | Fetch page 1 |
| View opens, cache fresh and not dirty | **Render from cache, no fetch** |
| User adds an income | Optimistically prepend, `markDirty()`, POST, on success `stampFresh()` |
| User edits an income | Patch the cached row in place, `markDirty()`, PATCH, `stampFresh()` |
| User deletes an income | Splice from cache, `markDirty()`, DELETE, `stampFresh()` |
| User pulls refresh / taps the refresh icon | `reset()` + `loadNextPage()` (manual force) |
| Offline | Render cache. Disable form & edit. **Do not** queue. |

`useStaleCache` already implements `isStale()` = `dirtyFlag || !lastFetchedAt || age > 24h`. Reuse it.

`stampFresh()` clears dirty AND bumps `lastFetchedAt`; call it on every
successful fetch / write to the server. `bumpFetchTime()` updates the
timestamp without clearing dirty (use it when you fetched a later page and the
data is still potentially stale).

## Design Tokens

All tokens come from `dinary/webapp/src/assets/base.css`. Do not invent new
ones. The income view uses:

| Token | Value | Used for |
|---|---|---|
| `--bg` | `#1a1a2e` | App background |
| `--surface` | `#16213e` | Header, form card, edit sheet |
| `--surface-2` | `#0f3460` | Disabled buttons, picker chips |
| `--success` | `#22c55e` | All "income" accents (pill, button, row border, amount, segment) |
| `--text` | `#eeeeee` | Primary text |
| `--muted` | `#94a3b8` | Secondary text, currency code |
| `--muted-2` | `#64748b` | Tertiary (italic placeholder, cache age) |
| `--warning` | `#f59e0b` | Offline banner accent |
| `--field` | `rgba(255,255,255,0.04)` | Comment field, count badge |
| `--border` | `rgba(255,255,255,0.08)` | All hairlines |
| `--border-strong` | `rgba(255,255,255,0.12)` | Sheet drag handle, popover border |
| `--font-num` | `"JetBrains Mono", ui-monospace, …` | Amounts, currency code, dates |

Income-specific dark-text-on-green color: `#04140a` (used for text on
`--success` buttons/pills, to mirror how the expense pill uses white on
`--accent`).

Sizes match existing components:
- Card radius: `12`
- Row radius: `10` (with `0 10px 10px 0` when there's a left accent)
- Button radius: `8` (small), `10`–`12` (large)
- Section label: `0.6875rem 700` with `letter-spacing: 0.07em` uppercase
- Hero amount: `2rem 500` in `--font-num`
- Row primary text: `0.9375rem 600`
- Row meta: `0.72rem` in `--font-num`

## API

New endpoints (see `dinary/webapp/src/api/income.js`):

```
GET    /api/incomes?page=1&page_size=20
       → { items: Income[], has_more: bool }

POST   /api/incomes
       body: { amount: number, currency: string, comment: string|null, date: "YYYY-MM-DD" }
       → Income

PATCH  /api/incomes/:id
       body: partial of the POST body
       → Income

DELETE /api/incomes/:id
       → 204
```

`Income` shape:
```ts
{ id: number, amount: number, currency: string, comment: string|null, date: "YYYY-MM-DD", created_at: ISO }
```

Wire through the existing `api/_request.js` helper so auth + base URL + error
handling come for free.

## Assets

No new assets. Icons used (all already in `lucide-vue-next`, which is
already a dep):

- `TrendingUp` — header tab + empty state
- `Save` — action bar + edit sheet
- `Calendar` — date field
- `Pencil` — swipe-edit panel
- `Trash2` — edit sheet delete button
- `X` — edit sheet close
- `RefreshCw` — list refresh
- `WifiOff` — offline banner

## Files in this bundle

| File | What it is |
|---|---|
| `Add Income.html` | Self-contained prototype — open in any browser. |
| `README.md` | This document — the spec. |

The Vue templates you're cloning are already in the repo:
`dinary/webapp/src/components/ExpenseForm.vue`, `ExpenseRow.vue`, and
`ExpenseEditSheet.vue`.

## Checklist for the implementer

- [ ] Add `IncomeView.vue` with form + list + observer-driven pagination
- [ ] Build `IncomeForm.vue` cloning `ExpenseForm.vue`'s hero row, dropping
      category / event / tag blocks, swapping `--accent` for `--success`
- [ ] Build `IncomeRow.vue` cloning `ExpenseRow.vue`'s swipe mechanics with
      a green left border + green amount + simplified body
- [ ] Build `IncomeEditSheet.vue` cloning `ExpenseEditSheet.vue` minus the
      category / tags / event / scope / rule blocks; add a Delete button
- [ ] Add `stores/income.js` modeled on `stores/review.js` (cache + 24h TTL)
- [ ] Add `api/income.js` with the 4 endpoints above
- [ ] Add the `income` tab to `HeaderSegmented.vue` (`--success` variant of
      `.seg-add`) and route it in `App.vue`
- [ ] Wire the offline banner copy:
      `tab === 'income' ? "Offline — incomes can't be added or edited" : …`
- [ ] Disable the form + edit sheet + swipe-edit panel when `!isOnline`,
      mirroring `ReviewView.vue`'s pattern
- [ ] Verify cache rules: open the view twice in a row offline → no network
      call; after `>24h` or after add/edit → fetch fires once

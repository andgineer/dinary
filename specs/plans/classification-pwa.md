# PWA — Review Page & Expense Editing

All client-side work. Implement after `classification-backend-done.md` is merged.

Sections in implementation order: swipe composable → sheets → row components → stores
→ API clients → views → tests.

---

## Design

### Review page layout

```
NEEDS REVIEW  [3]            by impact   [↻]
┌──────────────────────────────────────────┐
│ Pedigree adult          Lidl Beograd     │  ← RuleRow (doubtful)
│ [maybe]  Еда  [🐾 собака]  [✓ Хозтовары] [Еда] [✎] │
└──────────────────────────────────────────┘
  ... more doubtful rules ...
  ... certain rules ...

RECENT EXPENSES                            [↻]
┌──────────────────────────────────────────┐
│ 18 May · Lidl Beograd · 250 RSD         │  ← ExpenseRow
│ Хозтовары · [🐾 собака]                 │
└──────────────────────────────────────────┘
  ... more expenses, infinite scroll ...
```

### ExpenseEditSheet — shared editor for both sections

```
Category
  [Хозтовары  ▾]  (tap → opens CategorySheet with suggestions if rule context)

Tags
  [✓ собака]  [☐ ЗОЖ]  [☐ профессиональное]  …

Event
  [None  ▾]

────────────────────────────────────────────
Scope: [Single  ▾]          (receipt-backed only)
[☐ Also update rule]        (receipt-backed with has_rule only)
────────────────────────────────────────────
[Cancel]                              [Save]
```

---

## 1. `webapp/src/composables/useSwipeRow.js` (new)

Gesture composable shared by `RuleRow` and `ExpenseRow`.

- [ ] Export `useSwipeRow({ panelWidth, commitOver = 80, onPrimary })` returning:
  `{ sliderEl, phase, isCommit, isOpen, onPointerDown, onPointerMove, endDrag, shouldFireTap, close, open }`
- [ ] Drag state lives in a plain object (not reactive) so `pointermove` doesn't trigger
  reactivity on every frame
- [ ] Axis lock: after 8px movement, commit to horizontal or vertical — vertical cancels
  the swipe and lets the scroll pass through (`touch-action: pan-y` on the slider element)
- [ ] Snap logic on release: past commit threshold → fire `onPrimary()` then close;
  past half panel → snap open; otherwise snap closed
- [ ] `shouldFireTap()`: returns true when `|visual| <= 4px` (near-zero drag = tap, not swipe)
- [ ] CSS required on the row wrapper (apply in each component, not the composable):
  - `.row-wrap`: `position: relative; overflow: hidden; background: var(--bg); border-radius: 10px`
  - `.row-panel`: `position: absolute; top: 0; bottom: 0; right: 0; display: flex; align-items: stretch; pointer-events: none` — set `pointer-events: auto` when open
  - `.row-slider`: `position: relative; z-index: 1; background-color: var(--bg); touch-action: pan-y; user-select: none`
  - **Critical**: slider must use `background-color` for the solid base and `background-image`
    for any tint overlay — mixing them in the `background` shorthand causes browsers to
    discard the whole declaration and the panel buttons bleed through

---

## 2. `webapp/src/components/CategorySheet.vue` (new)

Reusable bottom sheet for picking a category. Used by `ExpenseEditSheet` and `ExpenseForm`.

- [ ] Props: `open: Boolean`, `suggestions: Array` (list of `{id, name}`, may be empty),
  `title: String` (default `"Select category"`)
- [ ] Emits: `select(categoryId: Number)`, `close`
- [ ] Search `<input>` at top, auto-focused on open via `watch(open)`
- [ ] When `query` empty: show SUGGESTIONS section (if `suggestions.length > 0`) as pill
  buttons + full group grid (2-column, same style as current `CorrectionSheet`)
- [ ] When `query` non-empty: hide suggestions and group sections; show flat filtered list
  `"Group › Category"` matching anywhere in name (case-insensitive)
- [ ] Tapping suggestion pill, grid button, or flat result → emit `select(id)` then `close`
- [ ] ✕ button clears query; Escape clears if non-empty, closes if empty
- [ ] Bottom sheet layout: drag handle, scrim, slide-up transition — same CSS as
  `CorrectionSheet`
- [ ] Suggestions pills style: same `.cat-btn` as `CorrectionSheet` with `is-suggested`
  (blue-tinted border, Sparkles icon)

---

## 3. `webapp/src/components/CategoryQuickPicks.vue` (new)

Simple pill row for `ExpenseForm`. No sheet logic of its own.

- [ ] Props: `categories: Array` (list of `{id, name}`)
- [ ] Emits: `select(categoryId)`, `search`
- [ ] Renders pills in a `flex-wrap` row + a `[🔍]` search button at the end
- [ ] Tapping a pill → `emit('select', id)` immediately; tapping search → `emit('search')`

---

## 4. `webapp/src/components/ExpenseEditSheet.vue` (new)

Single editor for both doubtful rules and recent expenses.

- [ ] Props: `open: Boolean`, `expense: Object | null`, `suggestions: Array`
  (alternative categories — populated from rule context, empty for plain expenses),
  `ruleItem: Object | null` (the rule item when opened from `RuleRow`; null for plain expenses)
- [ ] Emits: `close`
- [ ] On open: pre-fill `selectedCategoryId` from `expense.category_id` (or
  `ruleItem.category_id` when expense is null); pre-fill
  `selectedTagIds: Set<number>` from `expense.tags` (or `ruleItem.tags`); pre-fill `selectedEventId` from
  `expense.event_id` (null when expense is null)
- [ ] **Category block**: current category name as a tappable chip → opens
  `<CategorySheet :open :suggestions>` on tap; on `select` → update `selectedCategoryId`
- [ ] **Tags block**: render all active tags from `catalogStore.tags` as toggle chips;
  tapping toggles membership in `selectedTagIds`
- [ ] **Event block**: dropdown or inline list of active events from `catalogStore.events`;
  selecting an event unions its `auto_tags` tag IDs into `selectedTagIds`
- [ ] **Scope selector**: visible only when `expense.receipt_id != null`; options: Single /
  Last month / This year / All history; default Single
- [ ] **"Also update rule" checkbox**: visible only when `expense.has_rule === true`
- [ ] Save: call `reviewStore.updateExpense(expense.id, { category_id: selectedCategoryId,
  tag_ids: [...selectedTagIds], event_id: selectedEventId,
  clear_event: selectedEventId === null, scope, update_rule })`
  then emit `close`
- [ ] When opened from a rule row (`expense` is null): save calls
  `reviewStore.correct(ruleItem, selectedCategoryId, "all")` (uses the `ruleItem` prop)
  for category, then `editExpense` separately for tags with `update_rule: true`

---

## 5. `webapp/src/components/ExpenseRow.vue` (new)

- [ ] Props: `expense: Object`
- [ ] Emits: `tap`
- [ ] Two-row card layout:
  - top: `{date} · {store} · {amount} {currency}`
  - bottom: `{category}` · tag chips · event name (if set)
- [ ] Swipe-to-act via `useSwipeRow`: single action panel `[✎ Edit]` (84px); long swipe
  fires `emit('tap')` as primary action
- [ ] On snap-open: call `reviewStore.setOpenRow(expense.id)`; watch `reviewStore.openRowId`
  — if it changes to a different id, call `close()` to snap this row shut
- [ ] Tap → `emit('tap')` (via `shouldFireTap()` guard)
- [ ] Warning left-border (4px `var(--warning)`) when `expense.confidence_level < 4`
- [ ] Tag chips: small pills, same style as RuleRow tag chips

---

## 6. `webapp/src/components/RuleRow.vue` — full redesign

Combines the swipe spec, alternative chips, and tag display.

### At-rest layout

```
┌──────────────────────────────────────────┐
│ Karamel čoko prot.čok.   Lidl Beograd   │  top: name (bold) · store (muted)
│ [maybe]  Еда  [🐾 собака]  [✓ Food] [Deli] [✎] │  bottom (doubtful only)
│ Еда                              [ › ]  │  bottom (certain)
└──────────────────────────────────────────┘
```

- [ ] Remove `.row-total` (price), `.row-sub` (count / date / currency), and the
  "current → suggested" arrow + dual-pill display from the current component
- [ ] Bottom row (doubtful): confidence pill · single category (the one Approve will commit,
  with Sparkles icon if it differs from current) · tag chips (from `item.tags`) · approve
  chips · ✎ icon
- [ ] Tag chips: rendered between confidence pill and approve area; same small-pill style
  as `ExpenseRow`; read-only (tap on ✎ to edit)
- [ ] Approve chips: green `[✓ {llmCategoryName}]` chip + up to 2 `[{alt.name}]` muted
  chips from `item.alternative_categories`; tapping any fires approve with that category id
- [ ] ✎ icon button (28×28): emits `tap`; parent `ReviewView` opens `ExpenseEditSheet`
  with `expense=null` and `suggestions=item.alternative_categories`
- [ ] Bottom row (certain): category breadcrumb + chevron `›`; chevron is visual only
- [ ] Swipe-to-act via `useSwipeRow`:
  - doubtful panel (168px): `[✎ Edit]` (84px) + `[✓ Approve]` (84px); long swipe fires
    Approve
  - certain panel (92px): `[✎ Edit]` (92px); long swipe fires `tap` emit (parent handles)
  - commit zone: primary button grows + brightens; secondary collapses
  - On snap-open: call `reviewStore.setOpenRow(item.id)`; watch `reviewStore.openRowId`
    — if it changes to a different id, call `close()` to snap this row shut
- [ ] `defineEmits(['tap', 'approve'])` where `approve` carries `{ item, categoryId }`
- [ ] `approveFromButton(e, categoryId)`: stop propagation, close swipe, emit approve
- [ ] Parent `ReviewView`: `approveItem({ item, categoryId })` uses `event.categoryId`

---

## 7. API clients

### `webapp/src/api/review.js`

- [ ] Add `getRecentExpenses()` → `GET /api/expenses/recent`

### `webapp/src/api/expenseCorrections.js`

- [ ] Add `editExpense(expenseId, payload)` → `PATCH /api/expenses/{id}` with
  `{ category_id, tag_ids, event_id, clear_event, scope, update_rule }`; returns
  `ExpenseEditResponse` (`id`, `category_id`, `category_name`, `tag_ids`, `event_id`,
  `event_name`)

---

## 8. Stores

### `webapp/src/stores/review.js`

- [ ] Add state: `expenses: ref([])`, `expensesLoading: ref(false)`,
  `expensesLoaded: ref(false)`
- [ ] Add `openRowId: ref(null)` + `setOpenRow(id)` — shared between RuleRow and
  ExpenseRow so only one row is ever swiped open at a time
- [ ] Add action `loadRecentExpenses()`: call `getRecentExpenses()`, populate `expenses`
- [ ] Add action `updateExpense(id, payload)`: call `editExpense`; on success replace the
  matching entry in `expenses` with the returned fields (`category_id`, `category_name`,
  `tag_ids`, `event_id`, `event_name`); toast on error

### `webapp/src/stores/frequentCategories.js` (new)

**Requires backend §7** — `catalogStore.frequentCategories` will be empty until
`GET /api/catalog` returns the `frequent_categories` field.

- [ ] State: `categories: ref([])`, `lastFetched: ref(null)`
- [ ] `ensureLoaded()`: if empty or `lastFetched` older than 24 h, call `useCatalogStore()`
  internally and read `frequentCategories`; otherwise no-op. Called on `ExpenseForm` mount.
- [ ] `refresh(responseData)`: called after every successful POST /api/expenses with the
  full response body; overwrites `categories` + bumps `lastFetched`

### `webapp/src/stores/catalog.js`

- [ ] Expose `frequentCategories` from catalog response `frequent_categories` field
- [ ] Expose `tags` from catalog response `tags` field
- [ ] Expose `events` from catalog response `events` field

---

## 9. `webapp/src/components/ExpenseForm.vue` — quick picks

- [ ] Add `<CategoryQuickPicks :categories="frequentCategoriesStore.categories"
  @select="onQuickPick" @search="categorySheetOpen = true" />` below amount row,
  above group→category block
- [ ] Call `frequentCategoriesStore.ensureLoaded()` on mount
- [ ] After successful POST: call `frequentCategoriesStore.refresh(response)`
- [ ] `onQuickPick(id)`: set `categoryId` + resolve `groupId` from catalog store
- [ ] Add `<CategorySheet :open="categorySheetOpen" :suggestions="[]"
  @select="onQuickPick($event); categorySheetOpen = false" @close="categorySheetOpen = false" />`
- [ ] Keep the existing group→category two-stepper for now (additive — remove later once
  quick picks are validated)

---

## 10. `webapp/src/views/ReviewView.vue` — two sections

- [ ] On mount: call `reviewStore.loadRecentExpenses()` in parallel with
  `reviewStore.loadIfNeeded()`
- [ ] Add `expenseEditOpen: ref(false)`, `editingExpense: ref(null)`,
  `editingSuggestions: ref([])`, `editingRuleItem: ref(null)`
- [ ] `openExpenseEdit(expense, suggestions = [], ruleItem = null)`: set all four state refs, open sheet
- [ ] `closeExpenseEdit()`: clear all four refs, close sheet
- [ ] Rule rows: `@tap="openExpenseEdit(null, item.alternative_categories, item)"`
- [ ] Replace `<CorrectionSheet>` with `<ExpenseEditSheet :open :expense :suggestions :ruleItem
  @close="closeExpenseEdit" />`; grep `webapp/src` for remaining `CorrectionSheet` usages
  and remove component + import only if no other file references it
- [ ] Add "RECENT EXPENSES" section header below rule list (or below the "All caught up"
  empty state)
- [ ] Render `<ExpenseRow v-for="expense in reviewStore.expenses" :expense @tap="openExpenseEdit(expense)" />`
- [ ] Infinite scroll sentinel already present — wire it to also trigger
  `reviewStore.loadRecentExpenses()` if more pagination is added later (for now 30 is enough)

---

## 11. CSS notes

- Alternative chips on RuleRow: `var(--field)` background, `var(--border)` border,
  font-size 11px — same border-radius and padding as Approve but no green tint
- Tag chips (both RuleRow and ExpenseRow): font-size 11px, `var(--field)` background,
  `var(--border)` border, with an emoji/icon prefix when icon is available
- `CategorySheet` suggestions pills: `is-suggested` style — blue-tinted border + Sparkles icon
- Warning left-border on RuleRow (doubtful) and ExpenseRow (low confidence): 4px
  `var(--warning)` on `.row-wrap`, not the slider (otherwise it slides off screen on swipe)

---

## 12. Tests

- [ ] `webapp/tests/useSwipeRow.test.js`: short swipe → revealed; long swipe → fires
  `onPrimary`; vertical swipe → no transform; `shouldFireTap` true under 4px
- [ ] `webapp/tests/CategorySheet.test.js`: search filters flat results, hides groups;
  suggestion tap emits `select`; grid tap emits `select`; clear resets query
- [ ] `webapp/tests/CategoryQuickPicks.test.js`: pill tap emits `select`; search button
  emits `search`
- [ ] `webapp/tests/ExpenseEditSheet.test.js`:
  - pre-fills category and tags from expense prop
  - tag toggle adds/removes from selection
  - event selection merges auto_tags
  - scope selector hidden when `receipt_id` is null
  - "update rule" checkbox hidden when `has_rule` is false
  - save emits correct payload
- [ ] `webapp/tests/ExpenseRow.test.js`: renders date/store/amount/category/tags; tap emits
  `tap`; swipe reveals Edit button; warning border when `confidence_level < 4`
- [ ] `webapp/tests/component-rule-row.test.js`:
  - tag chips render from `item.tags`
  - alternative chips render (max 2); each emits `approve` with correct `categoryId`
  - ✎ emits `tap`; approve chip emits `approve`
  - certain row: no approve button, no alternative chips
- [ ] `webapp/tests/frequentCategories.test.js`:
  - `ensureLoaded` populates from catalog on empty cache
  - `ensureLoaded` no-op when fresh (< 24 h); re-reads when stale (> 24 h)
  - `refresh` overwrites categories + bumps `lastFetched`
- [ ] `webapp/tests/store-review.test.js`: `updateExpense` replaces matching entry from
  response data; `setOpenRow` closes previously open row
- [ ] `webapp/tests/component-review-view.test.js`: both sections render; tapping
  ExpenseRow opens ExpenseEditSheet; tapping RuleRow opens ExpenseEditSheet with suggestions

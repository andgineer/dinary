# Handoff: Review-page editor improvements

## Overview

Three improvements to the expense editor on the **Review** view of the Dinary
webapp:

1. **Manual expenses can edit amount and currency** (today the editor is
   category / tags / event only).
2. **Manual expenses can be deleted** from the editor, with a confirmation.
3. **Receipt-backed expenses can delete the whole receipt** (cascading to every
   expense generated from it), with a confirmation that explains the cascade.

The receipt-backed editor does **not** get amount/currency editing — the
receipt is the source of truth and per-item OCR amounts shouldn't be edited
out of context.

## About the Design Files

`Review Editors.html` in this bundle is a **design reference** — a
self-contained standalone HTML prototype rendered with React. It is not
production code and should not be copied into the app directly.

The task is to **recreate these designs inside the existing Dinary webapp
(`dinary/webapp/`)**, which is a Vue 3 + Pinia + Vite SPA, by modifying the
existing `ExpenseEditSheet.vue` component and adding one new component
(`ConfirmDeleteSheet.vue`). All visual tokens already exist in
`src/assets/base.css` — reuse, do not invent.

To preview: open `Review Editors.html` in a browser. The canvas is
pan/zoom; double-click any phone artboard to focus it.

## Fidelity

**High-fidelity.** Colors, typography, spacing, and layout match the
existing Dinary v0.9 design tokens. Reuse the existing CSS variables
(`--accent`, `--surface`, `--danger`, `--field`, `--border`, `--font-num`,
etc.).

## Where it lives in the codebase

```
dinary/webapp/src/
├── api/
│   ├── expenses.js              ← MODIFIED — add deleteExpense(id)
│   └── receipts.js              ← MODIFIED — add deleteReceipt(id)
├── components/
│   ├── ExpenseEditSheet.vue     ← MODIFIED — add amount editor, Delete action
│   └── ConfirmDeleteSheet.vue   ← NEW — bottom-sheet confirm (two variants)
└── stores/
    └── review.js                ← MODIFIED — add deleteExpense / deleteReceipt
```

No view-level changes (`ReviewView.vue`) are required — the new affordances
all live inside `ExpenseEditSheet`.

## What changed (manual vs receipt-backed)

The Vue store already tracks `receipt_id` on each expense. That's the only
signal needed:

| | Manual (`receipt_id == null`) | Receipt-backed (`receipt_id != null`) |
|---|---|---|
| Edit category / tags / event | ✅ existing | ✅ existing |
| Edit scope (Only this / month / year / all) | n/a | ✅ existing |
| Edit `Also update rule` | n/a | ✅ existing (when `has_rule`) |
| **Edit amount + currency** | ✅ **NEW** | ❌ hidden |
| **Delete** | ✅ **NEW** — deletes one expense | ✅ **NEW** — deletes the whole receipt |
| `FROM RECEIPT` pill in sheet eyebrow | ❌ | ✅ **NEW** |

## Screens

The prototype shows **5 phone states** + 1 notes artboard, all in a
390×780 iOS frame, dark mode.

### A · Manual edit sheet (idle)

The sheet that opens when tapping or swipe-editing a manual expense row.

- Same scrim + bottom-sheet chrome as today's `<ExpenseEditSheet>`
  (drag handle, eyebrow `EDIT EXPENSE`, X button top-right).
- **NEW: `AMOUNT` field block** at the top of the sheet body. Mirrors the
  hero row from `ExpenseForm.vue`:
  - Currency pill (left): 0.3rem 0.6rem padding, `var(--accent)` bg, white text,
    radius 8, font 0.78rem 700 in `var(--font-num)`. Reuses the existing
    `.currency-pill` styles; on tap opens `<CurrencyPicker>` in a popover.
  - Amount input (flex 1): 60px tall, font 2rem 500 in `var(--font-num)`,
    right-aligned, 1px bottom border (`--border` → `--accent` on focus).
    `inputmode="decimal"`.
  - Date (right): Calendar icon + read-only date label in `--muted`. **Not
    editable** in the sheet — matches the existing read-only behavior.
- Existing `CATEGORY`, `TAGS`, `EVENT` blocks below, unchanged.
- **Footer** (3 buttons, left to right):
  - **Delete** — outline-danger button. Background `transparent`, border
    `1px solid rgba(239,68,68,0.30)`, text `#fca5a5`, padding 0.5rem 0.7rem,
    radius 8. Small trash icon + label.
  - **Cancel** — neutral border button, `flex: 1`.
  - **Save** — primary, `var(--accent)` bg, white text, `flex: 1`.

### B · Amount focused + numeric keypad

Same screen with the amount input focused (red caret + red bottom border)
and the iOS decimal pad up. No design change here — just confirms the
amount field uses `inputmode="decimal"` and the existing
`<KeyboardSaveBar>` pattern is NOT used (the sheet has its own footer).

### C · Manual delete confirmation

A second bottom-sheet stacks on top of the edit sheet. The edit sheet is
dimmed (`opacity: 0.55`, light blur) so the user sees the action chains.

- Drag handle.
- 44×44 round icon at top-left, `rgba(239,68,68,0.10)` bg, `#fca5a5` trash icon.
- Title (1.05rem 700, `--text`): `Delete this expense?`
- Body (0.88rem, `--muted`, line-height 1.45):
  ```
  480 RSD on transport, 18 May. This can't be undone.
  ```
  The amount uses `var(--font-num)` and `--text` color; everything else is
  muted. Pull the amount + currency + category name + formatted date from
  the expense being edited.
- Action row (2 buttons, equal flex):
  - **Cancel** — transparent bg, `--border` outline, `--text` color, radius 10.
  - **Delete** — `var(--danger)` (`#ef4444`) bg, white text, radius 10, trash icon + label.

### D · Receipt-backed edit sheet

Same surface as A, with these differences:

- **No `AMOUNT` block**. Receipt amounts are not editable here.
- **`FROM RECEIPT` pill** next to the eyebrow `EDIT EXPENSE`:
  `display:inline-flex; gap:4px; padding:1px 6px; border-radius:999px;
  background:rgba(148,163,184,0.12); color:var(--muted); font-size:0.62rem;
  font-weight:600; letter-spacing:0.05em; text-transform:uppercase;` —
  small receipt glyph (12px) + word "from receipt".
- The existing `SCOPE` radio row stays (sits below `EVENT`, separated by a
  1px `--border` top rule + 0.85rem top padding).
- **Footer Delete button** is heavier than the manual variant:
  - Filled-tint background `rgba(239,68,68,0.10)` (not transparent), border
    `1px solid rgba(239,68,68,0.30)`, text `#fca5a5`.
  - Label is **`Delete receipt`** (two words), with a trash icon. Wider — its
    `flex: 0` and the Cancel/Save buttons share the remaining row.
- The two-word label and tint warn that this is heavier than the manual
  delete — the cascade is the whole point.

### E · Delete receipt — cascade confirmation

The heaviest sheet. Stacks on top of D.

- Drag handle.
- 44×44 round icon, `rgba(239,68,68,0.10)` bg, `#fca5a5` **alert-triangle**
  icon (not trash — it's a warning about cascade).
- Title: `Delete this receipt?`
- Body:
  ```
  This deletes the whole receipt and all 5 expenses created from it —
  not just this one. This can't be undone.
  ```
  "whole receipt", "5 expenses" highlighted in `--text` (rest is `--muted`).
- **Cascade summary card** (`--field-deep` bg, 1px `--border`, radius 10,
  scrollable if many items):
  - Header row: small receipt glyph + merchant name (0.9rem 600, `--text`)
    on the left, receipt date+time (0.78rem `--muted` in `--font-num`) on
    the right; 1px `--border` bottom rule.
  - One row per item from the receipt: item name (truncate with ellipsis on
    overflow, 0.85rem `--text`, `flex: 1`) + amount (`--font-num`, 0.82rem,
    `--muted`).
  - Footer row: `TOTAL` eyebrow (0.7rem 700 uppercase letter-spacing 0.06em
    `--muted`) on the left, total amount (`--font-num` 700 `--text`) on the
    right; 1px `--border` top rule, subtle `rgba(255,255,255,0.02)` tint.
- Action row:
  - **Cancel** — `flex: 1`, neutral.
  - **Delete N items** — `flex: 1.4` (slightly wider to fit the count),
    `var(--danger)` filled, trash icon + label `Delete 5 items` where the
    number is the receipt's expense count. The count IS the warning.

## Interactions & Behavior

### Opening the editor

Unchanged from today. `ReviewView.vue` calls `openExpenseEdit(expense)` on
row tap. Pass through to `<ExpenseEditSheet :expense :open>` — the sheet
internally branches on `expense.receipt_id != null`.

### Manual expense — save with amount/currency changes

The existing save flow (`reviewStore.updateExpense` → `editExpense` API)
already accepts arbitrary fields. Add `amount_original` and
`currency_original` to the patch body when the values changed:

```js
const patch = {
  category_id: selectedCategoryId.value,
  tag_ids: [...selectedTagIds.value],
  event_id: selectedEventId.value,
  clear_event: selectedEventId.value === null,
  // NEW (manual only):
  ...(props.expense.receipt_id == null ? {
    amount_original: Number.parseFloat(String(amount.value).replace(',', '.')),
    currency_original: selectedCurrency.value,
  } : {}),
};
```

Validate the amount the same way `ExpenseForm.save()` does:
- Replace `,` with `.`, parse with `Number.parseFloat`.
- Reject empty, NaN, or `<= 0` with toast `Enter a valid amount`.
- Reject if neither amount nor currency was changed AND the rest of the
  form is also unchanged — but easier to just always send the full patch.

Patch the local cached row after the API succeeds (already happens via
`reviewStore.patchExpense`; extend the patch object with the new fields).

### Manual expense — delete

1. User taps **Delete** in the footer.
2. Open `<ConfirmDeleteSheet kind="expense" :expense>` (stacked, edit sheet
   stays mounted behind, dimmed via a `:has(.confirm-sheet)` opacity rule
   on the edit sheet or a local `confirmingDelete` ref).
3. On Cancel: close the confirm sheet, back to the editor.
4. On Delete:
   - Call `await reviewStore.deleteExpense(props.expense.id)`.
   - On success: close both sheets, splice the expense from
     `reviewStore.expenses`, toast `Expense deleted` (info).
   - On failure: toast the error, leave both sheets open.

### Receipt-backed expense — delete receipt

1. User taps **Delete receipt** in the footer.
2. Sheet first needs the cascade summary. Fetch it lazily on open:
   ```
   GET /api/receipts/:id?include=expenses
   → { id, merchant, captured_at, expenses: [{ id, item_name, amount, currency }] }
   ```
   Show a small spinner inside the cascade card until the response arrives.
3. Render the summary card from the response (merchant, date, items, total).
4. On Cancel: close confirm sheet only.
5. On Delete:
   - Call `await reviewStore.deleteReceipt(receiptId)`.
   - On success: close both sheets. Remove EVERY expense with
     `receipt_id === receiptId` from `reviewStore.expenses`. Toast
     `Receipt deleted (5 expenses removed)`.
   - On failure: toast the error.

### Online/offline contract

Same rule as today: if `!isOnline.value`, the toast says
`Not available offline` and the sheet doesn't open. Delete actions are also
online-only.

### Animations & stacking

Both confirm sheets reuse the existing scrim+sheet transitions from
`ExpenseEditSheet`. When stacked:

- The edit sheet stays mounted; do **not** unmount it.
- Apply `opacity: 0.55` (and optionally a tiny `filter: blur(0.5px)`) to
  the edit sheet while the confirm sheet is open, transitioning 180ms.
- The confirm sheet sits at `z-index: 50` (one above the edit sheet's 45).
- Drop a darker scrim (`rgba(0,0,0,0.35)`) between them, or skip the
  intermediate scrim and let the dimmed edit sheet read as the backdrop —
  the prototype does the latter.

## State Management

### `stores/review.js` — add two actions

```js
async function deleteExpense(id) {
  await api.deleteExpense(id);
  expenses.value = expenses.value.filter((e) => e.id !== id);
}

async function deleteReceipt(receiptId) {
  await api.deleteReceipt(receiptId);
  // Cascade-remove every expense that came from this receipt
  expenses.value = expenses.value.filter((e) => e.receipt_id !== receiptId);
}
```

Both actions should call `markDirty()` if `useStaleCache` is wired here
(check whether `review.js` uses the same pattern as the income store — it
should).

### `<ExpenseEditSheet>` — new local state

```js
const amount = ref("");                // string, "" until populated from props
const currency = ref("");
const currencyPickerOpen = ref(false);
const confirmingDelete = ref(false);
const cascade = ref(null);             // { merchant, captured_at, expenses, total } | null
const cascadeLoading = ref(false);
```

Initialize `amount` and `currency` from `props.expense.amount_original` and
`currency_original` in the existing `watch(() => props.open, …)` block.

## Component shapes

### `<ConfirmDeleteSheet>` — new component

```html
<template>
  <Teleport to="body">
    <Transition name="sheet">
      <div v-if="open" class="confirm-sheet" :data-kind="kind">
        <div class="drag-handle" />
        <div class="confirm-body">
          <div class="confirm-icon">
            <AlertTriangle v-if="kind === 'receipt'" :size="20" />
            <Trash2 v-else :size="18" />
          </div>
          <h3 class="confirm-title">{{ title }}</h3>
          <p class="confirm-text"><slot name="body"/></p>
          <slot name="detail"/> <!-- cascade card here, for receipt kind -->
          <div class="confirm-actions">
            <button class="btn-cancel" @click="$emit('cancel')">Cancel</button>
            <button class="btn-danger" :disabled="loading" @click="$emit('confirm')">
              <Trash2 :size="14" />
              {{ destructiveLabel }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
defineProps({
  open:              { type: Boolean, default: false },
  kind:              { type: String, default: "expense" }, // 'expense' | 'receipt'
  title:             { type: String, required: true },
  destructiveLabel:  { type: String, required: true },
  loading:           { type: Boolean, default: false },
});
defineEmits(["cancel", "confirm"]);
</script>
```

Styling: same surface, drag handle, radii, and shadow as
`ExpenseEditSheet`. Confirm sheet's `z-index: 50`. Destructive button is
`var(--danger)` filled, white text, radius 10.

## Design Tokens

All tokens come from `dinary/webapp/src/assets/base.css`. Do not invent new
ones.

| Token | Value | Used for |
|---|---|---|
| `--accent` | `#e94560` | Currency pill, Save button, focus underline |
| `--danger` | `#ef4444` | Destructive confirm button bg, icons in danger states |
| `--surface` | `#16213e` | Both sheets' background |
| `--field` | `rgba(255,255,255,0.04)` | Category chip, comment input |
| `--field-deep` | `rgba(0,0,0,0.18)` | Cascade summary card bg |
| `--border` | `rgba(255,255,255,0.08)` | Hairlines |
| `--border-strong` | `rgba(255,255,255,0.12)` | Drag handle |
| `--text` | `#eeeeee` | Body text, highlighted phrases in confirm copy |
| `--muted` | `#94a3b8` | Labels, secondary text, eyebrow |
| `--muted-2` | `#64748b` | Tertiary metadata |
| `--font-num` | `JetBrains Mono, …` | Amounts, dates, currency codes |

Danger-button-ghost extras (not yet in `base.css` — either add or inline):

| Use | Value |
|---|---|
| Ghost danger text | `#fca5a5` |
| Ghost danger border | `rgba(239,68,68,0.30)` |
| Filled-tint danger bg | `rgba(239,68,68,0.10)` |

If you want to add these as tokens, name them `--danger-soft`,
`--danger-border`, `--danger-tint`. The prototype inlines them.

Sizes match existing components:
- Sheet radius: `18px 18px 0 0`
- Sheet drag handle: `36 × 4` radius 2, `--border-strong`
- Field-block bottom margin: `1.25rem` (matches existing)
- Eyebrow label: `0.62rem 700 letter-spacing 0.07em uppercase --muted`
- Confirm title: `1.05rem 700 --text`
- Confirm body text: `0.88rem --muted line-height 1.45`

## API

### Modified — `api/expenses.js`

```js
export async function deleteExpense(id) {
  return request(`/api/expenses/${id}`, { method: "DELETE" });
}
```

Backend should refuse (`409 Conflict`) if the expense has `receipt_id !=
null` — manual delete is for manual expenses only; receipt-backed delete
goes through `/api/receipts/:id`.

### New — `api/receipts.js`

```js
export async function getReceipt(id, { include = "" } = {}) {
  const qs = include ? `?include=${encodeURIComponent(include)}` : "";
  return request(`/api/receipts/${id}${qs}`);
}

export async function deleteReceipt(id) {
  return request(`/api/receipts/${id}`, { method: "DELETE" });
}
```

`GET /api/receipts/:id?include=expenses` returns:
```ts
{
  id: number,
  merchant: string,
  captured_at: ISO,           // datetime, used for "20 May, 13:42"
  total: { amount: number, currency: string },
  expenses: Array<{
    id: number,
    item_name: string,
    amount: number,
    currency: string,
  }>,
}
```

`DELETE /api/receipts/:id` cascades server-side, removing the receipt row,
all its expense rows, and any uploaded image. Returns `204`.

## Assets

No new assets. New icons (all in `lucide-vue-next`, already a dep):

- `Trash2` — Delete buttons, delete-confirm icon
- `AlertTriangle` — receipt cascade confirm icon
- `Receipt` — `FROM RECEIPT` eyebrow pill + cascade summary header

## Files in this bundle

| File | Purpose |
|---|---|
| `Review Editors.html` | Self-contained, offline prototype — open in any browser to see the 5 screens. Visual reference only; **do not port its React/JSX structure**, recreate in Vue per this README. |
| `README.md` | This document — the spec |

## Checklist for the implementer

- [ ] Add the hero amount row to `ExpenseEditSheet.vue`, gated on
      `props.expense?.receipt_id == null`. Reuse `<CurrencyPicker>` and
      the `.hero-row` / `.currency-pill` styles from `ExpenseForm.vue`.
- [ ] Add the `FROM RECEIPT` eyebrow pill to the sheet header, gated on
      `props.expense?.receipt_id != null`.
- [ ] Add the Delete button to the sheet footer. Label and styling vary by
      `receipt_id` (manual: outline danger "Delete"; receipt: filled-tint
      danger "Delete receipt").
- [ ] Build `<ConfirmDeleteSheet>` with two variants ("expense" /
      "receipt"). Stack it above the edit sheet at `z-index: 50`. Dim the
      edit sheet behind to `opacity: 0.55` when the confirm is open.
- [ ] Wire the cascade-fetch lazy load when opening the receipt confirm.
      Show a spinner inside the cascade card while loading; render
      merchant + items + total when it arrives.
- [ ] Extend `updateExpense` patch with `amount_original` and
      `currency_original` for manual expenses.
- [ ] Add `reviewStore.deleteExpense(id)` and `reviewStore.deleteReceipt(id)`
      that splice the cache and call the API.
- [ ] Add API helpers `deleteExpense`, `getReceipt(id, {include})`, and
      `deleteReceipt` (existing `_request.js` for auth/baseURL).
- [ ] Backend: `DELETE /api/expenses/:id` (manual only), `DELETE
      /api/receipts/:id` (cascade), `GET /api/receipts/:id?include=expenses`.
- [ ] Offline contract: all three new actions toast `Not available offline`
      when `!isOnline.value`, same as today's edit flow.
- [ ] Tests:
      - `component-expense-edit-sheet.test.js` — extend with: amount change
        patches the expense; delete button opens confirm; confirm calls
        `deleteExpense`; receipt-backed expense hides amount editor; receipt
        confirm fetches cascade and calls `deleteReceipt`.
      - `store-review.test.js` — `deleteExpense` splices the array;
        `deleteReceipt` removes every expense with that `receipt_id`.
      - `api-expenses.test.js` / `api-receipts.test.js` — the new endpoints.

# Cross-Cutting Patterns

Patterns that recur in multiple screens. When a new pattern starts to repeat, document it here so future code reuses rather than reinvents.

## Bottom sheets

Used for: edit-expense, edit-income, category correction, provider CRUD, currency picker (popover variant). Any flow that needs to feel modal but be one-thumb-reachable.

### Two shells

| Component | Source | When |
|---|---|---|
| `BaseSheet` | `BaseSheet.vue` | Default. Every new sheet uses this. Provides scrim + drag handle + header slot with ✕ + scrollable body + optional footer slot. Props for `dimmed`, `tall`, `fullHeight`, `zIndex`. |
| Inline shell | `ProviderSheet.vue` | Only when the body needs to manage its own multi-step state (preset switcher, show-key, two-step delete) that doesn't fit the `BaseSheet` slot model. Predates `BaseSheet`. Don't add new inline shells. |
| `ConfirmDeleteSheet` | `ConfirmDeleteSheet.vue` | Stacked on top of another sheet — see "Confirm-delete" below. |

### Anatomy

```
┌──────────────────────────────────────┐
│              ════                    │ 36×4 drag handle, --border-strong
│                                      │
│  EYEBROW             [FROM RECEIPT] ✕│ uppercase 11px label + optional pill + close
│  Sheet title                         │ 16 px / 600  (optional — set in header slot)
│                                      │
│  ╔═════════════════════════════════╗ │
│  ║ scrollable body                 ║ │
│  ║                                 ║ │
│  ╚═════════════════════════════════╝ │
│  ────────────────────────────────    │ 1 px border --border
│  [Delete]    [Cancel]    [Save]      │ sticky footer
└──────────────────────────────────────┘
```

### Rules

- Mount via `<Teleport to="body">` (`BaseSheet` does this for you).
- Scrim: `rgba(0, 0, 0, 0.55)`, click-to-close, fades in 260 ms.
- Sheet: `background: var(--surface)`, `border-radius: 18 px 18 px 0 0`, `max-height: 80vh` (sheets), `min-height: 50vh` when `tall`. Shadow `0 -4 px 24 px rgba(0,0,0,0.35)`.
- Slide in via `translateY(100%) → translateY(0)` over 280 ms with `cubic-bezier(0.32, 0, 0.67, 0)`.
- Drag handle is decorative — actual close is via the ✕, the scrim, or programmatically. No drag-to-dismiss.
- Footer is `display: flex; gap: 0.75 rem` with `padding-bottom: calc(0.75 rem + env(safe-area-inset-bottom, 0px))` so it clears the home indicator.
- A `Delete` button in the footer sits to the **left**; Cancel + Save sit to the right with `flex: 1`. Delete is `flex-shrink: 0`.

### Dimming behind a stacked sheet

When a sheet pops a child sheet (e.g. `ExpenseEditSheet` → `ConfirmDeleteSheet`), pass `:dimmed="confirmingDelete"` to the parent. `BaseSheet` then applies `opacity: 0.55; filter: blur(0.5 px); pointer-events: none`. The child sheet sits at `z-index: 50`; the parent's scrim sits at 40, the parent itself at 45.

References: `ExpenseEditSheet.vue`, `IncomeEditSheet.vue`, `CategorySheet.vue`, `BaseSheet.vue`.

## Confirm-delete

Every destructive action goes through `ConfirmDeleteSheet`. No JS `confirm()`. No inline two-step in the host except where a sheet would be overkill (`ProviderSheet`'s delete).

### Anatomy

```
┌──────────────────────────────────────┐
│             ════                     │
│                                      │
│     ⚠   ← 44×44 danger-tint circle   │
│                                      │
│  Delete this receipt?                │ 1.05 rem / 700
│  This deletes the whole receipt and  │ 0.88 rem / muted body slot
│  all 5 expenses created from it…     │
│                                      │
│  ┌──────────────────────────────┐    │ detail slot (cascade card on receipts)
│  │ Mama Shelter         17 May  │    │
│  │ Pelmeni dinner       820 RSD │    │
│  │ Beer × 2             520 RSD │    │
│  │ TOTAL              1 340 RSD │    │
│  └──────────────────────────────┘    │
│                                      │
│  [   Cancel   ] [  Delete 5 items  ] │ Cancel + filled --danger
└──────────────────────────────────────┘
```

### Rules

- Icon: 44×44 circle, `rgba(239, 68, 68, 0.10)` background, `#fca5a5` glyph. `AlertTriangle` for receipts, `Trash2` for expenses + incomes (and any future single-record delete).
- Title: 1.05 rem / 700 in `--text`. Phrased as a question: "Delete this expense?" / "Delete this receipt?" / "Delete this income?".
- Body slot: one short sentence in `--muted`. **Inline-bold the exact thing being deleted** via a `.confirm-highlight` span (`var(--font-num)`, `var(--text)`). Always end with "This can't be undone." for single-record deletes.
- Detail slot: optional. Used by receipt deletes to inline a `ReceiptCascadeCard` so the user can see every item that will go.
- Footer: Cancel (transparent, `--border`) on the left, destructive button (`--danger` fill, white) on the right. Both `flex: 1`. Destructive label is dynamic: `"Delete"` for single records, `"Delete N items"` (with the live cascade count) for receipt cascades.
- Sit at `z-index: 50` so the parent sheet sits at 45 dimmed; the parent's scrim at 40 stays visible behind everything.

References: `ConfirmDeleteSheet.vue`, `ReceiptCascadeCard.vue`, `composables/useExpenseDeleteFlow.js`.

## Scope selector

When a correction can apply to varying breadth of history, the user picks the scope explicitly. Lives inside `ExpenseEditSheet` only when editing a receipt-backed expense (`expense.receipt_id != null`). Manual expenses and rule-correction paths skip it.

### Anatomy

```
SCOPE
  ⦿ Only this   ⦾ Last month   ⦾ This year   ⦾ All history
─────────────────────────────────────────────────────────── ← 1 px separator above
```

### Rules

- 4 options: `single` / `month` / `year` / `all`. Default `single` — the safest scope.
- Native radio inputs with `accent-color: var(--accent)`. Wraps with `gap: 0.5 rem`.
- Uppercase 11-px `SCOPE` label above (`.field-block` + `.field-label` recipe).
- Sits at the bottom of the sheet body, separated from the rest by a 1-px `--border` line on top of the block (`scope-block` adds `border-top + padding-top`).
- When `has_rule === true`, render a sibling **"Also update rule"** checkbox below — applying a `scope > single` and ticking the box updates the rule's mapping too.
- Don't show the selector when scope is forced or irrelevant (manual expenses; rule-correction path). Hiding > disabling.

References: `ScopeSelector.vue`, `ExpenseEditSheet.vue`.

## Picker vs Manage

Every catalog-backed select supports two modes, triggered by separate buttons in the same section header.

### `+` (plus) — inline create

Drops an `InlineCreateRow` (or `InlineCreateEvent`) below the field. See "Inline create".

### ⚙ (cog) — manage

Toggles a `ManageList` below the field. The list shows active items above inactive items, separated by the eye / eye-off state divider.

### Rules

- Both buttons live in the section header row, right-aligned. Use `IconBtn` (accent for `+`, muted for `⚙`).
- Tapping ⚙ toggles to ✕ while open (same button, swapped icon).
- One section's manage panel can be open while another section's add panel is also open. Don't auto-collapse siblings.
- `CatalogSelectField` packages this pattern with its own dropdown trigger; `ExpenseForm`'s event + tags blocks build it manually with `IconBtn`s because the field below is a chip flow, not a select.

References: `CatalogSelectField.vue`, `ManageList.vue`, `ExpenseForm.vue`.

## Inline create

Replaces the older add-modal pattern. Net-new catalog items are created without leaving the form context.

### Anatomy (`InlineCreateRow`)

```
┌──────────────────────────────────────────┐
│ + │ [autofocused input            ] [✕][✓]│ accent border + 3-px glow
└──────────────────────────────────────────┘
```

### Rules

- Shell: `border: 1 px solid var(--accent)`, `background: color-mix(in oklab, var(--accent) 10%, var(--field-deep))`, `box-shadow: 0 0 0 3 px color-mix(in oklab, var(--accent) 15%, transparent)`, `border-radius: 8 px`.
- Leading `+` icon is accent-tinted, decorative.
- Input autofocuses on mount. Enter → save, Esc → cancel.
- Empty input on save → silent cancel (don't show a validation error for that).
- Optional `validate(value)` prop returns an error string. Errors render under the input in `--error`.
- ✓ button hovers to `--success`; ✕ hovers to `--text`.

### When to use `InlineCreateEvent` instead

Events have richer schema (name + date range + auto-attach + auto-tags). Use the dedicated `InlineCreateEvent` component — same accent shell, larger body, footer with Cancel + Add-event. Don't try to make `InlineCreateRow` polymorphic.

References: `InlineCreateRow.vue`, `InlineCreateEvent.vue`.

## State dividers

Used in `ManageList` to separate active and inactive items without using text labels.

### Anatomy

```
👁  ─────────────────────────────         ← solid gradient, accent-tinted (40 % alpha)
  …active items (edit + hide buttons)…
👁⃠  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─               ← dashed line, --border-strong
  …inactive items (edit + restore + delete buttons)…
```

### Rules

- **Active divider** — `Eye` icon (12 px) on the left, `--accent` color, then a 1-px solid linear-gradient line from `--accent` to transparent at 40 % opacity.
- **Inactive divider** — `EyeOff` icon (12 px) on the left, `--muted-2` color, then a 1-px dashed `--border-strong` line.
- Inactive item names get `text-decoration: line-through` and `color: var(--muted)`. Active item names keep `--text`.

Apply this same active/inactive visual recipe anywhere a binary state pair shows up (on/off, published/draft, enabled/disabled) — don't fall back to words.

References: `ManageList.vue`.

## Status dot

Used in `ProviderCard` and `HealthSummaryCard`. 8-px solid circle with a soft halo (`box-shadow` at 20 % alpha).

### Kinds

| Kind | Color | Halo | Use |
|---|---|---|---|
| `ok` | `--success` | yes | Enabled, last call succeeded |
| `rate_limited` | `--warning` | yes | Enabled but currently rate-limited (`rate_limited_until > 0`) |
| `off` | `--muted` | no | Disabled by user |
| `error` | `--error` | yes | Enabled but last call failed |

### Rules

- Halo is 3-px solid `box-shadow` at 20 % alpha of the same color.
- The dot does not animate.
- Always paired with text — the dot is never the sole carrier of meaning.

References: `StatusDot.vue`.

## Keyboard handling

The PWA runs full-screen on phones; the soft keyboard occludes the bottom action bar. Two complementary mitigations:

### `useKeyboardVisible` composable

Tracks `window.visualViewport` and exposes `keyboardVisible` + `keyboardBottom`. Threshold: keyboard considered visible if `viewport.height / window.innerHeight < 0.75`.

### `KeyboardSaveBar`

While `keyboardVisible` is true, mount a branded Save button at `position: fixed; bottom: <keyboardBottom> px`. The button:

- Fills with the view's primary color via the `accentColor` prop (`var(--expense)` in Add, `var(--success)` in Income).
- Full-width up to `max-width: 480 px`, 40-px high, 10-px radius.
- Animates in with a 0.15-s slide-up + fade.

**Why both:** the form's bottom action bar is hidden by the keyboard, but its Save key (or `enterkeyhint`) is too easy to mistake for the keyboard's "Done" or "Return". A view-coloured Save bar removes the ambiguity and makes commit explicit.

References: `composables/useKeyboardVisible.js`, `components/KeyboardSaveBar.vue`, `views/AddView.vue`, `views/IncomeView.vue`.

## Toasts

Single global `<div class="toast">` lives in `App.vue`, driven by `useToastStore`.

### Rules

- Drops in from top with overshoot easing (`cubic-bezier(0.34, 1.56, 0.64, 1)`, 380 ms).
- Pill shape, `border-radius: 999 px`.
- Tap to dismiss (`cursor: pointer`, `pointer-events: auto` while visible).
- Three types: `success` (green bg, black text + `✓` glyph), `error` (red bg, white text + `✕`), `info` (`--surface-2` bg, white text + `ℹ`, 1-px `--border-strong`).
- Single-line, `max-width: calc(100% - 2 rem)`, ellipsis on overflow.

Don't queue multiple toasts. Replace.

## Swipe-to-act

List rows that have a primary tap action plus 1–2 secondary actions reveal those actions via a left swipe instead of cluttering the row chrome. Used in `RuleRow` (Edit + Approve on doubtful, Edit on certain), `ExpenseRow` (Edit), and `IncomeRow` (Edit). All wiring lives in `composables/useSwipeRow.js`.

### Anatomy

```
At rest                          Mid-swipe                       Past commit
┌─────────────────────┐          ┌─────────────────┬───┬────┐    ┌───┬─────────────────┐
│  row content        │   →      │  row content    │ ✎ │ ✓  │ →  │ … │ ✓   APPROVE    │
└─────────────────────┘          └─────────────────┴───┴────┘    └───┴─────────────────┘
                                    slider             panel       secondary shrinks,
                                                                   primary widens+bright
```

### Behavior

| User action | Result |
|---|---|
| Tap the row | Emits `tap` (opens edit sheet). |
| Tap an inline action chip (RuleRow approve chips) | `@click.stop` + emits `approve`. Never bubbles. |
| Swipe left ~`commitOver` px (default 80, IncomeRow + ExpenseRow use 60) | Row slides left, reveals action panel docked right. |
| Continue past `panelWidth` | `isCommit` flips true — primary panel button widens to ~2× and brightens; secondary collapses. |
| Release inside revealed zone | Snaps fully open. User can tap a panel button or tap the row to close. |
| Release past commit zone | `onPrimary` fires: Approve on doubtful, Edit-sheet on certain / expense / income rows. Row snaps closed. |
| Vertical scroll | 8-px axis-lock detects vertical intent and disables horizontal drag for the gesture. |
| Open another row | Store-mediated: every row writes its id to `<store>.openRowId` on open; other rows watch and close themselves. |

### Rules

- **Wrapper is the clip surface** (`overflow: hidden`, fully opaque background, holds the warning left-border on `RuleRow` so it doesn't slide off-screen).
- **Slider must be opaque.** The action panel sits behind it. A translucent slider bleeds button color through the row at rest.
- **Don't put the row's tint into shorthand `background`** mixed with `var(--bg)` — browsers drop the whole declaration. Use `background-color` for the solid base and `background-image: linear-gradient(...)` for the tint.
- **`touch-action: pan-y`** on the slider — vertical page scroll keeps working through the row.
- **Don't replace row content during commit.** The commit cue lives on the *panel button* (widens + brightens). The user keeps reading what they're acting on.
- **Single open row** per store — opening any swipe row closes the currently-open one via `<store>.openRowId`. No multi-open state to reason about.
- **`shouldFireTap()` gates the `@click`** so a drag-induced click (release after a horizontal move) doesn't fire `tap`.

References: `composables/useSwipeRow.js`, `components/RuleRow.vue`, `components/ExpenseRow.vue`, `components/IncomeRow.vue`.

## Confirm all

Batch-confirm pattern for queues where every pending item shares the same primary action. Currently used at the end of the NEEDS REVIEW list to confirm every visible doubtful rule in one PATCH.

### Rules

- Mount the button only when **(a)** the list has fully paginated (`!hasMore`) and **(b)** at least one actionable item remains.
- Centered, full-width-capped, pill-shaped (`border-radius: 999 px`), green-tinted: `rgba(34, 197, 94, 0.15)` fill, `rgba(34, 197, 94, 0.3)` border, `--success` text.
- Label format: `Confirm all (N)` — N is the live count.
- Tap → single API call, then refresh any dependent sections (e.g. EXPENSES, so the user sees the new classifications immediately).
- Don't auto-confirm. The user always has to tap. No countdown.
- Gate on `isOnline` like every other write action.

References: `views/ReviewView.vue`.

## Skeleton rows

Use a card with the same border, radius, and approximate height as the real row, pulsing opacity 1 ↔ 0.4 on a 1.4-s loop. Two skeletons is enough to communicate "more loading."

```css
@keyframes skeleton-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
```

Heights: ~72 px for review rows, ~62 px for income rows. `IncomeView` adds a 4-px green left border to skeletons so the user reads the placeholders as "future income rows", not "future expense rows".

Show skeletons only when **fetching** new pages, not during initial load (initial load shows nothing — the user already sees the chrome).

References: `ReviewView.vue`, `IncomeView.vue`.

## Infinite scroll

`IntersectionObserver` on a hidden sentinel `<div>` near the bottom of the list.

### Rules

- `rootMargin: '120 px'` so loading kicks off before the user hits the end.
- Gate on `!loading && hasMore && isOnline` to avoid duplicate calls.
- Render skeleton rows below the last item while loading.
- Don't render the sentinel when `!hasMore` (no false triggers).
- A view can mount multiple sentinels for independent lists (`ReviewView` does this for rules and expenses separately).

References: `ReviewView.vue`, `IncomeView.vue`.

## Offline-aware actions

Many actions require the server. Pattern:

```js
function requireOnline() {
  if (!isOnline.value) {
    toast.show("Not available offline", "info");
    return false;
  }
  return true;
}
```

Gate write actions and refresh buttons; render the per-view offline notice strip in `App.vue` so the user understands why interactions are blocked. The offline notice copy is per-view:

- Add: "Offline — expenses will be queued" (expenses queue, so the action isn't actually blocked — it's deferred).
- Income: "Offline — incomes can't be added or edited".
- Review / LLM: "Offline — changes not available".

References: `composables/useOnline.js`, `App.vue`, every view file.

## Provider sheet form

The add/edit/delete sheet for LLM providers — currently the only sheet that doesn't use `BaseSheet` (predates it).

### Layout

```
EYEBROW (ADD PROVIDER / EDIT PROVIDER)
Title (label · model in edit; "New entry" otherwise)

[Groq] [OpenRouter] [Gemini] [Custom]   ← preset chips

LABEL          [____________]
BASE URL       [____________]            ← mono
MODEL          [____________]            ← mono
               [ suggestion chips ]      ← when a preset matches
API KEY        [••••••••]    [👁]        ← show/hide toggle

[ ] Enabled in failover pool

────── dashed separator (edit only) ──────
[🗑 Remove provider]                       ← ghost-danger button
  on tap →
  ┌────────────────────────────────┐
  │ Remove <label>? Logs are kept. │     ← inline confirmation in danger-tint bg
  │ [Cancel]            [🗑 Remove]│
  └────────────────────────────────┘

[Cancel]                    [✓ Save changes]
```

### Rules

- Preset chips prefill `base_url`; if the preset has a model list, those appear as one-tap suggestion chips below the model input.
- API-key field in edit mode is empty with the hint *"Leave empty to keep the existing key"* — never display the stored secret.
- Delete is two-step inline: ghost button → danger-tinted block with Cancel + Remove. Two-step inline is OK here only because the rest of the sheet would be lost behind a stacked `ConfirmDeleteSheet`. Don't reach for two-step inline elsewhere — use `ConfirmDeleteSheet`.

References: `ProviderSheet.vue`.

## Per-context primary color

A behavioural pattern, not just a token choice: every top-level view has one primary color, and every primary commit affordance inside that view uses it. See `design-language.md#per-context-primary-color`.

Practical consequences:

- The currency pill, the bottom Save bar, and `KeyboardSaveBar` *all share the view's color* — they're the same call to action in three places.
- Selected-state chips inside that view also use the view's color (`CategoryQuickPicks` orange; selected event chip orange; future income tag selection should be green if anyone adds tag support there).
- Hover / focus states still use `--accent` — it's the global UI focus color even in coloured contexts (see the underline-on-focus in `ExpenseForm`'s hero amount: `--accent`).
- `CurrencyPicker` accepts an `accentColor` prop so the selected-chip fill follows the host context.

References: `ExpenseForm.vue` (orange), `IncomeForm.vue` (green), `ExpenseEditSheet.vue` + `CurrencyAmountRow.vue` (sky-blue), `CurrencyPicker.vue`.

## Hierarchy connector (legacy)

Old parent → child indented-line pattern. Not currently used — `ExpenseForm`'s group→category dropdowns were replaced by `CategoryQuickPicks` + `category-pick-btn`. The pattern is kept here for any future parent→child select pair (e.g. account → sub-account) where the chevron-trigger form still makes sense.

```
[ parent select       ▾   +⚙ ]
 │
 └─►  [ child select  ▾   +⚙ ]
```

### Rules

- 1-px vertical line in `--border-strong`, starting at the bottom of the parent and extending to the row of the child.
- L-elbow at the bottom — 1-px horizontal segment.
- Child is indented ~30 px past the line.
- The indent IS the label. Don't write "Group" / "Category" above the fields.

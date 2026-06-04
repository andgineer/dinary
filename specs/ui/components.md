# Component Catalog

Every shipped UI component, with its source file and one-line contract. The `.vue` file is the source of truth for props, slots, and events.

## Primitives

| Component | File | Contract |
|---|---|---|
| `IconBtn` | `components/IconBtn.vue` | Square 26×26 icon button. `tone`: `accent` / `muted` / `danger`. Icons resolved by short name from a closed set (`plus`, `cog`, `x`, `edit`, `eye`, `eye-off`, `save`, `search`, `cal`, `scan`, `check`, `hash`, `refresh`). Props: `icon`, `tone`, `label` (aria, required), `disabled`. |
| `StatusDot` | `components/StatusDot.vue` | 8px halo dot for service status. `kind`: `ok` (green) / `rate_limited` (amber) / `off` (muted, no halo) / `error` (red). |
| `BaseSheet` | `components/BaseSheet.vue` | Shared bottom-sheet shell: scrim, drag handle, header slot + close button, scrollable body, optional footer slot. Props: `open`, `dimmed`, `ariaLabel`, `tall`, `fullHeight`, `zIndex` (default 45). Slots: `header`, `pre-body`, `default`, `footer`. Emits: `close`. |
| `BaseModal` | `components/BaseModal.vue` | Legacy backdrop + centered modal shell. Used by `EditModal`; do not add new flows here — use `BaseSheet`. |

## Form fields

| Component | File | Contract |
|---|---|---|
| `CurrencyAmountRow` | `components/CurrencyAmountRow.vue` | Composite: hero currency pill (sky-blue `#60a5fa` fill) + decimal amount input with a 2rem mono right-aligned bottom-line. Owns its `CurrencyPicker` popover. Used in `ExpenseEditSheet`. `v-model:amount` + `v-model:currency`. |
| `CatalogSelectField` | `components/CatalogSelectField.vue` | Catalog dropdown row with built-in picker panel + plus + cog. Owns the picker-vs-manage modes (see `patterns.md`). No longer used by `ExpenseForm` (replaced by `CategoryQuickPicks` + `category-pick-btn`); kept available for future catalogs that need the chevron-trigger form. Props: `kind` (`group`/`category`/`event`), `label`, `modelValue`, `options`, `inactive`, `manageOpen`, etc. |
| `CurrencyPicker` | `components/CurrencyPicker.vue` | Wrap-flow of saved currency chips + manage mode (search world list, add/remove). Lives inside a popover anchored under any currency pill. Prop `accentColor` (CSS value or token) drives the selected-chip fill so each context picks its own (orange in Add, green in Income, sky-blue in Review edit). |
| `TagPicker` | `components/TagPicker.vue` | Wrap-flow of selectable tag chips with hidden `<input type=checkbox>`. Selected chips fill `--accent`. Used in `ExpenseForm` and inside `InlineCreateEvent` for auto-tags. `v-model` of an array of ids. |
| `ManageList` | `components/ManageList.vue` | Active + inactive list with inline edit/hide/delete buttons. State dividers (Eye + accent gradient, EyeOff + dashed muted line) replace the words "Active" / "Inactive". Inactive names get strike-through. Props: `kind`, `active`, `inactive`, `label`, `pendingId`. |
| `ScopeSelector` | `components/ScopeSelector.vue` | Radio-row helper. Renders one `<input type=radio>` per option with `accent-color: var(--accent)`. Inline-wrapped. `v-model` is the option value. Used inside `ExpenseEditSheet`. |
| `CategoryQuickPicks` | `components/CategoryQuickPicks.vue` | Horizontal wrap of pill buttons over the frequent-category list. Selected pill fills `--expense` (orange) — `ExpenseForm` is the only consumer today and owns Add's context. Props: `categories`, `selectedCategoryId`. Emits: `select(categoryId)`. |

## Inline create (replaces add-modals on the entry form)

| Component | File | Contract |
|---|---|---|
| `InlineCreateRow` | `components/InlineCreateRow.vue` | One-line accent-shell row: leading `+` glyph + autofocus input + ✕/✓. Enter → save, Esc → cancel. Empty input → silent cancel. Optional `validate(value)` prop. |
| `InlineCreateEvent` | `components/InlineCreateEvent.vue` | Same accent shell, but full event schema: name + from/to dates + auto-attach checkbox + auto-tags via embedded `TagPicker`. Footer has Cancel + "Add event" buttons. |

## Navigation

| Component | File | Contract |
|---|---|---|
| `HeaderSegmented` | `components/HeaderSegmented.vue` | Segmented control with two inline tabs and an overflow menu. **Inline**: Add (Plus, 56×38, `--expense` tint/fill) and Review (ListChecks, 56×38, sky-blue `#60a5fa` tint/fill, doubtful-count badge bottom-right). **Overflow `•••`** (MoreHorizontal, 36×30, muted; `--accent` fill when any rare tab is the current tab): on tap, drops a 200-px-wide menu listing every entry in the module-level `RARE_TABS` array — currently Income (TrendingUp) and LLM providers (Cpu). Esc / outside `pointerdown` closes the menu. `v-model:tab` with values `'add'`/`'income'`/`'review'`/`'llm'`. Future rare tabs: append to `RARE_TABS` only — no header restructure needed. |

## App shell

| Component | File | Contract |
|---|---|---|
| `App` | `App.vue` | Top-level shell — dev banner, sticky header (brand + version + queue badge + `HeaderSegmented` + offline notice strip), main view router by `tab`, queue modal, global toast. |
| `AddView` | `views/AddView.vue` | Mounts `ExpenseForm`, `QrScanner`, the sticky bottom action bar (Scan 48×48 orange + Save flex-1 48px orange), and `KeyboardSaveBar` (orange variant). |
| `IncomeView` | `views/IncomeView.vue` | Mounts `IncomeForm` inline at top, `INCOMES` section grouped by year with year totals, `IncomeRow` list, scroll-sentinel pagination, edit sheet, sticky bottom Save bar (green), `KeyboardSaveBar` (green variant). |
| `ReviewView` | `views/ReviewView.vue` | Two-section list (`NEEDS REVIEW` warning header + `EXPENSES` neutral header). Owns the scroll container, two `IntersectionObserver` sentinels, refresh control, Confirm-all bulk action, and `ExpenseEditSheet` open/close. |
| `LLMView` | `views/LLMView.vue` | Provider pool management. Renders `HealthSummaryCard`, an optional `RECEIPT QUEUE` chip strip, then `PROVIDER POOL` and a list of `ProviderCard`s. Owns the 30s polling refresh timer. |

## Add view

| Component | File | Contract |
|---|---|---|
| `ExpenseForm` | `components/ExpenseForm.vue` | The entry form. Hero row (currency pill `--expense` + amount input + cal+date), grouped `category-card` (`CategoryQuickPicks` row at top, `category-pick-btn` row below — both inside one 12-px-radius shell with `--field` fill and an internal divider), Event section (`event-chips` flow with `--expense` selected fill + `+` / cog actions), Tags section (`TagPicker` + `+` / cog), comment single-line input. Exposes `save()`, `reset()` via `defineExpose`. |
| `KeyboardSaveBar` | `components/KeyboardSaveBar.vue` | Branded Save button floating above the soft keyboard. Mounted only when `useKeyboardVisible` reports the keyboard up. `accentColor` prop picks the button fill: `var(--expense)` in `AddView`, `var(--success)` in `IncomeView`. |
| `QrScanner` | `components/QrScanner.vue` | Camera viewfinder for fiscal QR codes. Exposes `start()`, `stop()`. Emits `scan(text)`, `error(err)`. |

## Income view (new)

| Component | File | Contract |
|---|---|---|
| `IncomeForm` | `components/IncomeForm.vue` | Card-shell entry form. Hero row: green currency pill + 2rem decimal amount input with green focus underline. Date row: two columns — `For month` (`<input type="month">`) and `Received date` (`<input type="date">`), both transparent with bottom-border underlines. Single-line comment input below. Exposes `save()`. |
| `IncomeRow` | `components/IncomeRow.vue` | Receipt-line-style row with green 4-px left border (`--success`). Top row: month label + `+amount currency` (mono, green num + muted code). Bottom row: received-date · comment OR received-date · original-amount fallback. Left-swipe reveals an `Edit` panel (`--success` fill); panel uses `--surface-2` muted variant when offline. Props: `income`. Emits: `tap`. |
| `IncomeEditSheet` | `components/IncomeEditSheet.vue` | Bottom-sheet edit. Mirrors `IncomeForm` body (hero row + date row + comment), green Save in the footer + ghost-danger Delete. Delete opens `ConfirmDeleteSheet` (`kind="income"`). Props: `open`, `income`. Emits: `close`. |

## Review view

| Component | File | Contract |
|---|---|---|
| `RuleRow` | `components/RuleRow.vue` | Row for a classification rule. **Doubtful**: graded left-border by `confidence_level` (1 → `--error`, 2 → `--warning`, 3 → muted amber `rgba(245,158,11,0.75)`; out-of-range treated as 2). Tinted amber wash (`rgba(245,158,11,0.07)`) over slider. Bottom row wrap-flow: tag chips → green approve chip (`Sparkles` prefix if LLM differs) → up to 2 alt-category chips → frequent-category quick picks → trailing pencil. **Certain**: plain card, group › category breadcrumb left, muted chevron right. Swipe reveals **Edit + Approve** (doubtful, 168 px panel — Approve grows + brightens on commit, Edit shrinks) or **Edit only** (certain, 92 px). Props: `item`. Emits: `tap`, `approve({ item, categoryId })`. |
| `ExpenseRow` | `components/ExpenseRow.vue` | Individual receipt-line row in EXPENSES. Top: `item_name` (or `store_name`/`store`/`merchant` fallback) + trailing `store_name` when item-name is primary. Bottom wrap: date (mono) · category-name · tag chips · `· event_name` · trailing amount (mono, muted). Warning left-border + amber wash when `confidence_level < 4`. Swipe reveals Edit (84 px). Props: `expense`. Emits: `tap`. |
| `ExpenseEditSheet` | `components/ExpenseEditSheet.vue` | Bottom sheet for editing one expense (or correcting a doubtful rule). Body fields: **AMOUNT** block with `CurrencyAmountRow` (manual-only), CATEGORY chip → `CategorySheet`, TAGS toggle row, EVENT `<select>`, **SCOPE** radios (receipt-backed only), "Also update rule" checkbox (when `has_rule`). Footer: ghost-danger Delete (left, label `"Delete"` for manual / `"Delete receipt"` with `--danger` tint background for receipt) + Cancel + Save (sky-blue `#60a5fa`, disabled until a category is selected). Pops the matching `ConfirmDeleteSheet`. **Event → tag lifecycle**: selecting an event adds its auto-tags to the tag selection; removing an event removes those auto-tags; switching events removes the old event's auto-tags then adds the new ones. **Inactive items**: inactive events and tags already attached to the expense are shown in the editor (so the user can remove them) even though they are hidden from the regular selection lists; they appear visually dimmed. Props: `open`, `expense`, `suggestions`, `ruleItem`. Emits: `close`. |
| `ConfirmDeleteSheet` | `components/ConfirmDeleteSheet.vue` | Reusable stacked confirm-bottom-sheet. 44×44 danger-tint circular icon (`AlertTriangle` when `kind="receipt"`, `Trash2` otherwise), title (1.05 rem / 700), `body` slot for one-line context, optional `detail` slot (used by receipt cascade), Cancel + filled `--danger` destructive button (label provided by `destructiveLabel`). Mounts at `z-index: 50`, sits above the edit sheet which `BaseSheet` dims via `:dimmed`. Used today by `kind="expense"` / `"receipt"` / `"income"`. |
| `ReceiptCascadeCard` | `components/ReceiptCascadeCard.vue` | Inline detail used in `ConfirmDeleteSheet`'s `detail` slot for receipt deletes. Header row (merchant + captured-at date), one `cascade-row` per expense (item-name + amount mono), TOTAL footer. Loading state shows "Loading…" placeholder. Props: `loading`, `cascade`. |
| `CategorySheet` | `components/CategorySheet.vue` | Full-height searchable sheet listing every active category, grouped by parent group. LLM `suggestions` pinned to the top with a `Sparkles` glyph and a blue-tinted border. Search input is `position: sticky; top: 0` inside the body and autofocuses on open. While typing, sheet collapses to a flat group-prefixed result list. Props: `open`, `suggestions`, `title`. Emits: `select(categoryId)`, `close`. |
| `CorrectionSheet` | `components/CorrectionSheet.vue` | **Legacy.** Earlier doubtful-row correction sheet — superseded by `ExpenseEditSheet`. Still in the tree with passing tests; no view mounts it. Don't add new entry points. |

## LLM view

| Component | File | Contract |
|---|---|---|
| `HealthSummaryCard` | `components/HealthSummaryCard.vue` | Card at top of LLM screen: status dot (`ok` when `healthy > 0`, otherwise `error`) + "N / M healthy" + `+` button (accent-bordered on hover). Strategy + last-switch shown as a separate muted sub-line below the card. |
| `ProviderCard` | `components/ProviderCard.vue` | Per-provider tile. **Card body** (tappable, opens edit sheet): priority chip `[N]` mono, status dot, label, rate-limit countdown pill (warning-tinted, mono `Xs`), model code (mono), optional `last_error_detail` line in `--danger`. **Usage row**: 3-px bar + `used / limit` mono numbers when a daily cap is set, falls through to `N calls today · no daily cap` otherwise; bar fills `--accent` until 80 %, then `--warning`. **Latency chip** inline right of "today" / "no daily cap", `--warning` when > 3000 ms. **Action row** (bottom, divider above): chevron-up / chevron-down / power. Actions use `@click.stop`. Power dims to `--muted-2` when disabled. |
| `ProviderSheet` | `components/ProviderSheet.vue` | Bottom sheet (custom shell — predates `BaseSheet`) for add / edit / delete. Preset chips (Groq / OpenRouter / Gemini / Custom) prefill `base_url` and offer model suggestions. Show/hide on API-key field; two-step inline delete confirmation in edit mode (ghost-danger button → inline danger-tinted block with Cancel + Remove). |

## Composables (non-component reusable logic)

| Composable | File | Contract |
|---|---|---|
| `useSwipeRow` | `composables/useSwipeRow.js` | Pointer-event horizontal swipe state for a row. Returns `sliderEl`, `phase`, `isCommit`, `isOpen`, pointer handlers, `shouldFireTap()` (gate the `@click`), and imperative `open()` / `close()`. Args: `panelWidth`, `commitOver` (default 80), `onPrimary` callback fired when a continued swipe past commit threshold releases. 8 px axis-lock; vertical scroll is preserved via `touch-action: pan-y` on the slider. |
| `useExpenseDeleteFlow` | `composables/useExpenseDeleteFlow.js` | Encapsulates the manual-vs-receipt expense delete state machine: `confirmingDelete`, `deleting`, `cascade`, `cascadeLoading`, plus `openDeleteConfirm()` / `confirmDelete()` / `cancelDelete()` / `resetDeleteState()`. Auto-fetches `cascade` from `GET /api/receipts/:id?include=expenses` for the receipt path. Gated on `isOnline`. |
| `useKeyboardVisible` | `composables/useKeyboardVisible.js` | `visualViewport`-based detection of soft-keyboard presence. Returns `keyboardVisible`, `keyboardBottom`. Used by `KeyboardSaveBar`. |
| `useOnline` | `composables/useOnline.js` | Reactive `isOnline`. Used by every view to gate write actions and refresh. |
| `useStaleCache` | `composables/useStaleCache.js` | localStorage-backed cache with dirty + last-fetched timestamps. Used by review and income stores. |
| `useCatalogManage` | `composables/catalogManage.js` | Local manage-mode state + action runner shared by every catalog-editing surface inside `ExpenseForm`. |

## Legacy / supporting

| Component | File | Notes |
|---|---|---|
| `QueueModal` | `components/QueueModal.vue` | Lists offline-queued entries waiting to flush. Reached via the warning queue-badge in the app header. |
| `EditModal` | `modals/EditModal.vue` | Single remaining classic modal — used by `ExpenseForm`'s manage-list to rename a group/category/event/tag. Net-new creation goes through `InlineCreateRow` / `InlineCreateEvent` instead. |

## Where to add a new component

- **Reusable across screens** → `components/`
- **Owns a top-level view** → `views/` (and add it to the router in `App.vue`)
- **Wraps a foreign API or shared reactive logic** → `composables/`
- **A new sheet/dialog** → `components/`. Default to `BaseSheet`; only roll a custom shell if `ProviderSheet`-style state (preset switcher, key visibility, etc.) demands it.

## Where it's worth duplicating

If a one-off design only ever appears in one screen, don't extract it. Components earn their abstraction by being used in 2+ places. Look at `useSwipeRow` (RuleRow + ExpenseRow + IncomeRow), `useExpenseDeleteFlow` (the receipt vs manual split), and `InlineCreateRow` (group/category/tag) for examples of well-justified extraction.

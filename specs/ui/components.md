# Component Catalog

Every shipped UI component, with its source file and one-line contract. The `.vue` file is the source of truth for props, slots, and events.

## Primitives

| Component | File | Contract |
|---|---|---|
| `IconBtn` | `components/IconBtn.vue` | Square 26–32px icon button with three tones (accent / muted / danger). Props: `icon`, `tone`, `label` (aria), `active`, `disabled`. |
| `StatusDot` | `components/StatusDot.vue` | 8px halo dot for service status. `kind`: `ok` (green) / `rate_limited` (amber) / `off` (muted) / `error` (red). |
| `BaseModal` | `components/BaseModal.vue` | Backdrop + centered modal-content shell with title, submit button, optional error message. Used for legacy add modals — most new flows use bottom sheets instead. |

## Form fields

| Component | File | Contract |
|---|---|---|
| `CatalogSelectField` | `components/CatalogSelectField.vue` | Group/Category/Event/Tag selector. Owns the picker-vs-manage modes (see `patterns.md`). Emits `add`, `manage-toggle`, lifecycle actions for each item. |
| `CurrencyPicker` | `components/CurrencyPicker.vue` | Search + chip grid for ISO currency selection. Lives inside a popover over the currency pill. |
| `TagPicker` | `components/TagPicker.vue` | Wrap-flow of selectable tag chips with leading `#` glyph. Used both in the entry form and inside `InlineCreateEvent` for auto-tags. Emits `update:modelValue` (array of ids). |
| `ManageList` | `components/ManageList.vue` | The eye/eye-off active + inactive list with inline edit/hide/delete buttons. State dividers replace the words "Active" / "Inactive". |

## Inline create (replaces add-modals on the entry form)

| Component | File | Contract |
|---|---|---|
| `InlineCreateRow` | `components/InlineCreateRow.vue` | One-line accent-shell row for Group / Category / Tag. Autofocus, Enter→save, Esc→cancel. Optional `validate` function returns error string. |
| `InlineCreateEvent` | `components/InlineCreateEvent.vue` | Same shell, but full event schema: name + from/to dates + auto-attach checkbox + auto-tags multi-select. Reuses `TagPicker`. |

## Navigation

| Component | File | Contract |
|---|---|---|
| `HeaderSegmented` | `components/HeaderSegmented.vue` | Three-button icon segmented control in the app header. Add (Plus, 56×38, primary) / Review (ListChecks, 36×30, with badge) / LLM (Cpu, 36×30). `v-model:tab`. |

## Add view

| Component | File | Contract |
|---|---|---|
| `ExpenseForm` | `components/ExpenseForm.vue` | The entire entry form: hero row (currency / amount / date), group→category hierarchy, event, tags, comment. Exposes `save()`, `reset()` via `defineExpose`. |
| `KeyboardSaveBar` | `components/KeyboardSaveBar.vue` | Branded Save button that floats above the on-screen keyboard. Mounted only when `useKeyboardVisible` reports the keyboard up. Prevents users mistaking the keyboard's "done" key for the form's Save. |
| `QrScanner` | `components/QrScanner.vue` | Camera viewfinder for fiscal QR codes. Exposes `start()`, `stop()`. Emits `scan`, `error`. |

## Review view

| Component | File | Contract |
|---|---|---|
| `RuleRow` | `components/RuleRow.vue` | Row for a classification rule (one per unique item-name). Renders **doubtful** rows (graded left-border by `confidence_level` 1/2/3 → error/warning/muted-warning, tinted bg, name+store, inline action chips, edit button) and **certain** rows (plain card, group › category breadcrumb, chevron). Whole row is the tap target. Doubtful rows expose multiple **fast-path approve chips** (suggested with `Sparkles` icon if it differs from current, up to 2 alternatives, then frequent-category picks). Supports swipe-to-act via `useSwipeRow` — left swipe reveals **Edit** + **Approve** (doubtful) or **Edit**-only (certain). Props: `item`. Emits: `tap`, `approve({ item, categoryId })`. |
| `ExpenseRow` | `components/ExpenseRow.vue` | Individual receipt-line row in the EXPENSES section. Top: item name (or store fallback), trailing amount + currency in `--font-num`. Bottom: store + date when item-name is the primary, plus group › category breadcrumb. Swipe-to-act via `useSwipeRow` reveals an **Edit** button. Props: `expense`. Emits: `tap`. |
| `ExpenseEditSheet` | `components/ExpenseEditSheet.vue` | Bottom sheet for editing a single expense or correcting a rule. Inline `CategorySheet` launcher, tag toggles, event select. Shows the **scope selector** (Only this / Last month / This year / All history) when editing a receipt-linked expense; shows an **"Update rule"** checkbox when the source expense has an existing rule. Doubtful-row corrections (rule path) skip the scope selector and always patch with `scope: "all"`. Props: `open`, `expense`, `suggestions`, `ruleItem`. Emits: `close`. |
| `CategorySheet` | `components/CategorySheet.vue` | Searchable bottom sheet listing every active category, grouped by parent group, with LLM `suggestions` pinned to the top (each prefixed with a `Sparkles` glyph). Autofocuses the search input on open. Props: `open`, `suggestions`, `title`. Emits: `select(categoryId)`, `close`. |
| `CategoryQuickPicks` | `components/CategoryQuickPicks.vue` | Wrap-flow of pill buttons over a list of frequently-used categories. Surfaces an "all categories" affordance to the parent. Props: `categories`. Emits: `select(categoryId)`. |
| `CorrectionSheet` | `components/CorrectionSheet.vue` | **Legacy.** Earlier doubtful-row correction sheet — superseded by `ExpenseEditSheet` for the Review flow. Still in the tree (with passing tests) but no view mounts it. Don't add new entry points; new screens that need the same flow should mount `ExpenseEditSheet`. |

## LLM view

| Component | File | Contract |
|---|---|---|
| `HealthSummaryCard` | `components/HealthSummaryCard.vue` | Card at top of LLM screen: status dot + "N / M healthy" + add (`+`) button. Strategy + last-switch shown as a separate sub line below the card. |
| `ProviderCard` | `components/ProviderCard.vue` | Per-provider tile: priority chip, status dot, label, model code, rate-limit pill, usage bar (or "no daily cap"), inline latency, **bottom action row** (up, down, test ⚡, power). Card body is tappable → opens edit sheet. |
| `ProviderSheet` | `components/ProviderSheet.vue` | Bottom sheet for add/edit/delete. Preset chips (Groq / OpenRouter / Gemini / Custom) prefill base_url and offer model suggestions. Show/hide on API-key field; two-step inline delete confirmation in edit mode. |

## App shell

| Component | File | Contract |
|---|---|---|
| `App` | `App.vue` | Top-level shell — sticky header (brand + version + queue badge + segmented), main view router, queue modal, global toast. |
| `AddView` | `views/AddView.vue` | Mounts `ExpenseForm`, `QrScanner`, the bottom action bar (Scan / Save), and `KeyboardSaveBar`. |
| `ReviewView` | `views/ReviewView.vue` | Two-section list (NEEDS REVIEW + EXPENSES). Owns scroll container, two `IntersectionObserver` sentinels (one per section), refresh control, **Confirm all** bulk action, and the edit sheet open/close. |
| `LLMView` | `views/LLMView.vue` | Owns refresh timer (30s polling), opens add/edit `ProviderSheet`. |

## Composables (non-component reusable logic)

| Composable | File | Contract |
|---|---|---|
| `useSwipeRow` | `composables/useSwipeRow.js` | Pointer-event horizontal swipe state for a row. Returns `sliderEl` (ref to slide-translating element), `phase`, reactive `isOpen` / `isCommit`, pointer handlers, `shouldFireTap()` (use to gate `@click` against drag-induced clicks), and imperative `open()` / `close()`. Args: `panelWidth`, `commitOver` (default 80), `onPrimary` callback fired when a continued swipe past commit threshold releases. 8px axis-lock; vertical scroll is preserved via `touch-action: pan-y` on the slider. |
| `useKeyboardVisible` | `composables/useKeyboardVisible.js` | `visualViewport`-based detection of soft-keyboard presence. Used by `KeyboardSaveBar`. |
| `useOnline` | `composables/useOnline.js` | Reactive `isOnline`. Used by every screen to gate write actions and refresh. |
| `useStaleCache` | `composables/useStaleCache.js` | localStorage-backed cache with dirty + last-fetched timestamps. Used by review store. |

## Legacy / supporting

| Component | File | Notes |
|---|---|---|
| `QueueModal` | `components/QueueModal.vue` | Lists offline-queued entries waiting to flush. |
| `EditModal`, `AddGroupModal`, etc. | `modals/` | Older modal-based catalog editors. Still used for some edit flows; net-new creation goes through `InlineCreateRow`/`InlineCreateEvent` inside `ExpenseForm`. |

## Where to add a new component

- **Reusable across screens** → `components/`
- **Owns a top-level tab's layout** → `views/`
- **Wraps a foreign API or shared reactive logic** → `composables/` (not a component)
- **Adds a modal/sheet for a CRUD pattern** → `components/` and prefer a bottom sheet over `BaseModal` for any new flow

## Where it's worth duplicating

If a one-off design only ever appears in one screen, don't extract it. Components earn their abstraction by being used in 2+ places. Look at `RuleRow` / `ExpenseRow` (both built on `useSwipeRow`) and `InlineCreateRow` (re-used for group/category/tag) for examples of well-justified extraction.

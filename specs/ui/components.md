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
| `RuleRow` | `components/RuleRow.vue` | Unified row for both doubtful and certain entries. Doubtful → warning left-border, tinted bg, confidence pill, optional suggestion chip. Certain → plain card, top-category breadcrumb. Tap → emits `tap`. |
| `CorrectionSheet` | `components/CorrectionSheet.vue` | Bottom sheet for category correction. Includes the **scope selector** (Last expense / Last month / This year / All history) for certain rows; doubtful rows force "all" (rule-creation path). |

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
| `ReviewView` | `views/ReviewView.vue` | Owns scroll container, IntersectionObserver, refresh control, correction-sheet open/close. |
| `LLMView` | `views/LLMView.vue` | Owns refresh timer (30s polling), opens add/edit `ProviderSheet`. |

## Legacy / supporting

| Component | File | Notes |
|---|---|---|
| `QueueModal` | `components/QueueModal.vue` | Lists offline-queued entries waiting to flush. |
| `EditModal`, `AddGroupModal`, etc. | `modals/` | Older modal-based catalog editors. Still used for some edit flows; net-new creation goes through `InlineCreateRow`/`InlineCreateEvent` inside `ExpenseForm`. |

## Where to add a new component

- **Reusable across screens** → `components/`
- **Owns a top-level tab's layout** → `views/`
- **Wraps a foreign API** → `composables/` (not a component)
- **Adds a modal/sheet for a CRUD pattern** → `components/` and prefer a bottom sheet over `BaseModal` for any new flow

## Where it's worth duplicating

If a one-off design only ever appears in one screen, don't extract it. Components earn their abstraction by being used in 2+ places. Look at `RuleRow` (re-used for doubtful + certain) and `InlineCreateRow` (re-used for group/category/tag) for examples of well-justified extraction.

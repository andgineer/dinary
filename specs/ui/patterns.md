# Cross-Cutting Patterns

Patterns that recur in multiple screens. When a new pattern starts to repeat, document it here so future code reuses rather than reinvents.

## Bottom sheets

Used for: category correction, provider CRUD, currency picker (popover variant), any flow that needs to feel modal but be one-thumb-reachable.

### Anatomy

```
┌──────────────────────────────────────┐
│              ════                    │ 36×4 drag handle, --border-strong
│                                      │
│  EYEBROW                       ✕    │ uppercase 11px label + close
│  Sheet title                         │ 16px / 600
│  optional · sub · meta               │ 12px / muted
│                                      │
│  ┌────────────────────────────────┐  │ optional info banner
│  │ ✨ Helpful context …           │  │ accent-tinted, 1px accent/0.2 border
│  └────────────────────────────────┘  │
│                                      │
│  ╔═════════════════════════════════╗ │
│  ║                                 ║ │ scrollable body
│  ║                                 ║ │
│  ║                                 ║ │
│  ╚═════════════════════════════════╝ │
│  ────────────────────────────────    │ 1px border --border
│  context label          [Confirm]    │ sticky footer
└──────────────────────────────────────┘
```

### Rules

- Mount via `<Teleport to="body">` and pair `<Transition name="scrim">` (opacity) + `<Transition name="sheet">` (`translateY(100%)` → `translateY(0)`, `cubic-bezier(0.32, 0, 0.67, 0)`).
- Scrim: `rgba(0, 0, 0, 0.55)`, click-to-close.
- Sheet: `background: var(--surface)`, `border-radius: 18px 18px 0 0`, `min-height: 50vh`, `max-height: 80vh`, shadow `0 -4px 24px rgba(0,0,0,0.35)`.
- Drag handle is decorative — actual close is via the ✕, the scrim, or scrolling down past max-height. No real drag-to-dismiss.
- Footer is sticky with `padding-bottom: calc(0.75rem + env(safe-area-inset-bottom, 0px))` so it clears the home indicator.
- The primary action is `flex: 0 0 auto`; the context label takes `flex: 1` and truncates with ellipsis.

References: `ExpenseEditSheet.vue`, `ProviderSheet.vue`, `CategorySheet.vue`.

## Scope selector

When a correction can apply to varying breadth of history, the user picks the scope explicitly. Lives inside `ExpenseEditSheet` for receipt-linked expenses (`expense.receipt_id != null`); doubtful-row corrections from `RuleRow` skip the sheet entirely via fast-path approve chips and force `scope: "all"` (correcting a doubtful row is fundamentally rule creation).

### Anatomy

```
APPLY CHANGE TO:
  ⦿ Only this
  ⦾ Last month
  ⦾ This year
  ⦾ All history
─────────────────────────────────────── ← 1px separator below
```

### Rules

- 4 options: `single` / `month` / `year` / `all`. Default to `single` — the safest scope.
- Native radio inputs (`<input type="radio">`) with `accent-color: var(--accent)`.
- Uppercase 11px label above, options stacked below.
- Sits at the top of the sheet body, separated from the rest by a 1px `--border` line.
- Don't show the selector when scope is forced or irrelevant (manual expenses with no `receipt_id`; rule-correction path). Hiding > disabling.
- When the source expense already has a rule (`has_rule === true`), show a sibling **"Update rule"** checkbox below — applying a scope > `single` and ticking the box updates the rule mapping too.

References: `ExpenseEditSheet.vue`.

## Picker vs Manage

Every catalog-backed select supports two modes, triggered by separate buttons on the same row.

### `+` (plus) — inline create
Drops an `InlineCreateRow` (or `InlineCreateEvent`) below the field. See "Inline create" below.

### ⚙ (cog) — manage
Toggles a `ManageList` below the field. The list shows active items (with edit + hide buttons) above inactive items (with edit + restore + delete buttons), separated by the eye / eye-off state divider.

### Rules

- Both buttons live in the section header row, right-aligned, with the field's label/glyph on the left.
- Tapping ⚙ toggles to ✕ while open (same button, swapped icon).
- One section's manage panel can be open while another section's add panel is also open. Don't auto-collapse siblings.

References: `CatalogSelectField.vue`, `ManageList.vue`.

## Inline create

Replaces the older add-modal pattern. Net-new catalog items are created without leaving the form context.

### Anatomy (`InlineCreateRow`)

```
┌──────────────────────────────────────────┐
│  +   [autofocused input        ]  ✕  ✓   │ accent border + glow shell
└──────────────────────────────────────────┘
```

### Rules

- Shell: `border: 1px solid var(--accent)`, `background: color-mix(in oklab, var(--accent) 10%, var(--field-deep))`, `box-shadow: 0 0 0 3px color-mix(in oklab, var(--accent) 15%, transparent)`, `border-radius: 8px`.
- Leading `+` icon is accent-tinted, decorative.
- Input autofocuses on mount. Enter → save, Esc → cancel.
- Empty input on save → emit `cancel` silently (don't show validation error for that).
- Optional `validate(value)` prop for kind-specific rules (e.g. `validateTagName` for tags).
- ✓ button hovers to `--success`; ✕ hovers to `--text`.

### When to use `InlineCreateEvent` instead

Events have richer schema (name + date range + auto-attach + auto-tags). Use the dedicated `InlineCreateEvent` component — same accent shell, larger body. Don't try to make `InlineCreateRow` polymorphic.

References: `InlineCreateRow.vue`, `InlineCreateEvent.vue`.

## State dividers

Used in `ManageList` to separate active and inactive items without using text labels.

### Anatomy

```
👁  ─────────────────────────────         ← solid gradient, accent-tinted
  …active items (edit + hide)…
👁⃠  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─               ← dashed line, muted
  …inactive items (edit + restore + delete)…
```

### Rules

- **Active divider** — `Eye` icon (12px) on the left, accent color, then a 1px solid gradient line from accent to transparent.
- **Inactive divider** — `EyeOff` icon (12px) on the left, muted-2 color, then a 1px dashed `--border-strong` line.
- Inactive item names get `text-decoration: line-through` and `color: var(--muted)`.

Apply this same active/inactive visual recipe anywhere a binary state pair shows up (on/off, published/draft, enabled/disabled, etc.) — don't fall back to words.

References: `ManageList.vue`.

## Status dot

Used in `ProviderCard` and `HealthSummaryCard`. 8px solid circle with a soft halo.

### Kinds

| Kind | Color | Use |
|---|---|---|
| `ok` | `--success` (green) | Enabled, last call succeeded |
| `rate_limited` | `--warning` (amber) | Enabled but currently rate-limited (`rate_limited_until > 0`) |
| `off` | `--muted-2` | Disabled by user |
| `error` | `--error` (red) | Enabled but last call failed |

### Rules

- Halo is the same color at ~30% alpha, 2–3px outside the dot via `box-shadow` or radial gradient.
- The dot does not animate.
- Always paired with text — the dot is never the sole carrier of meaning.

References: `StatusDot.vue`.

## Keyboard handling

The PWA runs full-screen on phones; the soft keyboard occludes the bottom action bar. Two complementary mitigations:

### `useKeyboardVisible` composable

Tracks `window.visualViewport` and exposes `keyboardVisible` + `keyboardBottom`. Threshold: keyboard considered visible if `viewport.height / window.innerHeight < 0.75`.

### `KeyboardSaveBar`

While `keyboardVisible` is true, mount a branded Save button at `position: fixed; bottom: <keyboardBottom>px`. The button:

- Uses `--accent` with the Save icon (matches the bottom action bar).
- Is full-width up to `max-width: 480px`.
- Animates in with a 0.15s slide-up.

**Why both:** the form's bottom action bar is hidden by the keyboard, but its Save key (or `enterkeyhint`) is too easy to mistake for the keyboard's "Done" or "Return" key. A visually distinct branded Save bar removes the ambiguity and makes commit explicit.

References: `composables/useKeyboardVisible.js`, `components/KeyboardSaveBar.vue`, `views/AddView.vue`.

## Toasts

Single global `<div class="toast">` lives in `App.vue`, driven by `useToastStore`.

### Rules

- Drops in from top with overshoot easing (`cubic-bezier(0.34, 1.56, 0.64, 1)`, 380ms).
- Pill shape, `border-radius: 999px`.
- Tap to dismiss (`cursor: pointer`, `pointer-events: auto` while visible).
- Three types: `success` (green bg, black text), `error` (red bg, white text), `info` (`--surface-2` bg, white text, 1px `--border-strong`).
- Single-line, max-width `calc(100% - 2rem)`, ellipsis on overflow.

Don't queue multiple toasts. Replace.

## Swipe-to-act

List rows that have a primary tap action plus 1–2 secondary actions reveal those actions via a left swipe instead of cluttering the row chrome. Used in `RuleRow` (Edit + Approve) and `ExpenseRow` (Edit). All wiring lives in `composables/useSwipeRow.js`.

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
| Tap an inline action chip (RuleRow approve chips) | `@click.stop` + emits `approve`. Never bubbles to row tap. |
| Swipe left ~`commitOver` px (default 80) | Row slides left, reveals action panel docked right. |
| Continue swiping past `panelWidth` | `isCommit` flips true — primary panel button widens to ~2× and brightens; secondary collapses. |
| Release inside revealed zone | Snaps fully open. User can tap a panel button or tap the row to close. |
| Release past commit zone | `onPrimary` fires: Approve on doubtful, Edit-sheet on certain / expense rows. Row snaps closed. |
| Vertical scroll | 8px axis-lock detects vertical intent and disables horizontal drag for the gesture. |
| Open another row | Store-mediated: every row writes its id to `review.openRowId` on open; other rows watch and close themselves. |

### Rules

- **Wrapper is the clip surface** (`overflow: hidden`, fully opaque background, holds the warning left-border on `RuleRow` so it doesn't slide off-screen).
- **Slider must be opaque.** The action panel sits behind it. A translucent slider bleeds button color through the row at rest.
- **Don't put the row's tint into shorthand `background`** mixed with `var(--bg)` — browsers drop the whole declaration. Use `background-color` for the solid base and `background-image: linear-gradient(...)` for the tint.
- **`touch-action: pan-y`** on the slider — vertical page scroll keeps working through the row.
- **Don't replace row content during commit phase.** The commit cue lives on the *panel button* (widens + brightens). The user keeps reading what they're acting on.
- **Single open row** — opening any swipe row closes the currently-open one via `review.openRowId`. No multi-open state to reason about.
- **`shouldFireTap()` gates the `@click` handler** so a drag-induced click (release after a horizontal move) doesn't fire `tap`.

References: `composables/useSwipeRow.js`, `components/RuleRow.vue`, `components/ExpenseRow.vue`.

## Confirm all

Batch-confirm pattern for queues where every pending item shares the same primary action. Currently used at the end of the NEEDS REVIEW list to confirm every visible doubtful rule in one PATCH.

### Rules

- Mount the button only when **(a)** the list has fully paginated (`!hasMore`) and **(b)** at least one actionable item remains.
- Centered, full-width-capped, pill-shaped (`border-radius: 999px`), green-tinted (same recipe as the approve chip): `rgba(34, 197, 94, 0.15)` fill, `rgba(34, 197, 94, 0.3)` border, `--success` text.
- Label format: `Confirm all (N)` where N is the live count.
- Tap → single API call (`confirmAllRules(ruleIds)`), then refresh any dependent sections (e.g. EXPENSES, so the user sees the new classifications immediately).
- Don't auto-confirm. The user always has to tap. Don't put a countdown.
- Gate on `isOnline` like every other write action.

References: `views/ReviewView.vue`.

## Skeleton rows

Use a generic 72px-tall card with the same border and radius as the real row, pulsing opacity 1 ↔ 0.4 on a 1.4s loop. Two skeletons is enough to communicate "more loading."

```css
@keyframes skeleton-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
```

Show skeletons only when **fetching** new pages, not during initial load (initial load shows nothing — the user already sees the chrome).

References: `ReviewView.vue`.

## Infinite scroll

`IntersectionObserver` on a hidden sentinel `<div>` near the bottom of the list.

### Rules

- `rootMargin: '120px'` so loading kicks off before the user hits the end.
- Gate on `!loading && hasMore && isOnline` to avoid duplicate calls.
- Page size: 20 items (server side).
- Render skeleton rows below the last item while loading.
- When `!hasMore`, render `─── end · N loaded ───` in muted-2 monospace. Exception: the NEEDS REVIEW section in `ReviewView` instead shows the **Confirm All** button at the end — the `─── end` footer is omitted there.
- Don't render the sentinel when `!hasMore` (no false triggers).

References: `ReviewView.vue`.

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

Gate write actions and refresh buttons; render an `Offline — …` notice at the top of the view if there's also no cached data to show.

References: `composables/useOnline.js`, `LLMView.vue`, `ReviewView.vue`.

## Provider sheet form

The add/edit/delete sheet for LLM providers — a richer specialization of the bottom-sheet pattern.

### Layout

```
EYEBROW (ADD PROVIDER / EDIT PROVIDER)
Title (label · model in edit)

[Groq] [OpenRouter] [Gemini] [Custom]   ← preset chips

LABEL          [____________]
BASE URL       [____________]            ← mono
MODEL          [____________]            ← mono
               [ suggestion chips ]      ← when a preset matches
API KEY        [••••••••]    [👁]        ← show/hide toggle

[⏻] Enabled in failover pool      [○─]   ← toggle row

────── dashed separator (edit only) ──────
[🗑 Remove provider]                       ← ghost-danger button
  on tap →
  ┌────────────────────────────────┐
  │ Remove <label>? Logs are kept. │     ← inline confirmation in danger bg
  │ [Cancel]            [🗑 Remove]│
  └────────────────────────────────┘

[Cancel]                    [✓ Save]
```

### Rules

- Preset chips prefill base_url; if the preset has a model list, those appear as one-tap suggestion chips below the model input.
- API-key field in edit mode is empty with the hint *"Leave empty to keep the existing key"* — never display the stored secret.
- Delete is two-step: the ghost button → inline danger-tinted confirmation. Never confirm via JS `confirm()` for this kind of action.

References: `ProviderSheet.vue`.

## Connector hierarchy (parent → child)

For any parent → child / belongs-to / narrows-into relationship in a vertical form.

```
[ parent select       ▾   +⚙ ]
 │
 └─►  [ child select  ▾   +⚙ ]
```

### Rules

- 1px vertical line in `--border-strong`, starting at the bottom of the parent and extending to the row of the child.
- L-elbow at the bottom — 1px horizontal segment.
- Child is indented `padding-left: ~30px` past the line.
- The indent IS the label. Don't write "Group" / "Category" above the fields.

References: this pattern is no longer used in `ExpenseForm.vue` (the group→category dropdowns were replaced by `CategoryQuickPicks` + `category-pick-btn`). The pattern document is kept for any future parent→child select pairs.

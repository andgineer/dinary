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

References: `CorrectionSheet.vue`, `ProviderSheet.vue`.

## Scope selector

When a correction can apply to varying breadth of history, the user picks the scope explicitly. Used inside `CorrectionSheet` for certain rows; doubtful rows force `"all"` (because correcting a doubtful row is fundamentally rule creation).

### Anatomy

```
APPLY CHANGE TO:
  ⦿ Last expense
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
- Don't show the selector when scope is forced (e.g. doubtful-row corrections always apply `all`). Hiding > disabling.

References: `CorrectionSheet.vue`.

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
- When `!hasMore`, render `─── end · N loaded ───` in muted-2 monospace.
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

References: `ExpenseForm.vue` (group → category).

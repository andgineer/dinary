# Design Language

The visual language of Dinary: a dark, utilitarian PWA where typography and color carry the meaning. Tokens live in `webapp/src/assets/base.css`.

## Color

### Background scale

| Token | Use |
|---|---|
| `--bg` (#1a1a2e) | Page background, row slider background |
| `--surface` (#16213e) | Sticky header, action bar, sheets, modals |
| `--surface-2` (#0f3460) | Secondary action buttons, button-pressed states, swipe-panel default fill |

The three blues read as one continuous dark surface, deepest to lightest. Don't pull a fourth shade.

### Surface fills (over `--bg`)

| Token | Use |
|---|---|
| `--field` (`rgba(255,255,255,0.04)`) | Default input / select / card fill |
| `--field-deep` (`rgba(0,0,0,0.18)`) | Recessed surfaces — manage panels, picker bodies, sheet sub-blocks |

These are alpha-on-white, so they tint with the underlying surface. Never use a flat hex for a field — always one of these.

### Borders

| Token | Use |
|---|---|
| `--border` (`rgba(255,255,255,0.08)`) | Default 1px field/card border |
| `--border-strong` (`rgba(255,255,255,0.12)`) | Hierarchy connectors, dashed manage borders, drag handles |

### Foreground scale

| Token | Use |
|---|---|
| `--text` (#eee) | Body text, input values |
| `--text-muted` / `--muted` (#94a3b8) | Labels, secondary content, icon defaults |
| `--muted-2` (#64748b) | Tertiary metadata (date ranges, timestamps under titles, separator chevrons) |

### Accent / status hues

| Token | Hex | Use | Never |
|---|---|---|---|
| `--accent` | `#e94560` | Focus rings, tag-picker selected chips, active manage-list divider, `IconBtn[tone=accent]`, doubtful-row primary commit cue, queue-chip "ready", currency-chip selected | Primary save action (use a per-context color instead, see below) |
| `--success` | `#22c55e` | **Income view's primary color** (currency pill, Save bar, IncomeRow border, year totals), status-dot "ok", approve chip + Confirm-all button, success toast | Generic primary actions outside the Income context |
| `--warning` | `#f59e0b` | Doubtful left-border (c2), rate-limit pill, queue badge, NEEDS REVIEW label, dev banner | Background fills (except low-alpha tints inside cards) |
| `--error` / `--danger` | `#ef4444` / `#e94560` | Destructive confirm button, status-dot "error", inline error text, doubtful left-border (c1) | Anything that isn't an error or destructive action |
| `--expense` | `#f97316` | **Add view's primary color** (Add tab, currency pill, Save & Scan buttons, selected event chip, selected category quick-pick) | Anything outside the expense-entry context |

### Per-context primary color — the rule

Every top-level view picks **one** primary color and uses it for its main commit action and its currency pill. The view's color is what tells the user "this is where you are":

| View | Primary | Where it shows |
|---|---|---|
| **Add** | `--expense` (orange) | Add tab fill, hero currency pill, bottom Save + Scan, KeyboardSaveBar, selected event chip, selected `CategoryQuickPicks` pill |
| **Income** | `--success` (green) | Income overflow-menu icon, IncomeForm/IncomeEditSheet currency pill, hero amount focus underline, bottom Save bar, KeyboardSaveBar, IncomeRow left-border, year-total label, refresh focus, IncomeRow swipe-panel |
| **Review** | dual: NEEDS REVIEW uses `--warning` + `--success` (approve chip); EXPENSES edit uses sky-blue `#60a5fa` for `ExpenseEditSheet`'s currency pill and Save button | Review tab fill (sky-blue), edit-sheet Save, Confirm-all button (green) |
| **LLM** | `--accent` (red) for add, but the screen itself is mostly neutral | `HealthSummaryCard`'s `+`, refresh focus |

**The Review edit-sheet sky-blue (`#60a5fa`)** is an inline literal (no token yet). If you find yourself needing it elsewhere, promote it to a `--review` token in `base.css` in the same PR.

**Never invent a hue** beyond what's listed. If a new view needs a primary color, pick one of the four above and own it.

## Typography

| Token | Stack | Use |
|---|---|---|
| `--font` | `system-ui, -apple-system, sans-serif` | All UI text |
| `--font-num` | `"JetBrains Mono", ui-monospace, SFMono-Regular, monospace` | Amounts, dates, ranges, ISO codes, IDs, latency, version strings |

Numeric/code values *always* use `--font-num`. Body text never does.

### Scale

The codebase favors a small set of sizes:

- **Hero amount input** — 2rem / 32px (mono, weight 500, right-aligned, bottom-line underline; underline turns the view's primary color on focus). Used on Add, Income create, Income edit-sheet, Expense edit-sheet.
- **Sticky header title** — 1.25rem / 20px, weight 600 (`Dinary`).
- **Confirm-sheet title** — 1.05rem, weight 700.
- **Card / row primary** — 0.9375rem / 15px, weight 600 (700 on doubtful rows).
- **Body / input value** — 0.9rem.
- **Sub / muted line** — 0.8rem.
- **Section eyebrow / label** — 0.6875rem / 11px, uppercase, letter-spacing 0.07em, weight 700.
- **Pill / badge / chip** — 0.7–0.72rem, weight 600–700.
- **Tertiary metadata** — 0.68–0.72rem.

If a new screen needs another size, use the existing ones first.

## Spacing

The codebase uses rem-based spacing. The implicit scale:

- **0.25rem (4px)** — gap between icon and label inside a tight chip
- **0.4rem (~6px)** — gap between adjacent icon-buttons in an action row
- **0.5rem (8px)** — typical chip-row gap, in-card padding-top, row vertical margin
- **0.625–0.75rem (10–12px)** — card inner padding, sheet inner padding
- **1rem (16px)** — section gap, header padding
- **1.25rem (20px)** — main content side padding

### Radii

- **6–8px** — small inputs, segmented buttons, manage rows, chips
- **9–10px** — cards (provider, rule, expense, field shell), selected category buttons
- **11px** — `HeaderSegmented` container
- **12px** — category-card outer shell, primary action buttons in `AddView`'s action bar, empty-state cards
- **14px** — primary Save button in `IncomeView`'s action bar
- **18px (top corners only)** — every bottom sheet
- **999px** — pills, badges, tag chips, the currency pill (despite being an 8px rect — pills are by intent, not shape), the Confirm-all button, FAB-style buttons

Don't pick a 14 or 16 in between.

## Density

Density is currently a single mode — comfortable. There is no compact prop today; if you compress later, follow the rule:

| | Comfortable | Compact |
|---|---|---|
| Section gap | 1rem | ~0.75rem |
| Card inner padding | 0.625rem 0.75rem | 0.5rem 0.6rem |
| Row gap inside cards | 4–6px | 2–4px |

Compact would be an explicit prop on every screen, not a global mode.

## Iconography

Icons come from `lucide-vue-next`. Sizes used in the codebase:

- **22px** — primary-action icon (HeaderSegmented Add/Review, hero save bar)
- **20px** — bottom action bar icons (Save, QR)
- **18px** — large body icons (confirm-sheet Trash, KeyboardSaveBar Save)
- **15–16px** — header segment dots, sheet close, provider-card actions, list-row trailing
- **13–14px** — inline manage-row icons (edit, eye, trash), swipe-panel labels
- **9–12px** — decorative inline (sparkle, hash, drag handle, status-dot adjacent)

Always set `aria-hidden="true"` and stroke color via `currentColor` so the icon tone follows context. Default tone is `--muted`; accent on hover/active, view-primary inside contexts that own a color.

The verb→icon table is canonical — see `future-screens-guide.md`. Don't introduce a synonym.

## Motion

- **120–180ms** — hover / active transitions on background, color, transform.
- **260–280ms** — sheet slide-in (`cubic-bezier(0.32, 0, 0.67, 0)`), scrim fade.
- **0.38s cubic-bezier(0.34, 1.56, 0.64, 1)** — toast drop-in (overshoot easing). Defined in `base.css`.
- **0.2s cubic-bezier(0.4, 0, 0.2, 1)** — swipe-row snap.
- **0.3s** — usage-bar fill grow.
- **1.4s ease-in-out infinite** — skeleton pulse (opacity 1 ↔ 0.4).
- **No keyframe animation longer than ~400ms** for entry/exit, no animations longer than the skeleton pulse for steady-state. Anything longer feels broken.

`prefers-reduced-motion` is not currently honored — when adding any new motion, gate keyframes with `@media (prefers-reduced-motion: reduce)`.

## Layout

- **Mobile-first, max-width 480px.** Centered with `margin: 0 auto`. Above 600px viewport, content padding doubles (`@media (min-width: 600px)`).
- **Sticky header** at top. Sticky bottom action bar in `AddView` and `IncomeView`; no bottom bar in `ReviewView` or `LLMView`.
- **Safe areas** — every bottom-fixed element uses `padding-bottom: calc(<base> + env(safe-area-inset-bottom, 0px))`.
- **Hit target ≥ 44px** for any thumb-reachable control. The HeaderSegmented `•••` button is 36×30 because it lives inside a 44+px container; the surrounding pill is the hit target.
- **No hover-only affordances.** Hover effects are bonuses; everything must work after a single tap.

## Anti-patterns

| ✗ Don't | ✓ Do |
|---|---|
| Pad screens with placeholder cards or stats | Leave space — if a screen feels empty, the design is probably done |
| Add a label to a self-explanatory field | Drop the label; if meaning isn't obvious, add a glyph (`#`, cal, search) |
| Invent a new hue for a "new" semantic | Reuse a per-context primary, `--accent`, `--warning`, `--success`, or stay muted |
| Use plain text for state ("Active" / "Inactive") | Use the eye / eye-off + line pattern from `ManageList` |
| Mix label words with icon buttons in one row | Pick one register and stay there |
| Use `--font` for amounts or IDs | Use `--font-num` |
| Render a primary action with `--surface-2` | Primary actions are always the view's primary color |
| Use `--accent` for a context's primary action | Use the per-context primary color (orange / green / sky-blue) |
| Use JS `confirm()` for destructive actions | Bottom-sheet confirm via `ConfirmDeleteSheet` (see `patterns.md`) |

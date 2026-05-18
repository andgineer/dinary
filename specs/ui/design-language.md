# Design Language

The visual language of Dinary: a dark, utilitarian PWA where typography and color discipline carry the meaning. Tokens live in `webapp/src/assets/base.css`.

## Color

### Background scale

| Token | Use |
|---|---|
| `--bg` | Page background |
| `--surface` | Sticky header, action bar, sheets, modals |
| `--surface-2` | Secondary action button, button-pressed states |

The three blues read as one continuous dark surface, deepest to lightest. Don't pull a fourth shade.

### Surface fills (over `--bg`)

| Token | Use |
|---|---|
| `--field` | Default input / select / card fill |
| `--field-deep` | Recessed surfaces — manage panels, picker bodies, sheet inputs |

These are alpha-on-white, so they tint with the underlying surface. Never use a flat hex for a field — always one of these.

### Borders

| Token | Use |
|---|---|
| `--border` | Default 1px field/card border |
| `--border-strong` | Hierarchy connectors, dashed manage borders, drag handles |

### Foreground scale

| Token | Use |
|---|---|
| `--text` (a.k.a. `--fg` in older docs) | Body text, input values |
| `--muted` / `--text-muted` | Labels, secondary content, icon defaults |
| `--muted-2` | Tertiary metadata (date ranges, timestamps under titles) |

### Accents — strict discipline

| Token | Use | Never |
|---|---|---|
| `--accent` (red `#e94560`) | Primary action, currency pill, selected state, focus ring, scan button | Section backgrounds, body text, inactive states |
| `--success` (green) | Success toast, status-dot "ok" | Primary action |
| `--warning` (amber) | Doubtful row left-border, rate-limit pill, queue badge, "to fix" badge | Background fills (except low-alpha tints inside cards) |
| `--error` / `--danger` (`#ef4444` / `#e94560`) | Error toast, destructive confirm, status-dot "error" | Anything that isn't an error or destructive action |

**Never invent a hue.** If a new color feels needed, it's almost always a missing pattern, not a missing color. Ask before adding.

## Typography

| Token | Stack | Use |
|---|---|---|
| `--font` | Inter, system-ui | All UI text |
| `--font-num` | JetBrains Mono | Amounts, dates, ranges, ISO codes, IDs, latency, version strings |

Numeric/code values *always* use `--font-num`. Body text never does.

### Scale

The codebase favors a small set of sizes:

- **Hero amount:** 2rem / 32px (font-num, weight 500, right-aligned underline)
- **Section title (sticky header):** 1.25rem / 20px, weight 600
- **Card title / row name:** ~15px, weight 600 (700 when emphasized, e.g. doubtful rows)
- **Body / input value:** ~14px
- **Sub / muted line:** 0.8rem / ~13px
- **Eyebrow / section label:** 0.6875rem / 11px, uppercase, letter-spacing 0.07em, weight 700
- **Pill / badge:** ~11px, weight 700
- **Tertiary metadata:** 0.7rem / ~11px

If a new screen needs another size, use the existing ones first.

## Spacing

The codebase uses rem-based spacing in `.vue` files. The implicit scale:

- **0.25rem (4px)** — gap between icon and label inside a tight chip
- **0.4rem (~6px)** — gap between adjacent icon-buttons in an action row
- **0.5rem (8px)** — typical chip-row gap, in-card padding-top
- **0.75rem (12px)** — card inner padding (vertical), sheet inner padding
- **1rem (16px)** — section gap, header padding
- **1.25rem (20px)** — main content side padding

### Radii

- **6–8px** — small inputs, segmented buttons, manage rows, chips
- **9–10px** — cards (provider, rule, field shell), selected category buttons
- **11px** — segmented control container
- **12px** — primary action buttons in the bottom action bar
- **18px** — top of sheets only
- **999px** — pills, badges, tag chips, the currency pill, FAB-style buttons

Don't pick a 14px or 16px in between. Reach for one of these.

## Density

Density is currently a single mode — comfortable. If you compress later, the rule is:

| | Comfortable | Compact |
|---|---|---|
| Section gap | 1rem | ~0.75rem |
| Card inner padding | 0.625rem 0.75rem | 0.5rem 0.6rem |
| Row gap inside cards | 4–6px | 2–4px |

Compact is an explicit prop on every screen, not a global mode.

## Iconography

Icons come from `lucide-vue-next`. Sizes used in the codebase:

- **22px** — primary-action icon (Add segment, save)
- **20px** — bottom action bar icons (Save, QR)
- **18px** — large body icons
- **15–16px** — header segment icons, sheet close, list-row trailing
- **13–14px** — inline manage-row icons (edit, eye, trash)
- **10–12px** — decorative inline (sparkle, hash, drag handle)

Always set `aria-hidden="true"` and stroke color via `currentColor` so the icon tone follows context. Default tone is `--muted`; accent on hover/active.

## Motion

- **120–180ms** — hover/active transitions on background, color, transform.
- **260–280ms** — sheet slide-in (`cubic-bezier(0.32, 0, 0.67, 0)`), scrim fade.
- **0.38s cubic-bezier(0.34, 1.56, 0.64, 1)** — toast drop-in (overshoot easing). Defined globally in `base.css`.
- **No keyframe animation longer than ~400ms.** Anything longer feels like the app is broken.

`prefers-reduced-motion` is not currently honored — when adding any new motion, gate keyframes with `@media (prefers-reduced-motion: reduce)`.

## Layout

- **Mobile-first, max-width 480px.** Centered with `margin: 0 auto`. Above 600px viewport, content padding doubles (`@media (min-width: 600px)`).
- **Sticky header** at top, sticky bottom action bar (Add view only).
- **Safe areas** — every bottom-fixed element uses `padding-bottom: calc(<base> + env(safe-area-inset-bottom, 0px))`.
- **Hit target ≥ 44px** for any thumb-reachable control.
- **No hover-only affordances.** Hover effects are bonuses; everything must work after a single tap.

## Anti-patterns

| ✗ Don't | ✓ Do |
|---|---|
| Pad screens with placeholder cards or stats | Leave space — if a screen feels empty, the design is probably done |
| Add a label to a self-explanatory field | Drop the label; if meaning isn't obvious, add a glyph (`#`, cal, search) |
| Invent a new accent hue for a "new" semantic | Reuse `--accent`, `--warning`, `--success`, or stay muted |
| Use plain text for state ("Active" / "Inactive") | Use the eye / eye-off + line pattern from `ManageList` |
| Mix label words with icon buttons in one row | Pick one register and stay there |
| Use `--font` for amounts or IDs | Use `--font-num` |
| Render a primary action with `--surface-2` | Primary actions are always `--accent` |

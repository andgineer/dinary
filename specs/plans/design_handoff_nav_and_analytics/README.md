# Handoff: Navigation redesign + Analytics screen

Two related changes to the Dinary expense-tracking PWA, in one package:

1. **Header navigation** — remove the `•••` overflow menu and show **all tabs inline**. The queue notification moves out of the header row into a full-width strip (Draft **C**).
2. **Analytics screen** — a new read-only dashboard view (Sketch **A** — "Savings hero").

They ship together because the nav change is what makes Analytics a first-class, always-visible tab.

---

## About the design files

The files in this bundle are **design references created in HTML/React (JSX)** — prototypes showing intended look and behavior. They are **not production code to copy directly**.

Dinary's real frontend is **Vue 3 (Composition API, `<script setup>`)** over a custom CSS-token design system in `webapp/src/assets/base.css` — **no component library** (the "Vuetify" mention in the original brief is inaccurate; the shipped app is hand-rolled). Recreate these designs as **Vue 3 components in `webapp/src/`**, matching existing conventions. Follow the design-system docs in `specs/ui/` (`design-language.md`, `components.md`, `screens.md`, `patterns.md`).

Two prototypes are included:
- **`Header Drafts.html`** — shows four nav options (A/B/C/D). **Only Draft C is chosen** — ignore A, B, D (`DraftA`/`DraftB`/`DraftD` in `header-drafts-screens.jsx`).
- **`Analytics Sketches.html`** — shows three analytics layouts. **Only Sketch A is chosen** — ignore B, C (`SketchB`/`SketchC`/`EventRowB`/`EventRowC`/`TrendBar` in `analytics-screens.jsx`).

## Fidelity

**High-fidelity.** Colors, type, spacing, and layout are final and map to `base.css` tokens. One new token (`--stat` indigo) must be added — see "Design tokens". Recreate pixel-faithfully using Dinary's existing primitives.

---
---

# PART 1 — Header: remove `•••`, all tabs inline (Draft C)

## Current → target

**Today** (`HeaderSegmented.vue`): two inline tabs (Add, Review) + a `•••` overflow button hiding the rare tabs (Income, LLM) in a dropdown. The queue badge sits inline between the brand and the segmented control.

**Target:** **no overflow menu.** All five tabs are inline and always visible: **Add · Review · Analytics · Income · LLM**. The queue notification relocates to a **full-width strip below the header row** (reusing the existing offline-notice idiom), which frees the row to hold the brand + all five tabs.

> This **reverses** the earlier "put Analytics behind `•••`" plan. There is no `RARE_TABS` array anymore — every tab is a peer.

## Anatomy

```
┌────────────────────────────────────────────┐
│ Dinary        [+][≣][▩][↗][▦]               │  ← header row: brand + 5-tab segmented
├────────────────────────────────────────────┤
│ ⏱ 2 receipts queued        tap to review → │  ← queue strip (amber), only when queued
├────────────────────────────────────────────┤
│  …view content…                            │
```

When **offline**, the existing offline-notice strip stacks **below** the queue strip (both are full-width, same idiom, warning vs. info coloring). Header height grows only when a strip is present.

### Header row
- Container: `background: var(--surface)`, `border-bottom: 1px solid var(--surface-2)`, `position: sticky; top: 0; z-index: 10`.
- Row: `display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 0.9rem 1rem`.
- **Brand:** `"Dinary"`, `1.25rem` / weight 600 / `var(--text)`, `white-space: nowrap`. **The `v0.11` version string is removed from the header chrome** (see note) — it moves to a Settings/About surface.
- **Segmented control** (`SegBar`, `size="sm"` in the prototype): `background: var(--field-deep)`, `border: 1px solid var(--border)`, `border-radius: 11px`, `padding: 3px`, `gap: 1px`. Holds the five tab buttons.

### The five tabs

| key | icon (lucide) | color token | order |
|---|---|---|---|
| `add` | `Plus` | `var(--expense)` #f97316 | 1 |
| `review` | `ListChecks` | `var(--review)` #60a5fa* | 2 |
| `analytics` | `BarChart3` (or `LineChart`) | `var(--stat)` #818cf8 (**new**) | 3 |
| `income` | `TrendingUp` | `var(--income)` #22c55e | 4 |
| `llm` | `Cpu` | `var(--muted)` #94a3b8 | 5 |

\* `--review` (#60a5fa) is currently an inline literal in the codebase; promote it to a token while you're here if it isn't one.

**Each tab button:**
- Size `40 × 36px`, `border-radius: 8px`, icon rendered at `20px`, `border: none`, `padding: 0`.
- **Inactive:** `background: color-mix(in srgb, <tabColor> 14%, transparent)`, `color: <tabColor>`.
- **Active:** `background: <tabColor>` (solid), `color: #fff`, `box-shadow: 0 4px 12px <tabColor>66` (~40% alpha glow).

> **Open item — LLM's active color.** LLM has no per-context hue (it's `--muted`). A muted-fill active state reads weakly. Options: (a) keep it muted/slate fill, (b) give LLM a real accent (e.g. a slate-blue), or (c) make LLM's active state text-only-bold. Recommend (a) for now; confirm with design. Every other tab uses its per-context color as the active fill (matches `design-language.md#per-context-primary-color`).

**Sizing note:** at 390px all five tabs + brand fit comfortably. At ≤340px they still fit (verified) because tabs are icon-only at 40px. Do **not** add labels to the inline tabs — there isn't room and the icons are the established vocabulary.

### Queue notification strip
Replaces the inline queue badge. Mirrors the offline-notice strip already in `App.vue`.

- Render **only when** `queue.items.length + receiptQueue.items.length > 0`.
- Full-width, `padding: 0.5rem 1rem`, `background: rgba(245,158,11,0.12)`, `border-top: 1px solid rgba(245,158,11,0.25)`, `color: var(--warning)`, `font-size: 0.78rem`, `cursor: pointer`.
- Layout: `display: flex; align-items: center; gap: 8px`.
  - Leading `Clock` icon (13px) — or `lucide Receipt`.
  - Text: `"<N> receipts queued"` — the count `N` in `var(--font-num)`, weight 700.
  - Right-aligned hint: `"tap to review →"`, `0.72rem`, `opacity: 0.85`, `margin-left: auto`.
- **Tap target** → opens the existing `QueueModal` (same action the old inline badge had).
- Sits **inside** the sticky header container so it sticks with the row.
- If both the queue strip and the offline strip apply, render **queue first, then offline** (or merge per design — confirm). Both are full-width rows under the header.

### Version-string note
Removing `v0.11` from the header is a deliberate space trade. If the team wants it visible, the cleanest home is a Settings/About row, or a long-press on the brand. Flag this for product sign-off — it's a visible change.

## Vue implementation notes (Part 1)

- **`components/HeaderSegmented.vue`** — biggest change. Remove the `RARE_TABS` array, the `•••` (`MoreHorizontal`) button, the dropdown `<Teleport>`, the `menuOpen` ref, and the outside-click/Esc handlers. Replace with a flat `TABS` array rendered as five inline buttons. Keep `v-model:tab` with values `add|review|analytics|income|llm`. Keep the `doubtfulCount` badge concept on the Review tab if still desired (small warning dot/count — confirm placement now that tabs are 40px; a corner dot is cleaner than a number at this size).
- **Queue strip** — move the queue-notification rendering from its current inline spot in `App.vue` into a strip directly under the header row (still in `App.vue`'s header block, since `App.vue` owns `QueueModal` and the offline strip). The old inline badge markup is deleted.
- **`App.vue`** — add `analytics` to the view router (`tab === 'analytics'` → `<AnalyticsView/>`); ensure the offline + queue strips stack correctly.
- **`base.css`** — add `--stat` / `--stat-deep` (see tokens).
- **Tests** — `HeaderSegmented.spec.js`: remove overflow-menu tests; assert all five tab buttons render, the active tab gets its color fill, and `update:tab` fires per tab. Add a test that the queue strip renders only when the queue is non-empty and opens `QueueModal` on tap.

Prototype refs: `DraftC` in `header-drafts-screens.jsx`; `Brand`, `SegBar`, `Tab`, `TABS`, `QueueBadge` in `header-drafts.jsx`.

---
---

# PART 2 — Analytics screen (Sketch A — "Savings hero")

## Overview
A new mobile-first, **read-only** Analytics view: a year-to-date savings hero, three spend totals, an optional basket-trends rail, and a list of recent spending events. No interactions beyond scrolling. Reached via the new inline **Analytics** tab (Part 1).

## Layout

Standard Dinary view shell — sticky header (Part 1) then a single scroll column. Content `max-width: 480px`, centered, side padding `1.25rem`, top `1rem`, bottom `2rem`. Major sections separated by `22px`.

Order: **SUMMARY eyebrow → savings hero card → 3-stat row → BASKET TRENDS (conditional) → EVENTS list.**

```
┌────────────────────────────────────────┐
│ SUMMARY                    year to date │  ← eyebrow, --stat indigo
│ ┌────────────────────────────────────┐ │
│ │ SAVED THIS YEAR                    │ │  ← hero card (indigo gradient + border)
│ │ +156 000  RSD                      │ │  ← 2rem mono, GREEN value
│ │ 24% savings rate · income − exp.   │ │  ← muted subtitle
│ └────────────────────────────────────┘ │
│ ┌────────┐┌────────┐┌────────┐         │  ← 3 equal stat cards
│ │THIS MON││LAST MON││YTD SPENT│        │
│ │ 84 200 ││102 400 ││489 000  │        │
│ └────────┘└────────┘└────────┘         │
│ BASKET TRENDS                          │  ← only if data
│ [Food ↑14%] [Pocket money ↑8%] [Trav…] │  ← horizontal scroll chips
│ EVENTS                  last 12 months │
│ ┃● Belgrade → Novi Sad      [OPEN]     │  ← open: indigo border+dot+pill
│ ┃  12–18 May 2026        42 800 RSD    │
│   Montenegro holiday                   │  ← closed: flat, muted
│   2–9 Apr 2026           96 200 RSD    │
└────────────────────────────────────────┘
```

## Components

### Stat card — small variant (the 3-up row)
- `background: var(--field)`, `border: 1px solid var(--border)`, `border-radius: 10px`, `padding: 0.75rem 0.8rem`, `display: flex; flex-direction: column; gap: 4px; min-width: 0`.
- **Label:** `0.625rem` / 700 / `letter-spacing: 0.06em` / uppercase / `var(--muted)`.
- **Value row:** `display: flex; align-items: baseline; gap: 5px; flex-wrap: wrap`. Value `var(--font-num)` / 600 / `1.15rem` / `var(--text)` / `line-height: 1.05`. Currency code `var(--font-num)` / `0.7rem` / `var(--muted-2)`.

### Stat card — hero variant (savings)
- `background: linear-gradient(135deg, rgba(99,102,241,0.18), var(--field))`, `border: 1px solid rgba(129,140,248,0.35)`, `border-radius: 14px`, `padding: 1rem 1.1rem`, `gap: 6px`.
- **Label:** `0.7rem`, color `var(--income)` (green — it's a savings figure).
- **Value:** `2rem` mono / 600 / **`var(--income)` green**. Currency code `0.85rem`.
- **Subtitle:** `0.78rem` / `var(--muted)` — `"24% savings rate · income − expenses"`.

Props: `label`, `value` (preformatted string), `currency`, `subtitle?`, `accentColor?`, `hero?`, `delta?` (optional signed %; not used in Sketch A's cards). Prototype: `StatCard` in `analytics-base.jsx`.

### Trend chip
- `display: inline-flex; align-items: center; gap: 5px`, `padding: 0.35rem 0.6rem`, `background: var(--field)`, `border: 1px solid var(--border)`, `border-radius: 999px`, `white-space: nowrap; flex-shrink: 0`, `0.78rem`, `var(--text)`.
- Label, then arrow + percent in `var(--font-num)` / 600 / `0.74rem`.
- **Direction color:** `up` → `var(--up)` red `#f87171` (spending more = caution); `down` → `var(--down)` green `#34d399`.
- Arrows: `lucide ArrowUp`/`ArrowDown`, ~11px, `stroke-width: 3`.
- Rail: `display: flex; gap: 8px; overflow-x: auto; padding: 0 0.25rem 2px`.

Prototype: `TrendChip` (non-inline form) in `analytics-base.jsx`.

### Event row (the chosen open/closed treatment)
- Container: `display: flex; align-items: center; gap: 12px`, `background: var(--field)`, `border: 1px solid var(--border)`, `border-radius: 10px`, `padding: 0.7rem 0.85rem`, `border-left-width: 3px`.
- **Open vs closed = the left border.** Open → `border-left-color: var(--stat)` (indigo). Closed → `border-left-color: transparent`.
- **Left block** (`min-width: 0; flex: 1`):
  - Title row `display: flex; align-items: center; gap: 7px`:
    - **Open only:** live dot — `7×7px`, `border-radius: 999px`, `background: var(--stat)`, `box-shadow: 0 0 0 3px rgba(129,140,248,0.25)`.
    - **Name:** `0.9375rem` / 600 / `var(--text)`, truncates (`white-space: nowrap; overflow: hidden; text-overflow: ellipsis`).
    - **Open only:** `OPEN` pill — `0.58rem` / 700 / `letter-spacing: 0.05em` / `var(--stat)` / `background: rgba(129,140,248,0.15)` / `border-radius: 999px` / `padding: 1px 7px`.
  - **Date range:** `var(--font-num)` / `0.72rem` / `margin-top: 3px`. `var(--muted)` open, `var(--muted-2)` closed.
- **Right — total:** `var(--font-num)` / `0.95rem` / 600. `var(--text)` open, `var(--muted)` closed. Currency code appended `0.7rem` `var(--muted-2)`.

A **closed** event = same row, transparent left border, no dot, no pill, muted text. Prototype: `EventRowA` in `analytics-screens.jsx`.

### Eyebrow
- `display: flex; align-items: center; gap: 8px; padding: 0 0.25rem; margin-bottom: 0.6rem`.
- Label `0.6875rem` / 700 / `letter-spacing: 0.07em` / uppercase. SUMMARY = `var(--stat)`; TRENDS & EVENTS = `var(--muted)`.
- Optional right hint `margin-left: auto; 0.7rem; var(--muted)`. Reuse the existing eyebrow recipe (`ReviewView`/`IncomeView`).

## Behavior & state (Part 2)

- **Read-only.** No tap targets, no sheets, no swipe. Event rows are **not** tappable in v1.
- Suggested Pinia store `useAnalyticsStore`: state `summary`, `trends`, `events`, `loading`, `lastFetched`; action `fetchAll()` on mount; use `useStaleCache` (paint cached, revalidate) like `useReviewStore`/`useIncomeStore`.
- **Trends section renders only if `trends?.length`** — otherwise omit the whole eyebrow + rail (it's a bonus, no empty state).
- **Events empty:** show one muted line / the `IncomeView` empty-state recipe (dashed card, tinted circle, short copy).
- **Offline:** render from cache; no writes to gate.
- **Currency figures are preformatted** into grouped strings (`"156 000"`) by the backend or a formatter util — don't format in the template. All numbers/dates/codes use `var(--font-num)`.

### Data shapes (inferred — confirm against the API)
```ts
interface AnalyticsSummary {
  this_month_total: string;  last_month_total: string;
  ytd_total: string;         ytd_savings: string;     // income − expenses
  savings_rate: string;      currency: string;
}
interface BasketTrend { basket_name: string; direction: 'up'|'down'; pct: string; }
interface SpendingEvent {
  id: string; name: string; date_range: string;       // preformatted
  total: string; currency: string; open: boolean;
}
```

---
---

## Design tokens (combined)

All map to `webapp/src/assets/base.css`. **Add these:**
```css
:root {
  --stat: #818cf8;        /* Analytics per-context primary (indigo): tab + page */
  --stat-deep: #6366f1;   /* hero-gradient stop */
  --up:   #f87171;        /* spending increased — caution (red) */
  --down: #34d399;        /* spending decreased — good (green) */
}
```
Alpha derivatives used (consider `color-mix(in oklab, var(--stat) N%, transparent)`): hero gradient `rgba(99,102,241,0.18)`, hero border `rgba(129,140,248,0.35)`, live-dot halo / inactive-tab tint via `color-mix(... 14%/25%)`, OPEN pill `rgba(129,140,248,0.15)`, queue strip `rgba(245,158,11,0.12)` + border `rgba(245,158,11,0.25)`.

Promote to tokens if not already: `--review` (#60a5fa).

**Existing tokens used (do not redefine):** `--bg` #1a1a2e, `--surface` #16213e, `--surface-2` #0f3460, `--field` rgba(255,255,255,0.04), `--field-deep` rgba(0,0,0,0.18), `--border` rgba(255,255,255,0.08), `--text` #eeeeee, `--muted` #94a3b8, `--muted-2` #64748b, `--income` #22c55e, `--expense` #f97316, `--warning` #f59e0b, `--font`, `--font-num`.

## Assets
- **Icons:** all `lucide-vue-next` (already a dep). Nav: `Plus`, `ListChecks`, `BarChart3`/`LineChart`, `TrendingUp`, `Cpu`. Strip: `Clock` or `Receipt`. Analytics: `ArrowUp`, `ArrowDown`. The prototypes hand-draw equivalents in `I.*` / `AI.*` — replace with real lucide components.
- **No images or illustrations.** Empty states = tinted circle + icon + short copy (`IncomeView` pattern).
- **Fonts:** JetBrains Mono + system-ui, already configured. No new font loading.

## Files in this bundle

| File | What it is |
|---|---|
| `Header Drafts.html` | Runnable nav prototype (A/B/C/D + a trade-offs card). **Build from Draft C only.** |
| `header-drafts.jsx` | Nav primitives: `HT` tokens, `I` icons, `TABS`, `Brand`, `QueueBadge`, `Tab`, `SegBar`. |
| `header-drafts-screens.jsx` | `DraftC` is the chosen header. (`DraftA/B/D` unselected — ignore.) |
| `Analytics Sketches.html` | Runnable analytics prototype (A/B/C + rationale). **Build from Sketch A only.** |
| `analytics-base.jsx` | Analytics primitives: `AT` tokens, demo data, `StatCard`, `TrendChip`, `Eyebrow`. |
| `analytics-screens.jsx` | `SketchA` + `EventRowA` are chosen. (`SketchB/C` etc. — ignore.) |
| `header-drafts-main.jsx`, `analytics-main.jsx`, `design-canvas.jsx`, `ios-frame.jsx` | Prototype scaffolding only — **do not port.** |

### Target repo reference files (not in this bundle)
- `webapp/src/components/HeaderSegmented.vue` — rewrite for inline 5-tab (Part 1).
- `webapp/src/App.vue` — queue strip + offline strip stacking; `analytics` route.
- `webapp/src/views/IncomeView.vue` — closest model for `AnalyticsView` (sections, eyebrows, empty state, `useStaleCache`).
- `webapp/src/components/StatusDot.vue` — reference for the halo-dot technique (open-event live dot).
- `webapp/src/assets/base.css` — add tokens.
- `specs/ui/{design-language,components,screens,patterns}.md` — follow these; update `screens.md` (header nav) + `components.md` (new `AnalyticsView`) when done.

## Acceptance checklist

**Part 1 — header**
- [ ] `•••` overflow, `RARE_TABS`, dropdown, and inline queue badge all removed from `HeaderSegmented.vue` / `App.vue`.
- [ ] Five tabs render inline (Add·Review·Analytics·Income·LLM), icon-only @40px, active = per-context color fill + glow.
- [ ] Fits at 340px with no clipping; no labels added to inline tabs.
- [ ] Queue strip renders below the header **only when queue non-empty**, amber, tappable → `QueueModal`.
- [ ] Offline strip still works and stacks with the queue strip.
- [ ] `v0.11` removed from header (relocated per product decision).
- [ ] LLM active-color decision confirmed with design.

**Part 2 — analytics**
- [ ] `--stat`/`--stat-deep`/`--up`/`--down` added to `base.css`.
- [ ] `AnalyticsView` mounts on the inline `analytics` tab.
- [ ] SUMMARY eyebrow indigo; hero gradient + indigo border; savings value green `2rem` mono; rate as subtitle.
- [ ] 3 equal stat cards (`1fr 1fr 1fr`); mono values; codes muted-2.
- [ ] Trends rail renders only when data present; up=red / down=green; horizontal scroll.
- [ ] Open events: indigo left-border + haloed dot + OPEN pill, white text. Closed: transparent border, no dot/pill, muted.
- [ ] Every number/date/code uses `--font-num`. Read-only — no tap/swipe/sheet.
- [ ] Renders from cache offline; trends/events degrade gracefully when empty.

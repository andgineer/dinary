# Screen Anatomy

The five top-level views, their layout, and how the header segmented control binds them together.

## Navigation

A single **header segmented control** in `App.vue` switches between the five views. There is no bottom tab bar and no overflow menu.

```
┌────────────────────────────────────────────┐
│ Dinary        [+][≣][▩][↗][▦]              │  sticky header row
├────────────────────────────────────────────┤
│ ⏱ 2 receipts queued        tap to review → │  queue strip (amber), only when queued
├────────────────────────────────────────────┤
│ Offline — expenses will be queued          │  offline strip, only when offline
├────────────────────────────────────────────┤
│              active view body              │
└────────────────────────────────────────────┘
```

### Tab inventory (`HeaderSegmented.vue`)

| Tab | key | Glyph (lucide) | Color token | Size |
|---|---|---|---|---|
| **Add** | `add` | `Plus` | `--expense` #f97316 | 40×36 |
| **Review** | `review` | `ListChecks` | `--review` #60a5fa | 40×36 |
| **Analytics** | `analytics` | `BarChart3` | `--stat` #818cf8 | 40×36 |
| **Income** | `income` | `TrendingUp` | `--income` #22c55e | 40×36 |
| **LLM providers** | `llm` | `Cpu` | `--muted` #94a3b8 | 40×36 |

Each tab button: inactive = `color-mix(in srgb, <tabColor> 14%, transparent)` bg + tab color text. Active = solid tab color fill + `#fff` icon + `0 4px 12px <tabColor>66` glow.

**Rule for the future:** all tabs are peers — no overflow menu. Every new top-level view gets an inline tab.

### Header chrome

- **Brand** (`Dinary`) on the left. Version string removed from header.
- **Queue strip** — full-width amber strip below the header row, renders only when `queue.items.length + receiptQueue.items.length > 0`. Shows count + "tap to review →". Tap → `QueueModal`. Stacks above the offline strip when both present.
- **Offline notice strip** — warning-color strip below the queue strip when `!isOnline`. Copy adapts by view: `Offline — expenses will be queued` on Add, `Offline — incomes can't be added or edited` on Income, generic `Offline — changes not available` elsewhere.

## Add view

The entry form. The most-used view — `tab` defaults to `'add'`.

```
┌──────────────────────────────────────┐
│  [RSD]   0          📅 17.05         │ hero row
│  ────────             ───            │
│                                      │
│ ┌──────────────────────────────────┐ │ category-card shell (12-px radius)
│ │ [Еда][Мясо][Перекус][Сладости] … │ │ CategoryQuickPicks pills
│ │ ──────────────────────────────── │ │ internal divider
│ │ Мясо                          ›  │ │ category-pick-btn → CategorySheet
│ └──────────────────────────────────┘ │
│                                      │
│  EVENT             [+] [⚙]           │
│  ┌──────────────────────────────┐    │
│  │ [trip-may] [poker-night] …   │    │ event-chips flow (selected = orange)
│  └──────────────────────────────┘    │
│                                      │
│  TAGS              [+] [⚙]           │
│  ┌──────────────────────────────┐    │
│  │ [собака][Аня][Лариса] …      │    │ TagPicker (selected = --accent)
│  └──────────────────────────────┘    │
│                                      │
│  [ Comment                       ]   │ single-line input
├──────────────────────────────────────┤
│  [📷]  [💾 Save                 ]    │ sticky action bar, both orange
└──────────────────────────────────────┘
```

Owned by `views/AddView.vue` + `components/ExpenseForm.vue`.

### Hero row

- **Currency pill** — left, rectangular, `--expense` fill, white text, mono. Tap opens `CurrencyPicker` in a popover (orange accent).
- **Amount input** — center, right-aligned, 2-rem mono weight 500, transparent with a bottom-line underline that turns `--accent` on focus.
- **Date** — right, compact `<input type="date">` (12.5 px, muted), bottom-line treatment with a leading `Calendar` glyph.

Three fields compressed into one line because each is self-explanatory by content and position.

### Category card (replaces the v0.7 group→category dropdowns)

`category-card` is a single 12-px-radius shell with `--field` background and `1.5px solid --border`. Two horizontal rows separated by a 1-px divider:

1. **`CategoryQuickPicks`** — wrap-flow of frequently-used pills. Tap selects without opening any sheet. Selected pill fills `--expense`.
2. **`category-pick-btn`** — 46-px-min-height row, the current category name (or `"Select category…"` placeholder, muted) + right-aligned `ChevronRight` muted. Click / Enter / Space opens `CategorySheet`.

The group is still tracked internally (for the group→category hierarchy logic) and pre-filled when a category is chosen, but no separate group selector is shown to the user.

### Event chips

- Header row: `Event` label (muted uppercase) + `IconBtn` plus (accent) + `IconBtn` cog/x (muted).
- Body: `event-chips` container (8-px-radius `--field` panel) with pill-shaped chips on `--surface` background. Selected chip fills `--expense`. Empty state: italic "no active events" text.
- Chips show active events from the last 365 days, newest to oldest. The same scope applies to the active section of the manage panel and to the event selector in `ExpenseEditSheet`.
- Plus opens an `InlineCreateEvent`. Cog opens a `ManageList` of active + inactive events. Both can be open simultaneously.

### Tags

- Same header pattern: `Tags` label + plus + cog.
- Body: `TagPicker` chips. Selected chip fills `--accent`.

### Comment

A single-line `<input type="text">` (not a textarea) on `--field` background. Focus outlines with 2-px `--accent`.

### Save flow

Two ways to save:

1. **Bottom action bar Save** — always visible at the bottom, orange.
2. **`KeyboardSaveBar`** — appears just above the soft keyboard while it's open, also orange (`accentColor="var(--expense)"`).

After save: the form resets but keeps the default group/category and currency. A toast confirms the saved amount.

## Analytics view

Read-only financial summary. Reached via the inline `analytics` tab.
See `specs/reference/pwa-analytics.md` for content, API contract, and cache policy.

## Income view

The income-tracking view. Accessed via the inline `income` tab.

```
┌──────────────────────────────────────┐
│ ┌──────────────────────────────────┐ │ IncomeForm card
│ │ [EUR]   0           ───────────  │ │ hero row (green currency pill)
│ │                                  │ │
│ │ For month             Received   │ │
│ │ ┌─────────┐         ┌─────────┐  │ │
│ │ │ 2026-05 │         │ 17.05.26│  │ │
│ │ └─────────┘         └─────────┘  │ │
│ │ ┌────────────────────────────┐   │ │
│ │ │ Comment (optional)         │   │ │
│ │ └────────────────────────────┘   │ │
│ └──────────────────────────────────┘ │
│                                      │
│  INCOMES   [3]  4m ago           ⟳  │ green eyebrow + count + cache age + refresh
│                                      │
│  2026                  +4 100.00 EUR │ year header (year mono, total mono green)
│  ┃ May 2026         +1 200.00 EUR    │ IncomeRow (green left border)
│  ┃ 17 May · paycheck                 │
│  ┃ April 2026       +1 200.00 EUR    │
│  ┃ 03 Apr · RSD 145 000              │
│  …                                   │
│                                      │
│  2025                 +14 000.00 EUR │
│  …                                   │
├──────────────────────────────────────┤
│  [ Save                          ]   │ sticky bottom bar, green 14-px radius
└──────────────────────────────────────┘
```

Owned by `views/IncomeView.vue`.

### Section header

- `INCOMES` label in `--success` (the only screen where green is used for an eyebrow).
- Count badge next to the label (count of all incomes).
- "Just now / Nm ago / Nh ago / Nd ago" muted-italic cache age.
- Right-aligned refresh button (muted RefreshCw, disabled while loading or offline).

### Year grouping

Incomes are grouped by `year` and rendered with a small header row showing the year (mono uppercase muted) and the year's total (mono green, prefixed with `+`). Currency is taken from the first item in the group — multi-currency years currently show the first currency only (acceptable for v0.10).

### `IncomeRow`

4-px green left border. Top row: month label ("May 2026") + trailing `+amount currency` (green num, muted code). Bottom row: received-date + comment or original-amount fallback. Whole row tappable → opens `IncomeEditSheet`. Left-swipe reveals an `Edit` panel (green; muted `--surface-2` when offline).

### Empty state

When `items.length === 0` and not loading: dashed card with a 44-px green-tinted circle (`TrendingUp` icon), "No incomes yet" title + "Add your first income above" subtitle. No illustration.

### Save flow

Two ways to save:

1. **Bottom action bar Save** — always visible, full-width, `--success` fill, 14-px radius, 0 4 14 green glow. Disables to `--surface-2` when offline.
2. **`KeyboardSaveBar`** — appears above the soft keyboard, also green (`accentColor="var(--success)"`).

## Review view

Two ordered sections in a single scroll container: **NEEDS REVIEW** (one row per doubtful classification rule, by impact) and **EXPENSES** (individual receipt-line expenses, newest first).

```
┌──────────────────────────────────────┐
│  NEEDS REVIEW  [5]   by impact    ⟳  │ only shown when doubtfulCount > 0
│                                      │
│  ⚠ ┃ Karamel čoko prot.čok.          │ doubtful — c2 (warning) left-border
│    ┃                Lidl Beograd     │
│    ┃ [✨✓ Сладости][Еда][Перекус] ✎  │ approve + alts + edit
│                                      │
│  ⚠ ┃ Energy drink unknown            │ c1 — error left-border (lowest)
│    ┃                7-Eleven         │
│    ┃ [✨✓ Напитки][Еда][Снеки]    ✎  │
│                                      │
│         [ Confirm all (5) ]          │ shown at end of doubtful list
│                                      │
│  EXPENSES                            │ second section header
│  ┌────────────────────────────────┐  │
│  │ Karamel čoko prot.čok.   220   │  │ ExpenseRow (item-name primary)
│  │ Lidl Beograd · 17 May    RSD   │  │
│  │ Еда › Сладости                 │  │
│  └────────────────────────────────┘  │
│  …more expenses…                     │
│  [skeleton]                          │ infinite-scroll loading state
└──────────────────────────────────────┘
```

Owned by `views/ReviewView.vue`. Rows by `components/RuleRow.vue` and `components/ExpenseRow.vue`.

### Section headers

- **NEEDS REVIEW** — only mounts when `doubtfulCount > 0`. `--warning` label + amber count badge + "by impact" muted hint on the right + refresh `IconBtn`.
- **EXPENSES** — always mounted below. Plain muted eyebrow, no badge, no refresh of its own.

### `RuleRow` at a glance

- **Confidence tier drives the left-border color** (4-px solid):
  - `c1` → `--error` (red) — lowest confidence
  - `c2` → `--warning` (amber)
  - `c3` → muted amber `rgba(245, 158, 11, 0.75)`
  - Any out-of-range value is treated as `c2`.
  Doubtful rows also paint a low-alpha amber wash over the slider.
- **Top row** — name (700 on doubtful, 600 on certain), store right-aligned muted. If `name` is empty, name slot falls back to `store` and the trailing slot is dropped.
- **Bottom row (doubtful)** — wrap-flow:
  1. Tag chips (if any)
  2. **Approve chip** for the suggested category (green-tinted; `Sparkles` glyph when LLM suggestion differs from current; `Check` + name). Tap = fast-path approve.
  3. Up to **2 alternative chips** from `alternative_categories`.
  4. **Frequent-category quick picks** filtered to exclude any IDs already in suggestion/alts.
  5. Trailing **Edit pencil** — opens `ExpenseEditSheet` in rule-correction mode.
- **Bottom row (certain)** — `group › category` breadcrumb left, muted-2 chevron right.

### Approve flow (fast path)

Tapping any approve / alt / freq chip emits `approve({ item, categoryId })`. The store calls `PATCH /api/rules/{rule_id}/category` which sets the rule to `confidence_level=4, source='user_correction'` and propagates the category to every linked expense in one transaction. On success the row leaves NEEDS REVIEW.

### Confirm all

When the doubtful list has fully paginated (`!hasMore`) and at least one doubtful row remains, a green outlined pill **Confirm all (N)** appears below the list. Tap → one batch call, then refresh EXPENSES to reflect the new classifications.

### `ExpenseEditSheet` flow

Tapping any row, the Edit pencil, the Edit panel button (or releasing a long swipe on a certain row) opens the sheet:

- **Manual expense** (`receipt_id == null`): AMOUNT block visible at top. Footer Delete is ghost-danger (outline only).
- **Receipt-backed expense**: no AMOUNT block; instead a small `FROM RECEIPT` pill next to the EDIT EXPENSE eyebrow. SCOPE radios appear at the bottom of the body (`Only this` / `Last month` / `This year` / `All history`, default `single`). "Also update rule" checkbox below SCOPE if the source has `has_rule`. Footer Delete reads "Delete receipt" with a danger-tint background fill.

Save is sky-blue `#60a5fa`, disabled until a category is selected.

### Delete flows

- Manual: tapping Delete pops a `ConfirmDeleteSheet` (`kind="expense"`), one-line context (`<amount currency>` mono on `<category>, <date>`), Cancel + Delete.
- Receipt: tapping Delete receipt pops `ConfirmDeleteSheet` (`kind="receipt"`) with a `ReceiptCascadeCard` in the `detail` slot — lists every item from the receipt with mono amounts and a TOTAL footer. Destructive button reads `"Delete N items"` with the live count. After delete, the store does a full feed reset + reload so the rule rows tied to the receipt disappear immediately.

### Job status banner (stuck receipts)

When the receipt behind an expense still has an active classification job, the
`ReceiptCascadeCard` detail view shows a banner above the item list:

- **poisoned** (error tone): the full error message, retry count, and time of
  the last attempt. No further automatic retries will happen.
- **pending** (warning tone): retry count and the next scheduled retry time.
- **in_progress** (neutral, spinner): retry count. Once the job has been
  running for more than 5 minutes, an "appears stuck" warning replaces the
  spinner-only state.

Every state except a healthy `in_progress` (under 5 minutes) shows a "Create
expense manually" button that opens the category picker and resolves the
receipt manually (see
[classification-pipeline.md](../reference/classification-pipeline.md#manual-resolution)).
If the automatic pipeline finishes first, the resolve call returns 409 and the
UI shows a toast instead of an error.

### Stuck receipts

When any receipt has an active classification job (`pending`, `in_progress`,
or `poisoned`), a STUCK RECEIPTS section lists every such receipt, oldest
first — shown in full, with no threshold. Each row shows the merchant (or
"Unknown store"), the amount/currency decoded from the receipt's QR payload
(or "amount unknown" if the URL can't be decoded, with the action disabled),
status, retry count, and age. A "Save as expense" button opens the category
picker and resolves the receipt manually, same as the job status banner above.

### Pagination

Two independent `IntersectionObserver` sentinels — one for the rule feed, one for the expense feed — each `rootMargin: "120 px"`. Skeleton rows show during fetch.

### Offline

Reads still render from cache. Writes are blocked with an info toast. Refresh is disabled.

## LLM view

Provider pool management. Backend API: `/api/admin/llm-providers` + `/api/admin/llm-status`.

```
┌──────────────────────────────────────┐
│  ●  3 / 4 healthy                [+] │ HealthSummaryCard
│  round-robin failover · last switch  │
│                                      │
│  RECEIPT QUEUE                       │ optional, only if classification job present
│  [12 ready][3 processing][1 sleeping]│
│  [2 failed]                          │
│                                      │
│  PROVIDER POOL          priority  ⟳  │
│  ┌────────────────────────────────┐  │
│  │ [1] ● Groq                     │  │ ProviderCard
│  │     llama-3.3-70b-versatile    │  │
│  │ ───────────────  412 / 14 000  │  │ usage bar + numbers
│  │              today        940ms│  │ latency chip
│  │ ────────────── divider ──────  │  │
│  │              [↑] [↓] [⏻]        │  │ action row
│  └────────────────────────────────┘  │
│  ┌────────────────────────────────┐  │
│  │ [3] ● OpenRouter   [86s]       │  │ rate-limited countdown pill
│  │     nvidia/nemotron-3-…        │  │
│  │ 12 calls today · no daily cap  │  │ no bar — uncapped
│  │              [↑] [↓] [⏻]        │  │
│  └────────────────────────────────┘  │
└──────────────────────────────────────┘
```

Owned by `views/LLMView.vue`. Refresh polled every 30 s when online.

### Receipt queue strip (new)

Above the provider pool, when any of `pending`, `in_progress`, `sleeping`, `poisoned` is > 0, a `RECEIPT QUEUE` label sits above a row of chips:

| Chip | Color |
|---|---|
| `N ready` | `--accent` text on transparent, accent border |
| `N processing` | `--text` |
| `N sleeping` | `--muted` |
| `N failed` | `--error` |

Each chip is a thin outlined pill. The strip is informational — no actions.

### `ProviderCard` rules

- **Status dot kinds** — see `patterns.md`.
- **Usage row** — bar + numbers when a daily limit is set; "N calls today · no daily cap" otherwise. Bar fills `--accent` until > 80 %, then `--warning`.
- **Latency chip** — inline with the right-side label. `--warning` if > 3000 ms.
- **Action row** — bottom-aligned, separated from the card body by a 1-px `--border` line. Move-up / Move-down disabled at list extremes. Power dims when disabled. No standalone "test" button in v0.10.
- **Card body tappable** — opens `ProviderSheet` in edit mode. Actions in the bottom row use `@click.stop` so they don't bubble.

### CRUD flow

`HealthSummaryCard`'s `+` opens `ProviderSheet` in add mode. Tapping a card body opens edit mode. See `patterns.md#provider-sheet-form`.

## When to add a new view

All five tabs are inline peers — icon-only at 40 px, fits at 340 px. A sixth tab would need design review. If the new view is:

- **A new primary workflow** — add an inline tab with its own `--<context>` color token.
- **An admin / settings panel** — push it into the LLM view's pattern (a dedicated screen reachable from elsewhere) or into a sheet, not a top-level slot.

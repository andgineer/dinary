# Screen Anatomy

The three top-level screens, their layout, and how they hand off to each other.

## Navigation

A single **header-segmented control** in `App.vue` switches between the three views. There is no bottom tab bar.

```
┌──────────────────────────────────────────────────────┐
│ Dinary v0.7  [⚠ queued]      [+] [☰ ●5] [▦]         │ sticky header
├──────────────────────────────────────────────────────┤
│                                                      │
│              active view body                        │
│                                                      │
└──────────────────────────────────────────────────────┘
```

- **Add (Plus, primary)** — default landing tab. Larger and accent-tinted even when inactive so it always reads as the primary action.
- **Review (ListChecks)** — warning badge in top-right when `doubtfulCount > 0`.
- **LLM (Cpu)** — no badge.

Left side of header carries the brand + version, the offline pill, and the queue badge (tappable → `QueueModal`).

Implementation: `App.vue` + `components/HeaderSegmented.vue`.

## Add view

The entry form. The most-used screen — defaults to opening here.

```
┌──────────────────────────────────────┐
│  [RSD]      0           📅 17.05    │ hero row
│  ────────────              ───       │
│                                      │
│  [ Еда                       ▾  +⚙]  │ group → category
│   │                                  │   (connector line)
│   └─►  [ еда                ▾  +⚙]  │
│                                      │
│  [ — no event —             ▾  +⚙]  │ event
│                                      │
│  #                            + ⚙    │ tags header row
│  [ собака  Аня  Лариса  ... ]        │ full-width tag field
│                                      │
│  ┌────────────────────────────────┐  │
│  │ Note                           │  │ comment textarea
│  └────────────────────────────────┘  │
├──────────────────────────────────────┤
│  [📷 QR]  [💾 Save              ]    │ sticky action bar
└──────────────────────────────────────┘
```

Owned by `views/AddView.vue` + `components/ExpenseForm.vue`.

### Hero row

- **Currency pill** — left, rectangular, accent fill, white text, mono `--font-num`. Tap opens `CurrencyPicker` in a popover.
- **Amount input** — center, right-aligned, large (32px, `--font-num`, weight 500), no border except a bottom-line underline that turns accent on focus.
- **Date** — right, compact (12.5px, muted), same bottom-line treatment with a leading cal glyph.

This compresses three fields into one line because each is self-explanatory by content and position.

### Group → Category hierarchy

Visualized as a vertical line + L-elbow connector under the Group select, with Category indented `padding-left` past the line. The indent IS the label. Changing the group filters the category list.

### Event / Tags / Comment

Standard sections, each with `+` and ⚙ (cog) icons on the right side of the section header. `+` opens an `InlineCreateRow` (or `InlineCreateEvent` for events). ⚙ opens the manage list (eye / eye-off rows).

### Save flow

Two ways to save:

1. **Bottom action bar Save** — always visible at the bottom.
2. **KeyboardSaveBar** — appears just above the on-screen keyboard while it's open, so the user doesn't have to manually dismiss the keyboard or mistake its close button for Save.

After save, the form resets but keeps the user's default group/category and currency selection.

## Review view

Two ordered sections in a single scroll container: **NEEDS REVIEW** (classification rules awaiting confirmation, one per unique item-name) and **EXPENSES** (individual receipt lines, newest first).

The feed `GET /api/receipts/review/feed` returns rule items with `is_doubtful: bool`. Doubtful items appear in NEEDS REVIEW. Certain items are filtered out of NEEDS REVIEW; individual expenses are loaded separately from `GET /api/expenses/feed` (paginated).

```
┌──────────────────────────────────────┐
│  NEEDS REVIEW  [5]   by impact    ⟳ │ (only shown when doubtfulCount > 0)
│                                      │
│  ⚠ ┃ Karamel čoko prot.čok.          │ doubtful row — c2 (warning) left-border
│    ┃                Lidl Beograd     │
│    ┃ [✨✓ Сладости] [Еда] [Перекус] ✎ │ approve chip + alts + edit
│                                      │
│  ⚠ ┃ Mesnata slanina                 │ another doubtful — c2
│    ┃              Maxi Vračar        │
│    ┃ [✨✓ Мясо] [Еда] [Колбасы]    ✎  │
│                                      │
│  ⚠ ┃ Energy drink unknown            │ c1 — error left-border (lowest conf)
│    ┃              7-Eleven           │
│    ┃ [✨✓ Напитки] [Еда] [Снеки]   ✎  │
│                                      │
│         [ Confirm all (5) ]          │ shown at end of doubtful list
│                                      │
│  EXPENSES                            │ second section header
│  ┌────────────────────────────────┐  │
│  │ Karamel čoko prot.čok.   220   │  │ ExpenseRow (item-name primary)
│  │ Lidl Beograd · 17 May    RSD   │  │
│  │ Еда › Сладости                 │  │
│  └────────────────────────────────┘  │
│  ┌────────────────────────────────┐  │
│  │ Maxi Vračar              4 870 │  │ ExpenseRow (store fallback)
│  │ 08.05                    RSD   │  │
│  │ Еда › еда                      │  │
│  └────────────────────────────────┘  │
│  …more expenses…                     │
│  [skeleton]                          │ infinite-scroll loading state
└──────────────────────────────────────┘
```

### Section headers

- **NEEDS REVIEW** — only mounts when `doubtfulCount > 0`. Warning-colored label + amber count badge + "by impact" sort hint on the right, alongside the refresh `IconBtn`.
- **EXPENSES** — always mounted below. Plain uppercase eyebrow.

### RuleRow at a glance

- **Confidence tier drives the left-border color** (4px solid):
  - `c1` → `--error` (red) — lowest confidence
  - `c2` → `--warning` (amber)
  - `c3` → muted amber (`rgba(245, 158, 11, 0.75)`)
  - Any out-of-range value is treated as `c2`.
  Doubtful rows also paint a low-alpha amber wash over the slider background.
- **Top row** — name (bold; 700 on doubtful, 600 on certain), store right-aligned and muted. If `name` is empty, name slot falls back to `store` and the trailing slot is dropped.
- **Bottom row (doubtful)** — wrap-flow of, in order:
  - Tag chips (if any tags attached to the rule)
  - **Approve chip** for the suggested category (green-tinted, `Sparkles` icon prefix when the suggestion differs from current, then `Check` + category name) — tap = fast-path approve with that category, no sheet
  - Up to **2 alternative chips** from `alternative_categories` — tap = fast-path approve with that alt
  - **Frequent-category quick picks** for any categories the user uses often that aren't already in suggestion/alt chips — tap = fast-path approve
  - Trailing **Edit pencil icon** — opens `ExpenseEditSheet` in rule-correction mode
- **Bottom row (certain)** — `group › category` breadcrumb on the left, muted chevron on the right.

### Approve flow (fast path)

Tapping any approve/alt/freq chip emits `approve({ item, categoryId })`. The store calls `correctCategory(expenseId, categoryId, "all")` — same endpoint as the sheet, scope forced to `all` because correcting a doubtful row is fundamentally rule creation. On success the row is removed from NEEDS REVIEW and re-inserted as a certain row above the existing certain section (so the user can see the result without losing position).

### Confirm all

When the doubtful list has fully paginated (`!hasMore`) and at least one doubtful row remains, a green outlined **Confirm all (N)** button appears below the list. Tap confirms every visible doubtful rule in one batch (`confirmAllRules(ruleIds)`), then refreshes the EXPENSES section to reflect the new classifications.

### Swipe-to-act

Both `RuleRow` and `ExpenseRow` support left swipe via `useSwipeRow`. See `patterns.md#swipe-to-act`. The reveal panel is **Edit + Approve** on doubtful rows (`168px` total) and **Edit** only on certain rows and expense rows (`92px` / `84px`).

### Correction sheet

Tapping a row, the trailing Edit pencil, the Edit panel button, or releasing a long swipe on a certain row opens `ExpenseEditSheet`. When opened from a doubtful row, the sheet runs in rule-correction mode (scope hidden, treated as `all`). When opened from an `ExpenseRow`, the sheet may show the scope selector and "Update rule" checkbox depending on the source expense's `receipt_id` and `has_rule`.

### Pagination

Two independent `IntersectionObserver` sentinels — one for the rule feed, one for the expense feed — each `rootMargin: "120px"`. Skeleton rows show during fetch. See `patterns.md#infinite-scroll`.

### Offline

The whole view degrades gracefully: cached rules and expenses still render, refresh + writes are disabled with an info toast. See `patterns.md#offline-aware-actions`.

Owned by `views/ReviewView.vue`. Rows by `components/RuleRow.vue` and `components/ExpenseRow.vue`.

## LLM view

Provider pool management. Backend API: `/api/admin/llm-providers` + `/api/admin/llm-status`.

```
┌──────────────────────────────────────┐
│  ●  3 / 4 healthy                [+] │ HealthSummaryCard
│  round-robin failover · last switch  │
│                                      │
│  PROVIDER POOL          priority  ⟳  │
│  ┌────────────────────────────────┐  │
│  │ [1] ● Groq                     │  │ ProviderCard
│  │     llama-3.3-70b-versatile    │  │
│  │ ──────────────────  412 / 14k  │  │ usage bar + latency
│  │                today      940ms│  │
│  │  ───────────────────────────── │  │
│  │              [↑][↓][⚡][⏻]      │  │ action row
│  └────────────────────────────────┘  │
│  ┌────────────────────────────────┐  │
│  │ [3] ● OpenRouter   [86s]       │  │ rate-limited countdown
│  │     nvidia/nemotron-3-…        │  │
│  │ 12 calls today · no daily cap  │  │ (no bar — uncapped)
│  │              [↑][↓][⚡][⏻]      │  │
│  └────────────────────────────────┘  │
└──────────────────────────────────────┘
```

Owned by `views/LLMView.vue`. Refresh polled every 30s when online.

### ProviderCard rules

- **Status dot kinds** — see `patterns.md#status-dot`.
- **Usage row** — bar + numbers when a daily limit is set; "N calls today · no daily cap" otherwise. Bar fills with `--accent` until > 80%, then `--warning`.
- **Latency chip** — inline with the right-side label. Yellow if > 3000ms.
- **Action row** — bottom-aligned, separated from the card body by a 1px `--border` line. Move-up / Move-down disabled at list extremes. Test = `Zap`. Power dims when disabled.
- **Card body tappable** — opens `ProviderSheet` in edit mode. Actions in the bottom row use `@click.stop` so they don't bubble to the edit handler.

### CRUD flow

`HealthSummaryCard`'s `+` button → `ProviderSheet` in add mode. Tap any card body → edit mode. See `patterns.md#provider-sheet` for the form contract.

## When to add a new screen

Adding a fourth tab to the segmented control is a major change — three icons already pushes the upper bound of what fits naturally on a phone. If the new screen is:

- **Heavy, persistent, primary** — extend the segmented control to 4 icons and shrink Add slightly. Adjusts to ~48px wide each.
- **Secondary or rare** — make it a sheet/modal launched from one of the existing screens.
- **An admin / settings panel** — push it into Settings, not the main nav.

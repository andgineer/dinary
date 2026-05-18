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

Single unified list. The shipped feed (`GET /api/receipts/review/feed`) returns one stream with `is_doubtful: bool` per item. The UI renders both kinds in one column with `RuleRow`, sorted by impact for doubtful and date for certain.

```
┌──────────────────────────────────────┐
│  NEEDS REVIEW  [5]   by impact    ⟳ │
│                                      │
│  ⚠ ┃ Karamel čoko prot.čok.   1340  │ doubtful (warning left-border)
│    ┃ Lidl Beograd · ×6        RSD   │
│    ┃ [maybe] Еда › еда → ✨ ...      │
│                                      │
│  ⚠ ┃ Mesnata slanina           920   │
│    ┃ Maxi Vračar · ×2         RSD   │
│    ┃ [maybe] Еда › еда → ✨ ...      │
│                                      │
│  ┌────────────────────────────────┐  │
│  │ Lidl Beograd            4 870  │  │ certain (plain card)
│  │ 08.05 · 19 items          RSD  │  │
│  │ Еда › еда                      │  │
│  └────────────────────────────────┘  │
│  …more certain rows…                 │
│  [skeleton]                          │ infinite-scroll loading state
│  ─── end · N loaded ───              │
└──────────────────────────────────────┘
```

Section header swaps copy based on whether doubtful items exist:
- `doubtfulCount > 0` → `NEEDS REVIEW  [N]  by impact`
- otherwise → `RULES`

The refresh button on the right is muted, icon-only — secondary action. List uses an `IntersectionObserver` sentinel + skeleton rows for pagination.

Owned by `views/ReviewView.vue`, rows by `components/RuleRow.vue`.

### Correction flow

Tapping any row opens `CorrectionSheet` — see `patterns.md` for the bottom-sheet pattern and `patterns.md#scope-selector` for the per-row scope choice.

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

# Future Screens Guide

Checklist for adding a new screen so it feels like part of Dinary. Read this before designing; pull patterns from `patterns.md` and components from `components.md` rather than inventing new ones.

## Checklist

- [ ] **Have I read `design-language.md`?** No new colors, no new font sizes outside the scale.
- [ ] **Is this actually a new screen, or a sheet from an existing one?** Default to sheet — see "Where does this belong" below.
- [ ] **Does an existing pattern in `patterns.md` cover the interaction?** If yes, use it verbatim.
- [ ] **Are there labels I can drop?** Every label that survives needs justification.
- [ ] **Are there text affordances I can swap for icons?** `+`, ⚙, ⏻, 👁, ⚡, 🗑 are the lingua franca.
- [ ] **Does any state pair (active/inactive, on/off) show as words?** Replace with the state-divider pattern.
- [ ] **Is the primary action one tap from the thumb?** If not, redesign before building.
- [ ] **Does it work offline / show a sensible state when offline?** Use `useOnline` + the `requireOnline()` pattern.
- [ ] **Have I added safe-area padding to anything bottom-fixed?** `env(safe-area-inset-bottom, 0px)`.
- [ ] **Is there a refresh story for any list?** Manual refresh + (optional) polling. Both call the same action.
- [ ] **Does the screen pull `--accent` for anything other than the primary action, selected state, focus ring, or scan/destructive?** If yes, fix it.
- [ ] **Am I queuing toasts?** Don't — replace.
- [ ] **Am I introducing a modal where a bottom sheet would work?** Bottom sheet, always, for any new flow.

## Rules of the road

### 1. Labels are the last resort
A field is recoverable from its content if any of these is true:
- Its current value tells you what it is (a date, an amount, a tag list).
- A glyph in the field tells you (cal, `#`, search).
- It's the only thing it could be on this screen.

If two of those are true, **drop the label**.

### 2. Hierarchy uses geometry, not words
For any parent → child relationship, use the connector pattern (`patterns.md#connector-hierarchy`). Indent + line = label.

### 3. State is shown, not spelled
Active/inactive, on/off, published/draft, enabled/disabled — use the state-divider pattern (`patterns.md#state-dividers`). Words are forbidden for binary state pairs.

### 4. Icons replace verbs

| Verb | Icon |
|---|---|
| New / Add | `Plus` |
| Manage / Configure | `Settings` / cog |
| Close / Cancel | `X` |
| Edit / Rename | `Pencil` |
| Hide / Disable | `EyeOff` |
| Show / Enable | `Eye` |
| Save / Submit | `Save` (floppy) |
| Search / Filter | `Search` |
| Schedule / Date | `Calendar` |
| Power / Toggle | `Power` |
| Test / Run | `Zap` |
| Refresh / Reload | `RotateCw` |
| Delete | `Trash2` |
| Suggested (AI) | `Sparkles` |

If you need a verb the table doesn't cover, add the icon here in the same PR.

### 5. Numbers use `--font-num`
Amounts, dates, ranges, ISO codes, IDs, latencies, version strings. Body text never.

### 6. Picker vs Manage on every catalog
Any catalog-backed select (groups, categories, events, tags, providers, future: accounts, payees, …) supports the picker-vs-manage pattern from `patterns.md`. The same `+` and ⚙ buttons sit in the section header.

### 7. Bottom action bar for editor screens
Editor screens (entry, settings forms) get a sticky bottom action bar. Square secondary action on the left (or omitted), flex-1 primary action on the right with `--accent`. List/dashboard screens don't get one — they use a floating action mounted inside a header card (see `HealthSummaryCard`) or an inline `+` per section.

### 8. Empty states
Use a single glyph + one muted sentence. Never illustrations. The icon IS the illustration.

### 9. Error / success feedback
- Field-level errors: inline below the field in `--danger`, 12px, no icon needed if the field also gets a danger border.
- Action-level success: toast (success type).
- Action-level failure: toast (error type) with the underlying error message.
- Optimistic local mutations are fine; if the server rejects, surface the rollback with an error toast and undo the local change.

### 10. Mobile-first, hit targets ≥ 44px
- All inputs use proper `inputmode` (`decimal`, `numeric`, `email`).
- Primary action lives in the bottom 100px of the viewport.
- No hover-only affordances.
- Anything visible only on hover must also be visible after a single tap.

### 11. Don't add filler
- No "Welcome back" headers.
- No "Quick stats" rows on the entry screen.
- No tutorial cards. The interface is the tutorial.
- If a screen feels empty, **the screen probably IS done**. Sit with it before adding.

## Where does this belong?

| New thing | Where |
|---|---|
| A primary, heavy, persistent workflow | New top-level view — extend `HeaderSegmented` to 4 segments |
| A secondary task launched from one screen | Bottom sheet on that screen |
| Settings, profile, dev tools | Settings sheet/screen — not a main nav slot |
| A confirmation or destructive action | Inline danger-tinted block (see `ProviderSheet`'s delete) — not a JS `confirm()` |
| A picker for a catalog item | `CatalogSelectField` (extend if the catalog doesn't exist yet) |
| Anything that resembles "click to edit a record" | Bottom sheet pre-filled with the record |

## When in doubt

1. Open `components.md` — your problem might already have a solution.
2. Open `patterns.md` — your interaction might already be defined.
3. Open `design-language.md` — the answer to "should I add a new color/font/size?" is almost always *no*.
4. Open an issue / Slack thread. **Don't quietly invent.** Drift compounds.

## After shipping a screen

- Update `screens.md` with the new anatomy diagram.
- If you extracted a reusable component, register it in `components.md` with the same one-line contract.
- If a new cross-cutting interaction emerged, add it to `patterns.md`.
- If you broke any rule above for a good reason, document the exception in `patterns.md` so it doesn't get treated as the norm.

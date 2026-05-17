# Dinary — Future Screens Guide

When designing new screens (history, dashboard, settings, etc.), follow these rules so they feel like one app.

## 1. Labels are the last resort
A field is recoverable from its content if any of the following is true:
- Its current value tells you what it is (a date, an amount, a tag list).
- A glyph in the field tells you (cal, #, search).
- It's the only thing it could be on this screen.

If two of those are true, **drop the label**. If none are, add a glyph before adding text.

## 2. Hierarchy uses geometry, not words
For any parent → child / belongs-to / narrows-into relationship:
- Indent the child by 30px.
- Draw a 1px L-connector in `--border-strong` (vertical line at x=18, elbow at bottom).
- Don't write "Group" / "Category" / "Parent" / "Child" — the indent IS the label.

This generalizes: account → transaction, project → task, currency → amount, etc.

## 3. State is shown, not spelled
Active / inactive, on / off, published / draft, enabled / disabled — never use words.
- **Active:** filled glyph (eye, check, dot) + solid gradient rule, accent-tinted.
- **Inactive:** outlined glyph (eye-off, dash, hollow dot) + dashed rule, muted-2 tone.

The pair must always be visually distinct enough to read at a glance with no text.

## 4. Icons replace verbs
| Verb | Icon |
|---|---|
| New / Add | plus |
| Manage / Configure | cog |
| Close / Cancel | x |
| Edit / Rename | edit (pencil) |
| Hide / Disable | eye-off |
| Show / Enable | eye |
| Save / Submit | save (floppy) |
| Search / Filter | search |
| Schedule / Date | cal |

If you need a verb the table doesn't cover, **add the icon to the table** (and to `Design System.html`) before using it. Don't fall back to text.

## 5. Numbers and codes use `--font-num`
Amounts, dates, ranges, ISO codes, IDs, version strings. Body text stays Inter.

## 6. Color discipline
- `--accent` (blue): primary action only — Save, selected state, focus ring, currency pill.
- `--danger` (red): scan / destructive only — never as accent.
- `--fg` / `--muted` / `--muted-2`: 90% of all text. Use `--muted` for labels and secondary content; `--muted-2` only for tertiary metadata (date ranges under event names, tertiary timestamps).
- Backgrounds: `--bg` for page, `--surface` for cards (rare in this design — most cards use `--field` instead), `--field` for form shells, `--field-deep` for recessed panels (manage lists, picker bodies).
- **Never invent a hue.** If you need a new accent, ask first.

## 7. Density
Every screen supports both modes. Build with comfortable spacing, then add a `density: 'compact' | 'comfortable'` prop and tighten:
- Section gap 18 → 14
- Row padding 6/6 → 4/6
- Form padding 16/16/100 → 12/14/96

## 8. Picker vs Manage on every catalog
Any catalog-backed dropdown (currency, group, category, event, tag, account, payee...) supports both:
- **Picker** — click the field → simple selectable list, click-to-pick.
- **Manage** — click the cog → edit/eye rows.

Trailing actions collapse for picker (full-width panel) and stay visible for manage (✕ to close).

## 9. Bottom action bar pattern
Every editor screen (entry, edit, settings, etc.) has a sticky bottom bar:
- Square 48×48 secondary action on the left (or omitted).
- Flex-1 primary action on the right with icon + label.
- Solid colors, no borders. Save = `--accent`, destructive = `--danger`.

For non-editor screens (lists, dashboards), use a floating + button at bottom-right instead.

## 10. Empty states
- Use a glyph + one short sentence in `--muted`. Never two sentences.
- Don't draw illustrations. The icon is the illustration.

## 11. Error / success
- Toast (existing pattern in `base.css`) — keep.
- Inline form errors: `--danger` text, 12px, no icon needed if the field also gets a `--danger` border.

## 12. Mobile-first, PWA-shaped
- Hit targets ≥ 44px.
- Inputs use proper `inputmode` (`decimal` for amount, `numeric` for codes).
- All interactions thumb-reachable; primary action lives in the bottom 100px.
- No hover-only affordances. Anything visible only on hover must also be visible after a single tap.

## 13. Don't add filler
- No "Welcome back" headers.
- No "Quick stats" rows on the entry screen.
- No tutorial cards. The interface is the tutorial.

If a screen feels empty, **the screen probably IS done**. Sit with it for a day before adding anything.

## 14. When in doubt
Open `Design System.html`. If it's not there, the answer is probably "don't add it."

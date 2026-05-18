# Dinary UI Specs

Living documentation of the Dinary PWA's visual language and interaction patterns. These files describe the **intent and contract** of the UI; the implementations in `webapp/src/` are the executable source of truth.

## What's in here

| File | Covers |
|---|---|
| `design-language.md` | Color, type, spacing, radii, density, accent discipline |
| `components.md` | Catalog of shipped components, one-line contracts, file pointers |
| `screens.md` | Anatomy of the three top-level screens (Add, Review, LLM) and navigation |
| `patterns.md` | Cross-cutting patterns — sheets, keyboard handling, scope selector, inline-create, picker-vs-manage, state dividers, status dots |
| `future-screens-guide.md` | Checklist + rules for adding a new screen |

## How to use these docs

- **Designing a new screen?** Start with `future-screens-guide.md`, then pull patterns from `patterns.md` rather than inventing new ones.
- **Touching an existing component?** Read its entry in `components.md` to find the file and understand the contract. Keep the contract intact unless you're also updating the doc.
- **Tweaking colors, type, or spacing?** Read `design-language.md` first — most "I need a new color" instincts are wrong.
- **Adding a Claude Code handoff?** Reference the relevant doc by name in the handoff brief instead of duplicating it.

## When to update

| Change | Update |
|---|---|
| Add or rename a component | `components.md` |
| New screen ships | `screens.md` |
| New cross-cutting pattern recurs in ≥2 places | `patterns.md` |
| Token added to `base.css` | `design-language.md` |
| Convention emerges from a PR review ("we should always do X") | `patterns.md` or `future-screens-guide.md` |

If a change makes a doc claim wrong, fix the doc in the same PR. Out-of-date docs are worse than no docs.

## What's *not* in here

- Exact pixel values — those live in the `.vue` files and `base.css`. The doc says "right-aligned with an underline"; the file says `height: 64px; border-bottom: 1px solid ...`. Pixels drift; rules don't.
- API schemas — those live in `specs/reference/`.
- Backend behavior — `specs/architecture/` and `specs/plans/`.
- Marketing or brand copy — Dinary's voice lives in the app itself.

## Quick links

- Tokens: `webapp/src/assets/base.css`
- Top-level shell: `webapp/src/App.vue`
- Views: `webapp/src/views/{AddView,ReviewView,LLMView}.vue`
- Components: `webapp/src/components/`

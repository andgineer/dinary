# Category Templates

## Terminology

A **category template** (RU UI: **набор категорий**) is a curated arrangement
of the category vocabulary: which categories are visible, which group each
belongs to, and what they're called. Categories are organised in two levels —
**group** and **category** — with the group as the rollup: related fine
categories (e.g. `food` and `fruit`) share a group so group totals stay
comparable across templates and over time.

## One evolving set

A user has a single evolving category set at any time. Onboarding seeds it
from a chosen template; switching templates later re-themes the existing set
in place — it does not create a second, coexisting set.

## Categories are never deleted

The full factory category vocabulary is seeded once into the database and
never deleted, even categories with no expenses. This guarantees any template
can be applied without breaking foreign keys, and every category ever used
remains resolvable for historical expenses.

User-created categories live in their own namespace, separate from the factory
vocabulary, so templates and reseeding never touch or retire them.

## Visibility

Each category carries:
- whether it's in the active template's visible subset,
- whether the user explicitly hid it (sticky — survives template switches and
  reseeding; never touched automatically),
- whether the vocabulary has retired it (the category was dropped or split
  into finer categories; kept only for historical expenses, never pickable
  again),
- whether it has ever been used on an expense (derived from expense history).

**Pickable for new expenses** = active, not hidden, and not retired. A
category stays active as long as it's in the active template's visible
subset or has ever been used — applying a new template never deactivates a
category with expense history.

Analytics and history always resolve a category by its identity regardless of
these flags: retiring or hiding a category never removes it from past reports
or trends.

## Applying a template

A template is a **complete** mapping over the whole factory vocabulary: for
every category it defines which group it belongs to in this template and
whether it's in this template's visible subset — including categories not in
that subset, so there are never orphaned factory categories with no group.
Applying a template:

- places every factory category in the group this template assigns it to,
- sets visibility to this template's visible/hidden split,
- bakes each category's display name from this template's label override, or
  the vocabulary's default name if the template doesn't override it, in the
  chosen language,
- leaves user-hidden categories hidden, and leaves user-created categories
  untouched (they sit outside every template's mapping; the user repositions
  them manually if needed).

A category the user has used stays visible after switching templates even if
the new template's visible subset doesn't include it.

## Vocabulary reconciliation

The category vocabulary and the factory templates are reconciled into the
database on every startup, by each category's stable identity:
- a new category in the vocabulary is added (not yet visible — reconciliation
  never picks a template),
- an existing category has its translations, template groupings, and template
  definitions updated in place,
- a category that disappears from the vocabulary is retired, not deleted —
  typically because it was split into finer categories that replace it.

User-created categories are never touched by this reconciliation, and a fresh
installation starts with no active template until onboarding picks one.

## Localization

Display names are resolved with this precedence: a user's manual rename, then
the active template's label override, then the vocabulary's default name for
the chosen language. Changing the UI language re-applies the active template
to re-bake names in the new language.

## Onboarding

A fresh installation has no active template — the user is asked to pick one
(plus a language) before any category-dependent view is usable. That choice is
the first template application.

The onboarding choices, in display order:

1. **Simple** — a lean starter for "just let me start".
2. **Active lifestyle** — sport/gear/travel granularity, no kids.
3. **Family & home** — kids + household, the broad mainstream choice.
4. **Freelancer** — separates personal and business spending in one wallet.

## User edits

Beyond picking a template, a user can: rename a category (label only, identity
stable), search for and activate any category not currently in their set
(including hidden or retired-but-used ones — activating un-hides it), hide a
category (sticky, never auto-restored), move a category to a different group,
create a new category in a group, and switch to a different template at any
time (re-themes the set; used and hidden categories are preserved).

Before switching, the user can preview a template's full contents — every
group and all its visible categories — to compare candidates without applying
any of them.

## Deferred: AI re-marking editor

Re-arranging a template — or producing a personal "My setup" template — with
AI assistance is planned but not built. It would reuse template application
with no new primitives.

# Category templates — design & implementation plan

Brainstorm-stage plan for predefined, switchable category templates
(EN: `category template`; RU UI: **набор категорий**). Template resources live in
`src/dinary/category_templates/` (`categories.yml` + one file per template). This
doc is the durable record of decisions; it will spawn a `specs/reference/` spec
once the model is built.

## Goal

Let a user start from a ready-made category arrangement that fits their
household instead of a blank slate, switch to a different one later without
losing history, and have analytics stay continuous across switches.

## Decided model

### Terminology
- EN (code, specs): `category template`. RU (UI): **набор категорий**.
- Dropped: `catalog`, `category set` (invented, not a real industry term).
- Hierarchy levels: `group` / `category`.

### One evolving set, templates applied additively
- A user has a single evolving set at any moment. Onboarding seeds it from a
  chosen template. "Switching later" = apply another template over the current
  set, not several coexisting live sets.

### Everything in the DB
- The whole category library lives as rows in `categories` (seeded once). No
  external-file lookups at runtime. Full-text search finds anything; selectors
  filter by visibility.
- **Categories are never deleted** (even with no linked expenses) so templates
  never break.
- `group`, `category`, `tag` all carry a stable `code` separate from the
  autoincrement `id`. `id` is the physical PK and is never renumbered; expenses
  and mapping tables keep referencing `id` unchanged.

### A template covers ALL categories
- A template is a **complete** mapping: for every category it defines (a) which
  group it belongs to in this template and (b) whether it is visible in this
  template's curated subset. So the active template has an opinion about the
  placement of every category — including ones not in its visible subset.
- Consequence: there are **no orphans**. Any shown category always sits in the
  active template's logical group for it. No "canonical/foreign" group headers.

### Granularity = categories within a group; the group is the rollup
- No category hierarchy/parent. Detail vs cumulative is expressed by the group:
  `food` and `fruit` are both categories in the **Food** group — fruit shown
  separately for detail, the group total gives cumulative food spend. Likewise
  `cafe` and `business_lunch` both sit in an **Eating out** group: the group total
  is all eating-out, the categories are the breakdown.
- A coarser template shows fewer categories per group; a finer one more. Because
  related fine categories share a group, group-level analytics stay comparable
  across templates and over time. This is why no separate rollup hierarchy is
  needed.

### Template file format
- `categories.yml` — the full category vocabulary, nothing else: `code → names`
  per language. No groups here (grouping is a per-template concern).
- One file per template, authored as direct, surveyable lists (no remap rules):
  - `groups` — this template's groups with names per language (order = display
    order).
  - `renames` — per-template label overrides that **preserve meaning** (same
    concept, different wording/locale, e.g. `mobile` → "Phone"). A rename must
    never change scope: narrowing/repurposing (cafe → "business lunch") is a
    *separate* category with its own `code`, not a rename — so history and
    analytics stay coherent. Optional, often empty.
  - `visible` and `hidden` — two maps `group → [category codes]`, same style.
    Together they place every category, so the active template has a home for
    each one (no orphans). The `hidden` block is long but grouped and readable.
- Templates reference categories by `code` only (language-neutral); display
  strings come from `categories.yml`, overridden by the template's `renames`.

### Authoring is AI-driven, never by hand
- Nobody hand-writes the full `visible`/`hidden` lists. The expected path: take an
  existing template and re-mark/re-arrange it with AI to fit yourself. The editor
  is AI-driven.

### Visibility
- Three stored flags + one derived predicate, each with a distinct owner:
  - `is_active` — in the active template's visible subset; template apply rewrites
    this wholesale.
  - `is_hidden` — user explicitly hid it; **sticky**, owned by the user; apply
    never touches it.
  - `is_retired` — the vocabulary dropped/split this code; seed-owned (see
    Idempotent seed). Not pickable even when `used`.
  - `used` — derived: `EXISTS (expense with this category)`.
- **Pickable for new expenses = `NOT is_retired AND NOT is_hidden AND (is_active OR used)`.**
  The `used` term keeps a category you actually used available after it falls out
  of the active template — unless it was retired or you hid it.
- **Analytics / history** read expenses directly and resolve the category by `id`
  for its name regardless of any flag — retiring or hiding never drops history.
- Efficiency: compute `used` with an indexed join
  (`LEFT JOIN (SELECT DISTINCT category_id FROM expenses)`), index on
  `expenses(category_id)`. No denormalized `used` flag/trigger — premature for
  this app's scale.

### Apply a template (clean new code, NOT the import seeder)
- Do **not** reuse / generalize `seed_classification_catalog` — it is import-only
  legacy for messy Google Sheets and irrelevant to future users.
- Apply template N projects N's definition onto the live rows:
  for every category set `group_id = N.group(category)`,
  `is_active = N.visible(category)`, bake `name` from N's `renames` for the chosen
  language (falling back to `categories.yml`); leave `is_hidden` untouched. Bump
  `app_metadata.catalog_version`.

### Idempotent seed (YAML files → DB)
- Templates live as package resources under `src/dinary/category_templates/`
  (`categories.yml` + one file per template). A clean, re-runnable seed loads them
  into the DB. This is NOT the import seeder.
- Reconcile **by `code`** — never by name, never deleting, never renumbering `id`:
  - new category code in `categories.yml` → insert (factory-code namespace);
    group codes and template definitions (groups, renames, visible/hidden)
    upserted by code the same way.
  - existing code → update names / grouping / template definitions in place
    (FK-safe; `id` and expenses untouched).
  - **factory code in DB but absent from `categories.yml`** → it left the
    vocabulary: set `is_active = false` AND `is_retired = true`. Kept for
    historical FK + analytics, not pickable for new expenses. Typical cause: the
    category was split into finer codes — the old one survives only for old
    expenses.
  - **user-created codes** (user namespace) are never touched by seed.
- Idempotent when files are unchanged (re-run is a no-op). When files change it
  cannot be idempotent by definition; it reconciles DB to files by code without
  breaking existing expenses.

### Seed modes & onboarding state
- `app_metadata.active_template` — code of the active template; empty/absent =
  none selected. Fresh seed leaves it empty.
- **Fresh seed (empty catalog):** insert the `categories.yml` vocabulary as
  category rows (all `is_active=false`) and all factory template definitions; set
  **no active template**. The PWA, seeing an empty `active_template`, shows the
  chooser (template + language); the user's pick is the first `apply`.
- **Adopt-existing seed (one-off — the personal DB):** a separate mode for a
  non-empty catalog that predates this feature.
  - Backfill `code` onto existing categories by **mapping each to its factory
    `code`** — a one-off hand-authored mapping (e.g. `еда`→`groceries`,
    `фрукты`→`fruit`, `алкоголь`→`alcohol`). Existing display names are **kept
    as-is — no rename** (code is identity, name is the label). Any category with
    no factory equivalent gets a custom-namespace code. Existing groups are kept
    and get custom codes.
  - Register a synthetic **custom template** ("My setup") in the DB from the
    current state: existing groups as its groups, all current categories visible
    under their current groups, the kept names carried as per-template `renames`;
    set it as the active template. No factory template is imposed.
  - Also load the factory vocabulary + factory templates as an **inactive
    library** so the user can switch later; because existing categories now share
    factory codes, a later switch reuses them instead of duplicating.
- **Origin marks what seed may touch:** template definitions and codes carry an
  origin (`factory` vs `custom`/user). A normal re-run reconciles only
  factory-origin rows by code (insert/update/retire); the **custom template and
  user-coded categories are never deleted or retired** — the custom template is
  absent from the files by design and must survive every re-run.

### Localization
- `categories.yml` holds default names per language keyed by `code`; a template's
  `renames` overrides them for that template. Language is chosen at apply time and
  the resolved name is **baked** into `categories.name`. Precedence: user manual
  rename > template `renames` > `categories.yml` default. `code` never changes.
  Changing UI language later = re-apply.

### Rendering "show category groups"
- A single join `categories → category_groups` on the live `group_id`, filtered
  by the visibility predicate, ordered by group then category sort. Template
  definitions are NOT consulted at render time — only at apply time.

### User edits later (mostly already in the PWA)
- Rename (label changes, `code` stays), add (new user-code or reuse existing by
  search → activation), hide (`is_hidden`), move between groups (`group_id`),
  plus the new "apply another template" action.

## Onboarding templates (proposed — pending confirmation)

Minimize the number so almost anyone finds a fit fast without browsing many
options. The visibility model + AI re-marking absorb the long tail, so the
starter list can be small. Proposed four, because each needs a distinct visible
subset (a single broad template can only carry one visible subset):

1. **Simple** — lean starter for "just let me start" (≈ `single-young-adult`).
2. **Active lifestyle** — no kids; sport/gear/travel granularity
   (≈ `active-couple-expats`).
3. **Family & home** — kids + household, the broad mainstream
   (≈ `family-young-kids`).
4. **Freelancer** — personal vs business in one wallet (≈ `freelancer`).

- `comprehensive-household` → not an onboarding choice; its breadth folds into the
  `categories.yml` vocabulary (the plain category dictionary; grouping now lives
  per-template).
- `zero-based-envelope` → not a starter taxonomy (budgeting-method, includes
  income/savings/debt an expense tracker won't log); drop from onboarding.

## Open items / next steps
- DONE: `src/dinary/category_templates/` created — `categories.yml` (69 categories)
  + `simple` / `active` / `family` / `freelancer`, each covering the full
  vocabulary (validated); old `catalogs/` removed.
- Confirm the onboarding template list and the four drafts' contents.
- Schema migration: add `code` to groups/categories/tags, add `is_hidden` and
  `is_retired`, backfill codes, index `expenses(category_id)`; do not touch
  expenses/mapping.
- Define the template-definition storage in the DB (per-template groups, renames,
  visible/hidden membership), keyed by `code`, with an `origin` (factory|custom).
  Add `app_metadata.active_template`.
- Adopt-existing seed mode for the personal DB (one-off hand-mapping of existing
  categories to factory codes, names kept; custom template from current state;
  preserved across re-runs).
- AI re-marking editor (later).

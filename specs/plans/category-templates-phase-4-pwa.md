# Phase 4 — PWA: onboarding & category management

Vue 3 + Pinia frontend in `webapp/`. Touch points:
`src/api/catalog.js`, `src/stores/catalog.js`, `src/stores/frequentCategories.js`,
`src/components/CategorySheet.vue`, `src/components/CategoryQuickPicks.vue`,
`src/composables/catalogManage.js`, `src/views/OnboardingTemplate.vue` (new),
`src/components/TemplateList.vue` (new).
RU UI term: **набор категорий**. Build via `uv run inv build-static`; tests via
`cd webapp && npm test`.

`src/components/CatalogSelectField.vue` and its test are **not touched** by
this phase — category management is nested in `CategorySheet.vue` (§4), not
built on `CatalogSelectField`.

`src/components/CategoryQuickPicks.vue` needs **no changes** — its
`frequent_categories` already come pre-filtered from
`build_catalog_snapshot` server-side.

## 0. Backend addendum — group code on visible categories
`VisibleCategoryItem` (`GET /api/categories`) gains `group_code: str`,
sourced via a join on `category_groups.code` in `list_visible_categories.sql`
(`category_groups.code` already exists, added by migration 0006). Thread it
through `VisibleCategoryRow` (`db/storage.py`) and `get_categories_response`
(`api/controllers/category_templates.py`), which maps each row to
`VisibleCategoryItem`. This is the only field Manage mode (§4) needs to call
`createCategory(name, group_code)` and `moveCategory(code, group_code)` —
without it there is no client-side source for group codes. Update the
existing visibility test (`tests/category_templates/test_visibility.py`) to
assert the new field.

## 1. API client — `src/api/catalog.js`
Add calls for the Phase 3 endpoints: `listTemplates()`,
`getActiveTemplate()`, `applyTemplate(code, lang)`, `getCategories()` (with
`If-None-Match` / ETag handling like `fetchCatalog`), `searchCategories(q)`,
`createCategory(name, groupCode)`, `renameCategory(code, name)`,
`activateCategory(code)`, `hideCategory(code)`, `unhideCategory(code)`,
`moveCategory(code, groupCode)`.

## 2. Onboarding (no active set → chooser)
- `stores/catalog.js` exposes an `activeTemplate` ref and a `templateReady`
  promise. `activeTemplate` starts as `undefined` (unknown — distinct from
  `null`, which means "no template selected"). Init sequence: call
  `getActiveTemplate()`, set `activeTemplate` to its `active_template` value
  (`null` or a template code), then resolve `templateReady`. No localStorage
  fast-path — always fetching from the server avoids stale local state after a
  server reset or re-seed (localStorage would cache a non-null value even when
  the server has reset `active_template` to absent).
  `activeTemplate` is also updated on every `applyTemplate` call (in-memory
  only; no localStorage).
- Invalidation: `stores/catalog.js` also holds a separate `visibleCategories`
  cache (see §3) with its own `catalog_version`. On a 200 response from
  `GET /api/categories` (version changed), it also calls `getActiveTemplate()`
  again and updates the stored value. This covers apply-from-another-device
  and re-seed scenarios.
- **No Vue Router exists in this app** — `webapp/` has no `vue-router` dependency
  and no `router/`; navigation is a flat tab switch in `App.vue`
  (`tab = ref("add")` plus a `v-if`/`v-else-if` chain over view components), so
  there are no routes or deep links to guard. Gate onboarding the same way the app
  already gates top-level state (cf. its `v-if="isDev"` banner), with a three-way
  conditional on `catalogStore.activeTemplate` driven by the `undefined` /
  `null` / `<code>` states from above. `App.vue` cannot simply `await
  templateReady` before its first render — that render has already happened by
  the time `onMounted`'s `init()` runs — so the conditional itself must cover
  the pre-resolution state to avoid a flash of either screen:
  - `activeTemplate === undefined` (still loading): render nothing (empty
    branch — the gap is sub-100ms against the local API and needs no spinner);
  - `activeTemplate === null`: render `OnboardingTemplate`;
  - otherwise: render the existing `<header>…</header><main>…</main>`.
  Because the whole app is one view with no routes, this single conditional is
  the complete equivalent of "funnel every entry through the chooser" — there
  is no separate deep-link case to cover.
- `ui_lang` resolution (shared by §2 and §5): the first two letters of
  `navigator.language`, lowercased, falling back to `ru` if that locale is
  absent from the template's language set (`names`/`taglines` keys — all
  factory templates share the same language set per `validate()`).
- `src/components/TemplateList.vue` (new, shared by §2 and §5): given
  `templates` (from `listTemplates()`), `activeCode`, and `lang`, renders each
  набор's localized name (`names[lang]`) and tagline (`taglines[lang]`) as the
  "this is you if…" descriptor, marks the active one, and emits
  `apply(code)` on tap.
- `src/views/OnboardingTemplate.vue` (new): a language selector (available
  languages = the key set of `names` from the first template returned by
  `listTemplates()`) above `TemplateList`. One tap on a набор →
  `applyTemplate(code, lang)`, persist `lang` to `localStorage` as
  `dinary:catalog:lastLang` (reused by §5, which has no language selector of
  its own) → continue into the app. Keep it fast: pick-and-go, no mandatory
  tweaking (the design's "just start").

## 3. Category picker with search-activate
- Visual design is already finalized — see `specs/plans/design_handoff_not_in_set/README.md`
  (Variant B: search results split into in-set rows and a fenced "Not in your
  set · add with one tap" section with one-tap activate-then-select). No further
  design work needed — implement per that handoff, adapted to the
  `getCategories()` / `searchCategories(q)` data sources below (the handoff's
  `searchableCategories()` getter over the admin snapshot is superseded by the
  live search endpoint).
- `CategorySheet.vue`: render the visible grouped list (empty-query state)
  from `stores/catalog.js`'s `visibleCategories` (§2's cache, sourced from
  `getCategories()`), grouped by `group_id`/`group_name` and ordered by
  `group_sort_order`. Selection still emits the numeric `id` (carried on both
  `VisibleCategoryItem` and `CategorySearchItem`) via `select` — the contract
  for `CategorySheet`'s four existing consumers (`ExpenseForm`,
  `ExpenseEditSheet`, `ReviewView`, `ReceiptCascadeCard`), which all key off
  `category_id`, is unchanged.
- Add a search box: on input call `searchCategories(q)` with a ~300 ms debounce
  (includes hidden / not-in-set, excludes retired). Split results into in-set
  rows (top, normal `.flat-item`) and a fenced "Not in your set · add with one
  tap" section (inactive + hidden matches), per the design handoff. Selecting
  an addable result calls `activateCategory(code)` (or `unhideCategory(code)`
  if the match is hidden but already active) then selects it — the "find
  anything, auto-activate" flow. The classifier/quick-picks keep using the
  visible set.
- Edge case: after `activateCategory` a category whose `group_id` is still NULL
  (no active template or code absent from its definition) cannot appear in the
  grouped picker. `CategorySearchItem` carries no group info, so detect this by
  checking whether the activated `code` is present in `visibleCategories` after
  the post-activation refresh below — absent ⇒ still `group_id IS NULL` ⇒
  display it inline in the search results with a label "(без группы /
  ungrouped)" and allow selection directly from the search result — do not rely
  on it appearing in the grouped list after activation.
- `stores/catalog.js`: `visibleCategories` is keyed by code, fetched lazily on
  first `CategorySheet` open. Refresh triggers: (a) every mutation in §3–§5
  (`activateCategory`/`unhideCategory`/`hideCategory`/`renameCategory`/
  `moveCategory`/`createCategory`/`applyTemplate`) returns the new
  `catalog_version` directly — if it differs from the cached value, refetch
  `getCategories()`; (b) on `visibilitychange`, mirroring `App.vue`'s existing
  `handleVisibilityChange` pattern for `reviewStore.loadIfNeeded()`, to pick up
  apply-from-another-device / re-seed without polling. This `catalog_version`
  is cached independently of the admin `snapshot`'s, used by `GET /api/catalog`,
  even though both endpoints share the same underlying counter (Phase 3 §2).
- Each successful addable-row activation also calls the nudge counter from §6.

## 4. Manage categories — nested in CategorySheet
- `CategorySheet.vue` gains a "Manage" toggle (cog icon, same pattern as the
  existing group/event/tag manage toggles in `ExpenseForm`) next to the search
  box. The toggle and its panel are part of `CategorySheet` itself, so they
  appear identically in all four places the sheet is opened from
  (`ExpenseForm`, `ExpenseEditSheet`, `ReviewView`, `ReceiptCascadeCard`) —
  intentional: one consistent "manage my categories / switch my set" surface
  regardless of entry point, rather than a separate context per caller.
  Toggling it switches the sheet body from "pick a category" to a managed
  view over `visibleCategories` (which now carry `group_code`, §0):
  - per category: hide (`hideCategory`), rename — inline label edit, `code`
    stable (`renameCategory`), move to another group (small picker over the
    groups present in `visibleCategories` → `moveCategory(code, group_code)`;
    a group with no currently-visible categories has no row in
    `visibleCategories` and so isn't offered as a move target — acceptable,
    since every template places all of its categories into a group, so an
    empty group can only happen for a user-created group with nothing left in
    it);
  - per group: "+ add category" → `createCategory(name, group_code)`, the
    code-based replacement for the old id/name-keyed "add" flow, generating a
    `u_`-prefixed code server-side (Phase 2's `create_category`).
- Hide is **sticky** across template switches; "delete" becomes "hide"
  (categories are never deleted). **Unhide is not duplicated in Manage
  mode** — a hidden category reappears via §3's search "Not in your set"
  section (tap → `unhideCategory` + select), so Manage mode only needs
  hide/rename/move/add.
- Retire the id-based surface this replaces — `add_category` /
  `edit_category` / `delete_category` (`catalog_writer_categories.py`) and their
  `POST /api/catalog/categories`, `PATCH /api/catalog/categories/{id}`,
  `DELETE /api/catalog/categories/{id}` endpoints (`api/catalog.py:121-172`).
  It is not just "add/delete" — `edit_category` also accepts `group_id` and
  `is_active` (`CategoryPatchBody`, wired straight into
  `UPDATE categories SET group_id = ?, is_active = ?, …`), and leaving that path
  reachable would let a caller bypass `move_category` (no `group_code`
  validation) and `activate_category`/`apply_template` (no NULL-`group_id`
  resolution, no respect for "in active template's visible subset" semantics)
  and write directly into columns those functions now own. Rename is the only
  part of `edit_category` with no code-based equivalent — give it one
  (`rename_category(con, code, name)`, label-only, alongside the others in
  Phase 2 §2) rather than keeping the old id-based PATCH alive for that one
  field. Once Manage mode is on `createCategory`/`renameCategory`/
  `hideCategory`/`unhideCategory`/`moveCategory`, delete `add_category`,
  `edit_category`, `delete_category` and their three router endpoints outright.
- Once those endpoints are gone, also remove the now-fully-dead frontend code
  that targeted them — it is already unreachable today (no UI ever sets
  `manageMode.category` or `newing.value === "category"`), but it would
  otherwise be left calling deleted routes:
  - `stores/catalog.js`'s `category` entries in the `add`/`patch`/`remove`/
    `reactivate`/`deactivate` dispatch maps (`adminAddCategory`,
    `adminPatchCategory`, `adminDeleteCategory`, `adminReactivateCategory`,
    `adminDeactivateCategory`, plus the corresponding exports in
    `api/catalog.js`);
  - `EditModal.vue`'s `kind === "category"` branches;
  - `ExpenseForm.vue`'s `kind === "category"` branch in its `add`/edit wiring.

## 5. Switch набор — nested in CategorySheet's Manage mode
- A single extra row at the top of Manage mode: "Switch category set →
  {active набор's name}". Expanding it renders `TemplateList` (§2, no
  language selector — applies with `localStorage`'s `dinary:catalog:lastLang`,
  falling back to `ui_lang` if never set) showing all наборы, the active one
  marked, tap → `applyTemplate(code, lang)`.
- Copy must set expectations: switching re-themes groups for the template's
  categories; your used categories stay; hidden ones stay hidden.
- This is the **only** entry point to switching — appropriate for a rarely-used
  action (the common path is onboarding, once). No new tab, no new top-level
  view.

## 6. "Wrong набор" nudge
- `stores/catalog.js` (or a small dedicated module) tracks out-of-set
  activations from §3's "Not in your set" flow in `localStorage`
  (`dinary:catalog:oosActivations`, an array of activation timestamps).
- On each activation, push `Date.now()` and prune entries older than 30 days.
- When the pruned count reaches 3, show an info toast once — e.g. "You've
  added several categories outside your set — open the category picker's
  Manage → Switch category set to see other sets." — then clear
  `oosActivations` so the next nudge requires 3 fresh activations.
- Pure frontend, no backend changes, no template category-list exposure
  needed (matches the "generic frequency nudge" decision — precise
  "this looks like Travel" matching is out of scope).

## 7. Tests (`webapp`, `npm test`)
- `App.vue`'s top-level conditional covers all three `activeTemplate` states:
  `undefined` (initial, before `getActiveTemplate()` resolves) ⇒ neither
  `OnboardingTemplate` nor header/main renders; `null` ⇒ `OnboardingTemplate`
  renders in place of header+tabs; a template code ⇒ the normal app renders.
  There are no routes/deep-links in this app, so this single conditional is the
  only case to cover.
- `TemplateList.vue`: renders `names[lang]`/`taglines[lang]` with `ru` fallback
  when `lang` is absent from a template's language set; marks the active code;
  tap emits `apply` with the code.
- Picker renders visible grouped list from `visibleCategories`, ordered by
  `group_sort_order`; search surfaces a hidden/inactive category in the
  "Not in your set" section; selecting it activates/unhides and selects it;
  ungrouped (NULL `group_id`) activated categories show inline with the
  "без группы" label.
- Manage mode: hide removes a category from the grouped picker (its expense
  history stays intact); rename updates the label without changing `code`;
  move changes its group; "+ add category" calls `createCategory` and the new
  category appears under the chosen group.
- Switch набор row: lists наборы via `TemplateList`, applying updates the
  visible grouping; reuses `dinary:catalog:lastLang` without showing a
  language selector.
- Nudge: 3 out-of-set activations within 30 days trigger the toast once and
  reset the counter; fewer than 3 does not trigger it.

## Out of scope (later, not a phase)
- AI re-marking editor (generate/re-arrange a набор from an existing one with AI).
  It produces a `category_templates` row (origin custom) and reuses `apply_template`;
  no new domain primitives — schedule after the four phases land.

## Done gate
`cd webapp && npm test` green; `uv run inv build-static` succeeds; backend
`uv run inv pre` + `uv run pytest` still green.

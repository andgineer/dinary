# Phase 4 — PWA: onboarding & category management

Vue 3 + Pinia frontend in `webapp/`. Touch points already present:
`src/api/catalog.js`, `src/stores/catalog.js`, `src/stores/frequentCategories.js`,
`src/components/CategorySheet.vue`, `src/components/CategoryQuickPicks.vue`,
`src/components/CatalogSelectField.vue`, `src/composables/catalogManage.js`.
RU UI term: **набор категорий**. Build via `uv run inv build-static`; tests via
`cd webapp && npm test`.

## 1. API client — `src/api/catalog.js`
Add calls for the Phase 3 endpoints: `listTemplates()`,
`getActiveTemplate()`, `applyTemplate(code, lang)`, `getCategories()`,
`searchCategories(q)`, `createCategory(name, groupCode)`, `renameCategory(code, name)`,
`activateCategory(code)`, `hideCategory(code)`, `unhideCategory(code)`,
`moveCategory(code, groupCode)`.

## 2. Onboarding (no active set → chooser)
- `stores/catalog.js` exposes a `templateReady` promise that resolves once
  `active_template` is known. Init sequence: call `getActiveTemplate()`, store
  the result, then resolve `templateReady`. No localStorage fast-path — `App.vue`
  awaits the promise before deciding what to render, so there is no UI flash; and
  always fetching from the server avoids stale local state after a server reset or
  re-seed (localStorage would cache a non-null value even when the server has reset
  `active_template` to absent).
  Updated on every `applyTemplate` call (store's in-memory value; no localStorage).
- Invalidation: on every `GET /api/categories` response the store compares the
  returned `catalog_version` against its cached value (existing `If-None-Match`
  mechanism in `api/catalog.js`). On a 200 response (version changed), it calls
  `getActiveTemplate()` again and updates the stored value. This covers
  apply-from-another-device and re-seed scenarios.
- **No Vue Router exists in this app** — `webapp/` has no `vue-router` dependency
  and no `router/`; navigation is a flat tab switch in `App.vue`
  (`tab = ref("add")` plus a `v-if`/`v-else-if` chain over view components), so
  there are no routes or deep links to guard. Gate onboarding the same way the app
  already gates top-level state (cf. its `v-if="isDev"` banner): in `App.vue`'s
  `init()`, `await catalogStore.templateReady` before the first render decision,
  then wrap the existing `<header>…</header><main>…</main>` in
  `v-if="catalogStore.activeTemplate !== null"` / `v-else` renders
  `OnboardingTemplate`. Because the whole app is one view with no routes, this
  single conditional is the complete equivalent of "funnel every entry through the
  chooser" — there is no separate deep-link case to cover.
- `src/views/OnboardingTemplate.vue` (new): lists наборы from `listTemplates()`
  showing each set's localized name (`names[ui_lang]`) and tagline
  (`taglines[ui_lang]`) as the "this is you if…" descriptor (`ui_lang` = the
  app's current UI locale, falling back to `ru` if the locale is absent from
  the template's language set); a language selector (available languages = the
  key set of `names` from the first template in the list — all factory templates
  are guaranteed to share the same language set by `validate()`);
  one tap → `applyTemplate(code, lang)` → continue into the app. Keep it
  fast: pick-and-go, no mandatory tweaking (the design's "just start").

## 3. Category picker with search-activate
- Visual design is already finalized — see `specs/plans/design_handoff_not_in_set/README.md`
  (Variant B: search results split into in-set rows and a fenced "Not in your
  set · add with one tap" section with one-tap activate-then-select). No further
  design work needed — implement per that handoff.
- `CategorySheet.vue` / `CatalogSelectField.vue`: render the visible grouped list
  from `getCategories()` (group headers + categories, by `group_sort_order`).
- Add a search box: on input call `searchCategories(q)` with a ~300 ms debounce
  (includes hidden / not-in-set, excludes retired). Selecting a result that isn't
  visible calls `activateCategory(code)` then selects it — the "find anything,
  auto-activate" flow. The classifier/quick-picks keep using the visible set.
- Edge case: after `activateCategory` a category whose `group_id` is still NULL
  (no active template or code absent from its definition) cannot appear in the
  grouped picker. In this case, display it inline in the search results with a
  label "(без группы / ungrouped)" and allow selection directly from the search
  result — do not rely on it appearing in the grouped list after activation.
- `stores/catalog.js`: hold visible categories keyed by code; refresh on
  `catalog_version` change (existing ETag-driven refresh).

## 4. Manage existing categories
- In the catalog-manage surface (`composables/catalogManage.js` + its component):
  - add a brand-new category via `createCategory(name, groupCode)` — the
    code-based replacement for the old id/name-keyed "add" flow, generating a
    `u_`-prefixed code server-side (Phase 2's `create_category`);
  - hide / unhide a category (`hideCategory`/`unhideCategory`) — note hide is
    **sticky** across template switches; "delete" becomes "hide" (categories
    are never deleted);
  - move a category to another group (`moveCategory`);
  - rename stays a **label-only** edit (`code` stable).
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
  field. Once the manage surface is on `createCategory`/`renameCategory`/
  `hideCategory`/`unhideCategory`/`moveCategory`, delete `add_category`,
  `edit_category`, `delete_category` and their three router endpoints outright.

## 5. Switch / apply another template ("сменить набор")
- A "наборы категорий" screen: lists templates (reuse the onboarding component),
  shows the active one, and applies a different one (`applyTemplate`). Copy must
  set expectations: switching re-themes groups for the template's categories;
  your used categories stay; hidden ones stay hidden.
- **Entry point — needs a decision, not assumed:** this app has **no
  settings/menu surface today** (`webapp/src/views/` holds only
  `AddView`/`ReviewView`/`AnalyticsView`/`IncomeView`/`LLMView`, switched via a
  flat `tab = ref("add")` chain in `App.vue`; category management currently
  lives inside `ExpenseForm.vue` via `composables/catalogManage.js`, which has
  no standalone screen either). Pick one before implementing: (a) add a new
  top-level tab/entry point for catalog management (covering both this screen
  and §4's manage surface), or (b) nest "сменить набор" next to the existing
  hide/unhide/move actions wherever §4 ends up placing them. Document the
  choice in this plan once made — "reachable from settings" is not an existing
  concept in this codebase.

## 6. Tests (`webapp`, `npm test`)
- Onboarding shows when active is `null`, hidden after apply. `App.vue` awaits
  `templateReady` before its first render decision — no flash of the normal tabs
  before the API response arrives. There are no routes/deep-links in this app, so
  the only case to cover is the top-level conditional itself:
  `active_template === null` ⇒ `OnboardingTemplate` renders in place of the
  header+tabs; non-null ⇒ the normal app renders.
- Picker renders visible grouped; search surfaces a hidden category; selecting it
  activates and selects it.
- Hide removes from picker; unhide restores; move changes group.
- Add/delete/rename-UI migration: "add" calls `createCategory` and the new
  category appears in the picker under the chosen group; "delete" calls
  `hideCategory` and the category disappears from the picker but its expense
  history is intact; rename calls `renameCategory` and updates the label without
  changing the underlying `code`.
- Switching sets updates the visible grouping.

## Out of scope (later, not a phase)
- AI re-marking editor (generate/re-arrange a набор from an existing one with AI).
  It produces a `category_templates` row (origin custom) and reuses `apply_template`;
  no new domain primitives — schedule after the four phases land.

## Done gate
`cd webapp && npm test` green; `uv run inv build-static` succeeds; backend
`uv run inv pre` + `uv run pytest` still green.

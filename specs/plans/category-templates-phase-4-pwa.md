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
`searchCategories(q)`, `activateCategory(code)`, `hideCategory(code)`,
`unhideCategory(code)`, `moveCategory(code, groupCode)`.

## 2. Onboarding (no active set → chooser)
- On app start (or first time the category picker is needed),
  `stores/catalog.js` calls `getActiveTemplate()`. If `null` → route to an
  onboarding view. Cache the result in the Pinia store (persist via `localStorage`)
  so subsequent app starts skip the round-trip; invalidate the cache on
  `applyTemplate` or when `catalog_version` changes.
- `src/views/OnboardingTemplate.vue` (new): lists наборы from `listTemplates()`
  showing each set's localized name (`names[ui_lang]`) and tagline
  (`taglines[ui_lang]`) as the "this is you if…" descriptor; a language
  selector; one tap → `applyTemplate(code, lang)` → continue into the app.
  Keep it fast: pick-and-go, no mandatory tweaking (the design's "just start").

## 3. Category picker with search-activate
- `CategorySheet.vue` / `CatalogSelectField.vue`: render the visible grouped list
  from `getCategories()` (group headers + categories, by `group_sort_order`).
- Add a search box: on input call `searchCategories(q)` with a ~300 ms debounce
  (includes hidden / not-in-set, excludes retired). Selecting a result that isn't visible calls
  `activateCategory(code)` then selects it — the "find anything, auto-activate"
  flow. The classifier/quick-picks keep using the visible set.
- `stores/catalog.js`: hold visible categories keyed by code; refresh on
  `catalog_version` change (existing ETag-driven refresh).

## 4. Manage existing categories
- In the catalog-manage surface (`composables/catalogManage.js` + its component):
  - hide / unhide a category (`hideCategory`/`unhideCategory`) — note hide is
    **sticky** across template switches;
  - move a category to another group (`moveCategory`);
  - rename stays the existing per-category label edit (label only; code stable).
- Migrate existing id-based add/delete UI to the code-based endpoints; "delete"
  becomes "hide" (categories are never deleted). Once migrated, remove the
  id-based add/delete endpoints from `catalog_writer_categories.py`.

## 5. Switch / apply another set ("сменить набор")
- A "наборы категорий" screen reachable from settings: lists templates (reuse
  the onboarding component), shows the active one, and applies a different one
  (`applyTemplate`). Copy must set expectations: switching re-themes groups for
  the set's categories; your used categories stay; hidden ones stay hidden.

## 6. Tests (`webapp`, `npm test`)
- Onboarding shows when active is `null`, hidden after apply.
- Picker renders visible grouped; search surfaces a hidden category; selecting it
  activates and selects it.
- Hide removes from picker; unhide restores; move changes group.
- Delete-UI migration: the old "delete" action calls `hideCategory`; category
  disappears from the picker but its expense history is intact.
- Switching sets updates the visible grouping.

## Out of scope (later, not a phase)
- AI re-marking editor (generate/re-arrange a набор from an existing one with AI).
  It produces a `category_sets` row (origin custom) and reuses `apply_template`;
  no new domain primitives — schedule after the four phases land.

## Done gate
`cd webapp && npm test` green; `uv run inv build-static` succeeds; backend
`uv run inv pre` + `uv run pytest` still green.

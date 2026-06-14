# Category-set switching: discoverable access + content preview

## Goal

Two UX problems with category templates ("наборы категорий"):

1. **Switching a set is undiscoverable.** The only prompt is a transient `info`
   toast (`oosNudge.js`) that auto-dismisses before the user can react, and the
   switch UI itself is buried behind the easily-missed gear in `CategorySheet`'s
   search row → Manage → "Switch category set".
2. **Picking a set is blind.** Onboarding and the in-app switcher show only the
   set `name` + `tagline`. The user cannot see which groups/categories a set
   actually contains, so the choice rests on vague names.

This plan makes switching reachable from clear, persistent entry points and lets
the user see a set's **full** contents (all groups, all visible categories)
before choosing — comparing sets by toggling a selector, not by reading a wall
of text.

---

## Design decisions (settled in brainstorm)

- **Preview = master-detail, not truncation, not tap-to-expand.** A set selector
  (chips) on top; below it a panel showing the *full* visible contents of the
  selected set, grouped. One set's contents on screen at a time (not four
  stacked), switched in one tap with the panel updating in place. Groups are
  shown as layout; categories carry the meaning, so nothing is truncated.
- **One shared preview component** serves both onboarding and the in-app
  switcher.
- **Persistent banner** replaces the transient nudge toast.
- **Permanent entry point**: a bottom row in `CategorySheet` (visible in any
  mode) — left side opens the set switcher, right side toggles Manage mode.
- The barely-visible gear in the search row is **removed**.
- On the Add-expense screen a gear is added **inside the existing category
  select-row** (left of the chevron) — **no new header**, no added height — that
  opens `CategorySheet` straight into Manage mode.

---

## Backend

### 1. Template preview data — `src/dinary/api/controllers/category_templates.py`

Extend `CategoryTemplateItem` with an ordered `groups` field so the client can
render the full preview multilingually (no per-lang refetch, consistent with the
existing multilang `names`/`taglines`).

New Pydantic models:

```python
class TemplatePreviewCategory(BaseModel):
    names: dict[str, str]          # lang -> display name

class TemplatePreviewGroup(BaseModel):
    code: str
    names: dict[str, str]          # lang -> group name
    categories: list[TemplatePreviewCategory]  # visible only, template order

class CategoryTemplateItem(BaseModel):
    code: str
    names: dict[str, str]
    taglines: dict[str, str]
    origin: str
    groups: list[TemplatePreviewGroup]   # NEW
```

`list_category_templates(con)` builds `groups` from each template's
`definition_json` (already loaded there):

- Iterate `definition["groups"]` **in order** (this is the template's display
  order — never sort).
- For each group, iterate `definition["visible"][group_code]` **in order**;
  skip groups with no visible codes.
- Available langs = `definition["names"].keys()`.
- Per category code, resolve the name for each lang reusing the existing
  precedence (extract a shared helper from `category_apply._resolve_name` rather
  than duplicating): `renames[code][lang]` → `category_translations[code][lang]`
  → `category_translations[code]["ru"]` → `code`. Group names: `groups[code][lang]`
  → `groups[code]["ru"]` → `group_code`.

Resolve translations with **one** `SELECT code, lang, name FROM
category_translations` query into a `{code: {lang: name}}` dict, not a query per
code/lang. Preview reflects the *template's* factory layout (independent of the
user's current edited catalog), which is correct for "what you'd get".

To avoid duplicating name precedence between `apply_template` and the preview,
extract the rename/translation lookup into a small reusable function (e.g.
`category_apply.resolve_category_name(translations, definition, code, lang)` that
takes a preloaded translations map) and have both call sites use it.

### 2. Tests — `tests/category_templates/`

- `/api/category-templates` response now includes `groups`: ordered, every
  visible code present, `renames` applied, group order preserved, all template
  langs present per name.
- A renamed code surfaces the rename, not the vocabulary name.
- Hidden codes are absent from the preview.

---

## Frontend

### 3. Shared preview picker — `webapp/src/components/TemplatePreviewPicker.vue` (new)

Master-detail. Props: `templates`, `lang`, `activeCode`. Emits `apply(code)`.

- Local `selectedCode` ref; defaults to `activeCode ?? templates[0]?.code`.
- **Selector**: wrapped chip row of set names (localized); selected chip has the
  active style; current `activeCode` chip gets a small check/badge.
- **Detail panel**: for the selected template, render every `group` with its
  full localized `categories` joined inline (`group name` + comma-separated
  category names). Scrolls if tall; only one set shown at a time.
- **Apply button**: "Выбрать этот набор" / localized — emits `apply(selectedCode)`;
  disabled while applying and when `selectedCode === activeCode`.
- Reuse the existing `localized(field, obj, lang)` pattern from `TemplateList.vue`.

`TemplateList.vue` is superseded; remove it once both call sites move over.

### 4. Onboarding — `webapp/src/views/OnboardingTemplate.vue`

Replace `<TemplateList>` with `<TemplatePreviewPicker>`. Keep the existing
language row and `apply()` logic (it already calls `catalog.applyTemplate`).

### 5. In-app switcher — `webapp/src/components/TemplateSwitchSheet.vue` (new)

A `BaseSheet` wrapping `TemplatePreviewPicker`:

- Loads templates via `catalogApi.listTemplates()` on first open (cache in the
  component); resolves `templateLang` the same way `CategorySheet.toggleManage`
  does today (`dinary:catalog:lastLang` → `resolveUiLang`).
- `activeCode = catalog.activeTemplate`.
- On `apply`: `catalog.applyTemplate(code, lang)`, persist `lastLang`, toast
  "Category set switched", close.
- Keeps the existing hint text ("Switching re-themes groups… used categories
  stay; hidden ones stay hidden").

**Single mount, opened via shared state.** Add to `stores/catalog.js` (or a tiny
ui store): `templateSwitchOpen` ref + `openTemplateSwitch()` / `closeTemplateSwitch()`.
Mount `<TemplateSwitchSheet>` once in `App.vue` driven by that flag, so both the
banner (App level) and `CategorySheet`'s set row open the same instance without
nested-sheet juggling.

### 6. CategorySheet — `webapp/src/components/CategorySheet.vue`

- **Remove** the gear button (`manage-toggle-btn`) from the search row.
- **Remove** the inline template-switch UI (`switch-template-row`,
  `switch-template-panel`, the embedded `TemplateList`, `templates`/`templateLang`/
  `applyingTemplate` state and `toggleSwitchTemplate`/`applySwitchTemplate`).
- **Add a persistent bottom bar** (rendered in any mode — search, manage, or
  default), pinned at the sheet bottom:
  - Left: `Набор: {activeTemplateName} ›` button → `catalog.openTemplateSwitch()`.
  - Right: Manage toggle icon — `Settings` when off, `X` when on — calls the
    existing `toggleManage()` logic (manage mode itself stays as-is). This is the
    new, more visible home for the toggle.
- Keep `activeTemplateName`, but source the active template name without the old
  in-sheet template load: read it from the switch sheet's cache or add a light
  `catalog.activeTemplateName` getter. Simplest: have `TemplateSwitchSheet`/store
  expose the resolved active name; fall back to `catalog.activeTemplate` code.

### 7. Add-expense category gear — `webapp/src/components/ExpenseForm.vue`

Inside the existing `.category-select-row` (no new header), add a gear
`IconBtn`/button just left of the `ChevronRight`:

- `@click.stop` (so the row's open-in-pick-mode handler does not also fire) opens
  the category sheet **in Manage mode**.
- Add a `manageOnOpen` ref; set it true before `categorySheetOpen = true`, pass
  `:initial-manage="manageOnOpen"` to `CategorySheet`; reset after open.
- `CategorySheet`: add prop `initialManage` (default false); in the `open` watch,
  set `manageMode.value = initialManage` when opening.

Style: small muted icon, vertically centered; row stays `min-height: 46px`.

### 8. Persistent nudge banner

- `webapp/src/composables/oosNudge.js`: instead of (or in addition to) the toast,
  set a persisted flag when the threshold is hit — e.g. `catalog.showSetNudge`
  (backed by `localStorage` key `dinary:catalog:nudgeActive`). Keep the 3-in-30-days
  threshold and counter reset. Drop the `info` toast path.
- `webapp/src/App.vue`: render a strip mirroring `queue-strip` (full-width, under
  the header) when `catalog.showSetNudge`:
  - Text: "Вы часто добавляете категории вне набора."
  - Action button "Сменить набор" → `catalog.openTemplateSwitch()` and clear the
    flag.
  - Dismiss `✕` → clear the flag (counter keeps accruing as today).
- Mount `<TemplateSwitchSheet>` here (see §5).

---

## Tests

### JS — `webapp/tests/`

- `TemplatePreviewPicker.test.js`: renders selector for all templates; clicking a
  chip swaps the detail panel; detail lists every group + all visible categories
  for the selected set; apply emits the selected code; apply disabled for the
  active set; language switch re-localizes.
- `TemplateSwitchSheet.test.js`: loads templates on open; apply calls
  `catalog.applyTemplate` and closes; **fetch fully mocked** (no real network —
  any `ECONNREFUSED:3000` in `npm test` is a bug).
- `CategorySheet.test.js` (update): search-row gear gone; bottom bar present in
  default/search/manage modes; left button calls `openTemplateSwitch`; right icon
  toggles manage; `initialManage` opens straight into manage; no inline template
  list remains.
- `ExpenseForm.test.js` (update/add): category-row gear opens the sheet with
  `initialManage`; `@click.stop` does not trigger normal open.
- `composable-oos-nudge.test.js` (update): threshold sets the persistent flag (no
  toast); reset behavior preserved.
- `App.test.js` (if present): banner shows when flag set; "Сменить набор" opens
  switch + clears flag; dismiss clears flag.

### Python — `tests/category_templates/`

- See §2.

---

## Verification

```
uv run inv pre        # ruff + ruff-format + pyrefly + hooks → "All checks passed!", 0 errors
uv run pytest         # N passed
cd webapp && npm test # all green, zero stderr ECONNREFUSED:3000
```

Manual smoke (local dev, `uv run inv dev`):

- Fresh DB → onboarding shows master-detail; switching set chips updates the
  full preview; choosing applies.
- Add expense → category card gear opens the sheet in Manage; row tap opens
  normal pick.
- Category sheet bottom bar: left opens switcher with preview, right toggles
  Manage; old search-row gear gone.
- Trigger nudge (3 out-of-set adds) → persistent banner appears, stays, "Сменить
  набор" opens the switcher, ✕ dismisses.

---

## Out of scope

- Changing the nudge threshold / window.
- Reworking Manage-mode category editing (rename/hide/move/add) itself — only its
  entry point moves.
- Persisting a per-set "last previewed" selection.

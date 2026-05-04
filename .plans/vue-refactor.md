# Vue 3 + Vite Refactor — webapp/

> **Status:** Planning. Not yet started.
>
> **Goal:** Replace the vanilla-JS PWA in `static/` with a maintainable
> Vue 3 + Pinia application in `webapp/`. The full rewrite is done
> locally and deployed once it reaches functional parity plus the three
> new tasks listed below. `static/` is deleted on completion.

## Context

The current PWA (`static/`) accumulates structural debt with each
change:

- `app.js` is an 800-line god file mixing state, DOM manipulation,
  event wiring and business logic.
- `catalog-add.js` builds modals via `innerHTML` strings with inline
  styles — CSS and logic are coupled.
- `style.css` has a global `input { appearance: none; width: 100% }`
  reset that forces type-specific override blocks every time a new
  control is introduced (checkbox, radio, currency search).
- No component boundaries: any code can reach any DOM node via `$()`.
- State updates require manually calling `rerenderCatalogControls()`
  which chains 6+ functions — the root cause of regressions.

The refactor is a full rewrite; incremental migration is explicitly
rejected (debug locally until parity is reached, then deploy).

In addition to parity, the following tasks land **inside the new
codebase** rather than as patches to the old one:

1. **Edit** — edit tag, category, event names/dates/fields in-place.
2. **Currency** — DB-backed currency dropdown with world-currency
   search, last-used default, background rate loading, manage mode.
3. **Bug: Delete on used events** — Hide/Delete buttons are shown on
   events that have associated expenses; Delete must be blocked.
4. **Bug: IndexedDB "Database is disconnecting"** — PWA stops queuing
   expenses until reloaded.

## Architecture decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Framework | Vue 3 (Composition API) | Component model enforced by tooling, not convention |
| State | Pinia | Replaces scattered module-level vars; computed getters eliminate manual rerenders |
| Build tool | Vite | Fast, already implied by vitest; native ES modules |
| PWA plugin | vite-plugin-pwa (Workbox) | Replaces hand-written `sw.js`; handles asset hashing |
| CSS | Scoped per component + `base.css` for true globals | Eliminates global reset / override accumulation |
| JS tests | vitest (existing) | No change; package.json moves to `webapp/` |

## Directory layout

```
webapp/                      # Vue 3 source — replaces static/ as source of truth
  index.html                 # Vite entry point
  vite.config.js             # outDir: '../_static', PWA plugin
  package.json               # moved from repo root; adds Vue, Vite, Pinia
  vitest.config.js           # moved from repo root
  public/
    icons/                   # from static/icons/
    manifest.json            # from static/manifest.json (vite-plugin-pwa takes over)
  src/
    main.js                  # createApp + Pinia + mount
    App.vue                  # header, ToastNotification, online/offline listener
    assets/
      base.css               # CSS vars, body, .btn, .card — NO global input reset
    api/
      expenses.js            # postExpense (near-identical to current)
      catalog.js             # fetchCatalog + all admin* functions
      currencies.js          # new: currency list, add, delete, rates
    stores/
      catalog.js             # Pinia: snapshot, getters, load/mutate actions
      queue.js               # Pinia: IndexedDB wrapper, enqueue, flush, count
    components/
      ExpenseForm.vue        # main form: amount, currency, group, category, event, tags, date
      TagPicker.vue          # reusable chip picker (v-model); replaces populateTagsList duplication
      ManageList.vue         # reusable active/inactive panel (props: kind, groupId?)
      QrScanner.vue          # camera + QR parse
      QueueModal.vue         # queue list + badge
      CurrencyPicker.vue     # new: searchable currency dropdown + manage mode
    modals/
      AddGroupModal.vue
      AddCategoryModal.vue
      AddEventModal.vue      # uses TagPicker for auto-tags
      AddTagModal.vue
      EditModal.vue          # new: edit any catalog entity (kind + item props)
  tests/                     # moved from tests/js/

_static/                     # gitignored — Vite build output; FastAPI auto-detects this
static/                      # deleted on completion of this plan
```

FastAPI's existing logic in `main.py` already handles this:
```python
_BUILT_STATIC = _PROJECT_ROOT / "_static"
STATIC_DIR = _BUILT_STATIC if _BUILT_STATIC.is_dir() else _PROJECT_ROOT / "static"
```
No Python changes required.

## Store design

### `stores/catalog.js`

```
state:    snapshot (raw catalog object), lastError

getters:  groups, categories(groupId), events(anchor), tags,
          inactiveGroups, inactiveCategories(groupId),
          inactiveEventsInWindow(anchor), inactiveTags

actions:  load(), replaceSnapshot(snap),
          reactivateGroup/Category/Event/Tag(id),
          deactivateGroup/Category/Event/Tag(id),
          deleteGroup/Category/Event/Tag(id),
          addGroup/Category/Event/Tag(body),
          patchGroup/Category/Event/Tag(id, body)  ← new for edit feature
```

Replaces the `catalog.js` module entirely. Components read getters;
`rerenderCatalogControls()` disappears because Vue reactivity propagates
snapshot changes to all consumers automatically.

### `stores/queue.js`

```
state:    count, isFlushing, lastFlushError

actions:  enqueue(expense), flush(), getAll(), remove(id)
```

IndexedDB access is encapsulated here. Components never touch IndexedDB
directly. `count` is reactive — the queue badge updates automatically.

**Bug #4 fix lives here:** do not cache `_db` across calls. Handle
`db.onversionchange` and `db.onclose` by setting `_db = null`, so the
next operation re-opens cleanly instead of throwing "Database is
disconnecting".

## Component design

### `TagPicker.vue`

```
props:   modelValue (number[])   — selected tag ids
emits:   update:modelValue
```

`v-model`-compatible chip picker. Reads `catalog.tags` from the store.
Used in `ExpenseForm` (expense tags) and `AddEventModal` (auto-tags).
Eliminates the `populateTagsList` / `readSelectedTagIds` duplication
between `app.js` and `catalog-add.js`.

### `ManageList.vue`

```
props:   kind ('group'|'category'|'event'|'tag')
         groupId? (number)   — required when kind === 'category'
```

Renders active rows (with Hide button) and inactive rows (with
Restore + Delete buttons). Delete is visible only when `item.removable
=== true`. Calls store actions directly; emits nothing (store update
triggers reactive re-render in parent).

Replaces the `renderManageList` / `appendRow` / `appendDeleteButton` /
`MANAGE_CONFIG` block (~170 lines in `app.js`). Used 4× in
`ExpenseForm`.

**Bug #3 fix:** the `removable` flag is computed server-side in
`build_catalog_snapshot`. Verify that events with associated expenses
get `removable: false`. If the bug is server-side (flag not set
correctly for events), fix it in `catalog_writer.py`; if it is a
UI-side regression (flag is correct but the button renders anyway),
the `ManageList` component enforces the guard consistently in one place.

### `EditModal.vue`

```
props:  kind ('group'|'category'|'event'|'tag')
        item (the catalog object to edit)
emits:  close
```

Renders the appropriate fields for the given kind. Calls
`catalog.patchGroup/Category/Event/Tag` on submit. One component
instead of four separate edit modals.

### `CurrencyPicker.vue`

```
props:   modelValue (string)   — selected currency code
emits:   update:modelValue
```

Two-level UI:
- **Primary list:** currencies saved in DB (from `api/currencies.js`).
  Default selection = last used (stored in localStorage).
- **Add from world list:** search input filters a bundled JSON of ~170
  ISO 4217 currencies. Selecting one calls `POST /api/currencies` to
  persist it.
- **Manage mode:** `v-if`-toggled list of saved currencies with Delete
  buttons.

Background rate loading (for app currency and currencies appearing in
the last 3 days of expenses) is a store action called on app init and
on a 30-minute timer, mirroring the existing `rate_prefetch_task`
pattern on the server.

## CSS strategy

`base.css` contains only genuinely global rules:
- CSS custom properties (`:root { --bg: ...; }`)
- `body`, `html` base styles
- Utility classes used across many components: `.btn`, `.btn-primary`,
  `.btn-secondary`, `.card`, `.toast`, `.modal-overlay`

**No** `input, select, textarea { appearance: none }` global reset.
Each component styles its own form controls via scoped CSS. The
checkbox/radio override accumulation problem is eliminated by
construction: `TagPicker.vue` hides its checkboxes in its own
`<style scoped>`, `ExpenseForm.vue` styles its text inputs in its
own `<style scoped>`, and they cannot interfere.

The 527-line `style.css` becomes ~80 lines of `base.css` plus styles
distributed into their owning components.

## Implementation steps

- [ ] **Step 1 — Scaffolding**
  - [ ] Create `webapp/` with `npm create vite@latest` (Vue 3 template)
  - [ ] Add Pinia, vite-plugin-pwa
  - [ ] Configure `vite.config.js`: `outDir: '../_static'`, Vite PWA plugin
        (precache all assets; replaces `static/sw.js`)
  - [ ] Move `package.json` and `vitest.config.js` from repo root to `webapp/`
  - [ ] Move `tests/js/` to `webapp/tests/`; verify `npm test` still passes
  - [ ] Update root `.gitignore`: add `_static/`
  - [ ] Update `Dockerfile` to multi-stage: Node stage builds `webapp/`,
        Python stage copies `_static/` (remove `COPY static/ static/`)
  - [ ] Copy `public/` assets: icons, update `manifest.json`

- [ ] **Step 2 — API layer**
  - [ ] `src/api/expenses.js` — port from `static/js/api.js` (`postExpense`)
  - [ ] `src/api/catalog.js` — port all `fetchCatalog` + `admin*` functions
  - [ ] `src/api/currencies.js` — stub (implement with currency feature)

- [ ] **Step 3 — Pinia stores**
  - [ ] `stores/catalog.js` — state, getters, load/mutate actions
  - [ ] `stores/queue.js` — IndexedDB wrapper with reconnect fix (bug #4)
  - [ ] Unit tests for both stores

- [ ] **Step 4 — Base CSS**
  - [ ] `src/assets/base.css`: CSS vars, body, `.btn*`, `.card`, `.toast`,
        `.modal-overlay`; no global form control reset

- [ ] **Step 5 — Reusable components**
  - [ ] `TagPicker.vue` (v-model, reads from catalog store)
  - [ ] `ManageList.vue` (kind + groupId props, calls store actions)
  - [ ] Unit tests for both

- [ ] **Step 6 — Main app shell**
  - [ ] `App.vue`: header, version badge, online/offline listener, toast
  - [ ] `QueueModal.vue`: queue list, copy-to-clipboard, version check
  - [ ] `QrScanner.vue`: camera + QR parse (port from `static/js/qr-scanner.js`)

- [ ] **Step 7 — Expense form**
  - [ ] `ExpenseForm.vue`: amount, group→category drill-down, event
        (date-anchored), tags, comment, date, Save button
  - [ ] Auto-attach event logic (port `applyAutoAttachEventForDate`)
  - [ ] Default group/category logic (port `applyDefaultGroupAndCategory`)
  - [ ] 4× `ManageList` embedded under each picker
  - [ ] `npm run build` → `_static/` → smoke-test in browser
        (basic expense entry must work end-to-end at this point)

- [ ] **Step 8 — Add modals**
  - [ ] `AddGroupModal.vue`
  - [ ] `AddCategoryModal.vue`
  - [ ] `AddEventModal.vue` (uses `TagPicker` for auto-tags)
  - [ ] `AddTagModal.vue`
  - [ ] Verify `dinary:catalog-add-result` toast equivalence

- [ ] **Step 9 — Edit feature (task #1)**
  - [ ] Add `adminPatch*` actions to catalog store
  - [ ] `EditModal.vue` (kind + item props; fields per kind)
  - [ ] Wire Edit buttons into `ManageList.vue` active-row rows
  - [ ] Unit tests for EditModal
  - [ ] Verify server-side PATCH endpoints exist for all four entity types

- [ ] **Step 10 — Currency feature (task #2)**
  - [ ] Backend: currency DB table, seed from `app_currency` env var,
        `GET /api/currencies`, `POST /api/currencies`, `DELETE /api/currencies/{code}`,
        rate endpoints for PWA-selected currencies
  - [ ] `src/api/currencies.js` (implement)
  - [ ] `CurrencyPicker.vue` (saved list, world-currency search, manage mode)
  - [ ] Add currency to `ExpenseForm` (replaces hardcoded `"RSD"`)
  - [ ] Background rate loading in queue store (on init + 30 min timer)
  - [ ] Last-used currency persisted in localStorage
  - [ ] Unit tests for CurrencyPicker

- [ ] **Step 11 — Bug fixes**
  - [ ] **Bug #3:** verify `removable: false` on events with expenses in
        `catalog_writer.py`; fix server-side if needed; confirm
        `ManageList` Delete button is gated on `item.removable`
  - [ ] **Bug #4:** already addressed in Step 3 (queue store reconnect);
        integration-test: close and reopen the IndexedDB connection,
        confirm enqueue succeeds without reload

- [ ] **Step 12 — Tests pass, cleanup**
  - [ ] All vitest tests green
  - [ ] `inv pre` passes (Python linting; JS is `npm run lint` if configured)
  - [ ] Delete `static/`
  - [ ] Remove `COPY static/ static/` from `Dockerfile`
  - [ ] Deploy

## Invariants

- `api/` contains only pure async functions (fetch wrappers). No Vue,
  no DOM, no store imports.
- Components read catalog state from `stores/catalog.js` getters only;
  they never hold a local copy of the snapshot.
- All IndexedDB access is inside `stores/queue.js`; no component
  imports `offline-queue.js` equivalents directly.
- Inline styles in JS are not permitted; visual rules live in `<style
  scoped>` blocks or `base.css`.
- Every new component and store action gets at least one unit test in
  the same step (not deferred to end).

# Vue 3 + Vite Refactor — webapp/

> **Status:** Done. Steps 1–16 are landed: vitest + pytest + `uv run
> inv pre` are green; the new currency / edit backend + frontend are
> in place; Bug #3 + Bug #4 are fixed; the legacy `static/` tree, its
> FastAPI fallback in `main.py`, and the rollback `COPY static/ static/`
> in the Dockerfile have been removed. Parity acceptance against a
> built `_static/` and the actual deploy remain operator-driven.
>
> **Goal (achieved):** Replace the vanilla-JS PWA that used to live in
> `static/` with a maintainable Vue 3 + Pinia application in `webapp/`.
> Functional parity (online **and** offline/PWA, see "Parity acceptance
> criteria" below) plus the four new tasks listed in "Context" all land
> in the rewrite; `static/` is gone.

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

In addition to parity, the following four tasks land **inside the new
codebase** rather than as patches to the old one:

1. **Edit** — edit tag, category, event names/dates/fields in-place.
2. **Currency** — DB-backed currency dropdown with world-currency
   search, last-used default, background rate loading, manage mode.
3. **Bug: Delete on used events** — Hide/Delete buttons are shown on
   events that have associated expenses; Delete must be blocked.
4. **Bug: IndexedDB "Database is disconnecting"** — PWA stops queuing
   expenses until reloaded. Treated as a **release blocker** because it
   silently breaks offline writes (see "Parity acceptance criteria").

## Architecture decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Framework | Vue 3 (Composition API) | Component model enforced by tooling, not convention |
| Routing | None (single-view app) | App today is a single screen; no `vue-router` until a second view is needed |
| State | Pinia | Replaces scattered module-level vars; computed getters eliminate manual rerenders |
| Build tool | Vite | Fast, already implied by vitest; native ES modules |
| PWA plugin | vite-plugin-pwa (Workbox) | Replaces hand-written `sw.js`; handles asset hashing |
| SW update mode | `registerType: 'autoUpdate'` + `skipWaiting` + `clientsClaim` | Target behavior for the Vue PWA; verified/adjusted in Step 4 after auditing current `sw.js` |
| Cutover queue migration | Drop old IndexedDB queue | Existing unflushed offline expenses from the vanilla PWA are acceptable to discard on cutover |
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
      currency.js            # Pinia: saved currencies, rates, last-used, refresher
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

_static/                     # gitignored — Vite build output; FastAPI mounts this at /
```

FastAPI mounts the built `_static/` at `/`:
```python
_STATIC_DIR = _PROJECT_ROOT / "_static"
if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
```
Python changes shipped with this plan:
- Currency feature (Step 13): new DB table, seed from `app_currency`,
  `GET/POST/DELETE /api/currencies`, server-side conversion at write
  time (no rate endpoints exposed to the PWA).
- Edit feature (Step 12): PATCH endpoints for group, category, event,
  and tag.
- Bug #3 (Step 15): fix in `build_catalog_snapshot` /
  `catalog_writer.py` so used events are no longer marked removable.
- Static-dir cleanup: `static/` and the `_static → static/` fallback
  are removed; FastAPI now mounts `_static/` only.

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

Cutover policy: the new Vue app is allowed to ignore or delete any
IndexedDB queue created by the old vanilla PWA. Existing unflushed
offline expenses are **not** migrated. The new queue starts fresh after
deployment; acceptance criteria apply to expenses queued by the new app
after first load.

### `stores/currency.js`

```
state:    saved (list from DB), rates (code → rate), lastUsed,
          isLoadingRates, lastRateError

getters:  byCode(code), savedCodes

actions:  load(), addCurrency(code), removeCurrency(code),
          setLastUsed(code), refreshRates(codes), startRefresher()
```

Owns currency-related concerns end to end: the saved list, the
last-used selection (mirrored to `localStorage`), and background rate
refresh (called on app init and on a 30-minute timer; mirrors the
server `rate_prefetch_task` pattern). Lives here, not in `queue.js`,
to preserve the invariant "all IndexedDB access is inside `queue.js`".
The client sends saved codes explicitly; the server owns any
"currencies used in the last 3 days of expenses" lookup.

Queued expenses store the submitted amount and currency code only. No
client-side exchange rate is frozen in the offline payload; the server
resolves rates when the queued expense is flushed. If the server cannot
resolve a rate at flush time, the item remains queued and
`lastFlushError` is surfaced.

## Component design

### `ExpenseForm.vue`

```
local state:  amount, currencyCode, groupId, categoryId, eventId,
              tagIds (number[]), comment, date

stores:       catalog (read-only getters), queue (enqueue),
              currency (lastUsed, saved list)

children:     CurrencyPicker, TagPicker, 4× ManageList (under each
              picker: groups, categories, events, tags)

behavior:     - Default group/category from last expense (port of
                `applyDefaultGroupAndCategory`).
              - Auto-attach event for selected date (port of
                `applyAutoAttachEventForDate`).
              - On Save: POST via `api/expenses.js` if online; else
                `queue.enqueue(expense)`. Reset form on success.
```

Top-level surface of the app. Holds no catalog snapshot of its own;
all catalog state comes from `stores/catalog.js` getters so the form
re-renders automatically after any add/edit/hide/restore/delete.

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

Two-level UI, all reads/writes go through `stores/currency.js`:
- **Primary list:** saved currencies from the store. Default
  selection = `currency.lastUsed` (mirrored to `localStorage`).
- **Add from world list:** search input filters a bundled JSON of ~170
  ISO 4217 currencies. Selecting one calls `currency.addCurrency(code)`
  which persists via `POST /api/currencies`.
- **Manage mode:** `v-if`-toggled list of saved currencies with Delete
  buttons (calls `currency.removeCurrency(code)`).

Background rate loading lives in `stores/currency.js` (`refreshRates`
+ `startRefresher`) and is invoked from `App.vue` on init and on a
30-minute timer. The component itself does no rate fetching.

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

## Parity acceptance criteria

"Parity" in this plan means **online + offline + PWA**. The rewrite is
not done until every checkbox below passes against a real build served
from `_static/` (not just `vite dev`). These are release blockers.

### Online (UI parity)

- [ ] Create expense end-to-end: amount, currency, group → category
      drill-down, event (date-anchored), tags, comment, date.
- [ ] Auto-attach event for date works (port of
      `applyAutoAttachEventForDate`).
- [ ] Default group/category logic works (port of
      `applyDefaultGroupAndCategory`).
- [ ] Catalog admin: add/hide/restore/delete for group, category, event,
      tag (4× `ManageList`).
- [ ] Edit (task #1) works for all four catalog kinds.
- [ ] Currency picker (task #2): saved list, world-currency search,
      manage mode, last-used default.
- [ ] QR scan path produces a valid expense draft.
- [ ] Toast notifications and online/offline banner behave as today.

### Offline (queue + reconnect)

- [ ] Airplane mode: submitting an expense enqueues it locally and the
      queue badge increments.
- [ ] Reload while offline: queued items survive and badge restores
      from IndexedDB.
- [ ] Cutover from old PWA: any pre-existing old IndexedDB queue can
      be discarded; a newly queued expense in the Vue app still
      survives reload and flushes correctly.
- [ ] Restore network: queue flushes automatically; failed items stay
      in queue with `lastFlushError` surfaced to the user.
- [ ] Repeated open/close of the IndexedDB connection does not produce
      "Database is disconnecting" (bug #4 regression test).
- [ ] Concurrent enqueue while flush is in progress does not lose items.
- [ ] Queue modal copy/export works for queued items while offline,
      so a user can manually recover queued payloads if sync fails.

### PWA

- [ ] Chrome/Edge fires `beforeinstallprompt` on first visit; "Install
      app" succeeds; the installed app launches and renders the same
      UI as the browser tab.
- [ ] First load caches the app shell; second load works offline with
      no network.
- [ ] Deploying a new build invalidates the SW cache: with
      `registerType: 'autoUpdate'` + `skipWaiting`, the next reload
      after deploy serves the new bundle (no stale-forever assets).
      Verified by bumping a version string and confirming the new
      value appears after one reload cycle.
- [ ] Handoff from the old `static/sw.js` is clean: with the old PWA
      previously loaded, deploy the Vue build, reload once, and verify
      the Workbox SW controls the page and serves the new app shell.
- [ ] Icons and `manifest.json` are served from `_static/` via
      `vite-plugin-pwa`, not from the deleted `static/`.

## Rollback strategy

The cutover and the cleanup landed together: `static/`, the FastAPI
`_static → static/` fallback in `main.py`, and the rollback
`COPY static/ static/` in the Dockerfile are all gone in the same
change set. There is therefore no in-tree rollback path.

If the deployed Vue PWA needs to be reverted, the rollback target is
the **last container image built before this change set** (which still
shipped `static/` and the fallback). Keep that image tagged for one
release cycle; redeploying it restores the vanilla-JS PWA without
needing any code-level revert.

## Runtime and tooling migration

The plan moved the JS toolchain root from the repo root into `webapp/`
and deleted `static/`. The rules below are the invariants that hold
in the implemented state:

- **Repo root** is no longer a Node project. There is no
  `package.json`, `node_modules/`, or `vitest.config.js` at the root.
- **All JS commands run from `webapp/`** (CI, `inv`, pre-commit,
  developer scripts). Use `npm --prefix webapp ...` or `cd webapp`.
- **Dockerfile single source of truth.** The multi-stage image builds
  `_static/` in the Node stage and copies it into the Python stage.
  There is no rollback `COPY static/ static/`; rollback is "redeploy
  the previous image".
- **FastAPI runtime guarantee.** `_static/` must exist in every run
  mode — there is no fallback:
  - Local dev: documented bootstrap in `README.md` (build once, or
    `vite dev` + proxy). `inv dev` triggers `inv build-static`
    automatically when `_static/index.html` is missing.
  - Tests: a chosen-and-encoded strategy (build-once fixture that
    fails loudly if `_static/` is absent — never silently skips).
  - Container: the Node stage produces `_static/` before the Python
    stage; image build fails loudly if `_static/` is empty.
- **Developer docs.** `README.md` and `AGENTS.md` are kept in sync
  with the implementation: `README.md` documents developer commands
  and runtime modes; `AGENTS.md` carries agent-facing rules.

## Implementation steps

The original Step 1 was a multi-day mixed bag and is split into three
focused steps (1–3). Currency lands as backend (Step 13) followed by
frontend (Step 14). Browser-pass checkpoints (`✓ checkpoint`) sit
between feature steps so regressions are caught before Step 16's full
acceptance walk. `static/` is **only** deleted in Step 16; no earlier
step does that work.

- [ ] **Step 1 — Vite scaffold**
  - [ ] Create `webapp/` with `npm create vite@latest` (Vue 3 template)
  - [ ] Add Pinia and `vite-plugin-pwa`
  - [ ] Configure `vite.config.js`: `outDir: '../_static'`,
        basic Vue plugin, and placeholder PWA plugin registration.
        Final Workbox strategy (`registerType: 'autoUpdate'`,
        `skipWaiting`, `clientsClaim`, runtime caching, SW handoff) is
        finalized in Step 4 after auditing `static/sw.js`
  - [ ] Copy `public/` assets: icons; port `manifest.json` (start_url,
        scope, display, icons)
  - [ ] Verify `npm run build` produces `_static/index.html`
        (final SW output is verified after Step 4)

- [ ] **Step 2 — JS toolchain migration**
  - [ ] Move `package.json`, `package-lock.json`, `vitest.config.js`
        from repo root to `webapp/`; delete root `node_modules/`
  - [ ] Move `tests/js/` to `webapp/tests/`. Existing tests target the
        old DOM/modals; expect to **rewrite** them against Vue
        components. Until rewritten, `xdescribe`/skip them so
        `npm test` is green
  - [ ] Track skipped legacy tests by feature owner. Each feature step
        below must rewrite or delete the tests it supersedes before
        that step is considered complete; Step 16 is only the final
        audit that no skips remain
  - [ ] Update root `.gitignore`: add `_static/` and
        `webapp/node_modules/`
  - [ ] Audit and update every JS-touching invocation: `tasks.py`,
        `.github/workflows/`, `.pre-commit-config.yaml`. Use
        `npm --prefix webapp ...` or `cd webapp`

- [x] **Step 3 — Container, dev bootstrap, runtime plumbing**
  - [x] Update `Dockerfile` to multi-stage: Node stage runs
        `npm --prefix webapp ci && npm --prefix webapp run build`;
        Python stage copies `_static/`. (The interim
        `COPY static/ static/` rollback fallback was kept through
        Steps 4–15 and removed together with `static/` itself in
        Step 16.)
  - [x] Document local dev bootstrap in `README.md`: chose option
        (a) build into `_static/` via `inv build-static` + run
        FastAPI; offline/PWA behavior must be tested against a real
        `_static/` build because the SW is not reliable in
        `vite dev`. (Option (b) `vite dev` with a `/api` proxy is
        documented as an HMR-only convenience.)
  - [x] pytest behavior when `_static/` is absent: the
        `built_static_dir` session fixture **fails loudly** with an
        actionable instruction. There is no `static/` fallback.
  - [x] PR CI smoke job runs the full Node→Python image build and
        fails loudly if `_static/` is empty.

- [ ] **Step 4 — Service worker audit and strategy**
  - [ ] Audit `static/sw.js` for behavior `vite-plugin-pwa` defaults
        do **not** cover: background sync, custom fetch handlers,
        version-check endpoint, queue interaction. Document each
        finding
  - [ ] For each behavior: either confirm Workbox provides it, or
        plan a `registerSW`/`workbox.strategies` extension in
        `vite.config.js`
  - [ ] Define the old-SW handoff: either same-scope Workbox takeover
        is sufficient and verified, or `main.js` explicitly
        unregisters stale `static/sw.js` registrations before
        registering the new SW
  - [ ] Decide the version-string mechanism (e.g. inject git SHA via
        `define`) so the PWA acceptance "new bundle after reload"
        criterion is verifiable

- [ ] **Step 5 — API layer**
  - [ ] `src/api/expenses.js` — port from `static/js/api.js`
        (`postExpense`)
  - [ ] `src/api/catalog.js` — port all `fetchCatalog` + `admin*`
        functions
  - [ ] `src/api/currencies.js` — stub (implemented in Step 13/14)

- [ ] **Step 6 — Pinia stores (catalog + queue)**
  - [ ] `stores/catalog.js` — state, getters, load/mutate actions
        (including `patchGroup/Category/Event/Tag` for the Edit
        feature)
  - [ ] `stores/queue.js` — IndexedDB wrapper with the bug #4
        reconnect fix (no cached `_db`; `onversionchange` /
        `onclose` handlers null it out)
  - [ ] Unit tests for both stores, including a regression test that
        simulates `onclose` and confirms the next `enqueue` succeeds

- [ ] **Step 7 — Base CSS**
  - [ ] `src/assets/base.css`: CSS vars, body, `.btn*`, `.card`,
        `.toast`, `.modal-overlay`; no global form-control reset

- [ ] **Step 8 — Reusable components**
  - [ ] `TagPicker.vue` (v-model, reads from catalog store)
  - [ ] `ManageList.vue` (kind + groupId props, calls store actions,
        Delete gated on `item.removable`)
  - [ ] Unit tests for both

- [ ] **Step 9 — Main app shell**
  - [ ] `App.vue`: header, version badge (uses Step 4 version
        string), online/offline listener, toast host
  - [ ] `QueueModal.vue`: queue list, copy-to-clipboard, version
        display/check. It shows the current build identifier from the
        injected version string and exposes a manual "reload app"
        action; automatic update behavior remains owned by the SW
        strategy from Step 4
  - [ ] `QrScanner.vue`: camera + QR parse (port from
        `static/js/qr-scanner.js`)

- [ ] **Step 10 — Expense form**
  - [ ] `ExpenseForm.vue`: amount, group→category drill-down, event
        (date-anchored), tags, comment, date, Save button
  - [ ] Auto-attach event logic (port
        `applyAutoAttachEventForDate`)
  - [ ] Default group/category logic (port
        `applyDefaultGroupAndCategory`)
  - [ ] 4× `ManageList` embedded under each picker
  - [ ] **✓ checkpoint:** `npm run build` → `_static/` → browser
        smoke test: create an expense online; verify queue path while
        offline (DevTools → Offline)

- [ ] **Step 11 — Add modals**
  - [ ] `AddGroupModal.vue`
  - [ ] `AddCategoryModal.vue`
  - [ ] `AddEventModal.vue` (uses `TagPicker` for auto-tags)
  - [ ] `AddTagModal.vue`
  - [ ] Verify success/error toasts appear when add operations
        succeed/fail (replacement for the old
        `dinary:catalog-add-result` event flow)
  - [ ] Rewrite or delete legacy add-modal tests that target old DOM
        strings/events (including today's staged
        `tests/js/event-add-modal.test.js`)
  - [ ] **✓ checkpoint:** browser pass — add one of each kind, verify
        all 4 `ManageList`s update reactively

- [ ] **Step 12 — Edit feature (task #1)**
  - [ ] Verify server-side PATCH endpoints exist for all four entity
        types; add any that don't
  - [ ] Use `patchGroup/Category/Event/Tag` actions on the catalog
        store
  - [ ] `EditModal.vue` (kind + item props; fields per kind)
  - [ ] Wire Edit buttons into `ManageList.vue` active rows
  - [ ] Unit tests for EditModal

- [ ] **Step 13 — Currency backend (task #2, server)**
  - [ ] Currency DB table; seed from `app_currency` env var
  - [ ] `GET /api/currencies`, `POST /api/currencies`,
        `DELETE /api/currencies/{code}`
  - [ ] Explicit lookup endpoint:
        `GET /api/currency-rates?codes=USD,EUR` returns rates for the
        requested codes only
  - [ ] Prefetch endpoint: `GET /api/currency-rate-prefetch` returns
        rates for saved currencies plus currencies used in the last 3
        days of expenses
  - [ ] Pytest coverage for the new endpoints

- [ ] **Step 14 — Currency frontend (task #2, client)**
  - [ ] `src/api/currencies.js` (implement against Step 13 endpoints)
  - [ ] `stores/currency.js` (saved list, rates, last-used,
        `refreshRates`, `startRefresher`)
  - [ ] `CurrencyPicker.vue` (saved list, world-currency search,
        manage mode)
  - [ ] Wire `CurrencyPicker` into `ExpenseForm` (replaces hardcoded
        `"RSD"`)
  - [ ] `App.vue` calls `currency.startRefresher()` on mount
  - [ ] Last-used currency persisted in `localStorage` via the store
  - [ ] Unit tests for `CurrencyPicker` and `stores/currency.js`
        (saved/manage flows + rate refresh)
  - [ ] **✓ checkpoint:** browser pass — add a new currency, switch
        last-used, verify it persists across reload

- [ ] **Step 15 — Bug #3 (Delete on used events)**
  - [ ] Verify `removable: false` on events with expenses in
        `build_catalog_snapshot` / `catalog_writer.py`. Fix
        server-side if the flag is wrong; the `ManageList` UI guard
        is already in place from Step 8
  - [ ] Add a backend test: event with at least one expense returns
        `removable: false`
  - (Bug #4 is implemented in Step 6 and re-checked in Step 16's
   acceptance walk — no separate step.)

- [x] **Step 16 — Acceptance, cleanup, deploy**
  - [x] All vitest tests green (no remaining skipped/`xdescribe`
        from Step 2)
  - [x] `uv run inv pre` and `uv run pytest` both green (per
        `AGENTS.md` "Verification before claiming done")
  - [x] Legacy JS tests are deleted along with the old `static/`
        test surface (rewrites of those flows live under
        `webapp/tests/`)
  - [ ] Walk the **Parity acceptance criteria** checklist end-to-end
        against a real `_static/` build (Online / Offline / PWA);
        every box ticked — *operator-driven, not done by the agent*
  - [x] Cleanup landed: `static/` removed, the `STATIC_DIR` fallback
        in `main.py` is gone, `COPY static/ static/` is gone from
        the Dockerfile
  - [ ] Confirm rollback target image (built before this change set,
        still ships `static/`) is published / available — *operator*
  - [ ] Deploy — *operator*

## Invariants

- `api/` contains only pure async functions (fetch wrappers). No Vue,
  no DOM, no store imports.
- Components read catalog state from `stores/catalog.js` getters only;
  they never hold a local copy of the snapshot.
- All IndexedDB access is inside `stores/queue.js`; no component
  imports `offline-queue.js` equivalents directly.
- No hand-built inline-style strings in JS (the `catalog-add.js`
  pattern) are permitted. Static visual rules live in `<style scoped>`
  blocks or `base.css`; Vue `:style` bindings are allowed only for
  genuinely dynamic values.
- Every new component and store action gets at least one unit test in
  the same step (not deferred to end).

## Accessibility scope

The rewrite should preserve current behavior and avoid new keyboard or
screen-reader regressions. Modal focus traps, ARIA labels, and keyboard
navigation improvements are desirable but not a separate feature epic
for this refactor; add them where they are local to the Vue component
being rewritten, and defer broad accessibility redesigns to a follow-up
plan.

Minimum checklist for rewritten Vue components:
- Every modal closes with Escape, traps focus while open, and restores
  focus to the opener after close.
- Every input/select/textarea has an associated label.
- Chip/list controls are keyboard reachable and expose selected state
  through native inputs or ARIA.

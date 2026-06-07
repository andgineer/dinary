# Handoff: Category search — “Not in your set” section (Variant B)

## Overview

The category picker (`CategorySheet.vue`) gets a smarter search.

**Today:** the search box filters only the **active** categories already
shown in the grouped list. If a category isn't in the user's current set, it
simply can't be found — the user assumes it doesn't exist.

**New behaviour:**

- **Empty query** → the grouped list is unchanged: the user's current set
  (active, non-hidden categories), grouped by group.
- **Search** now matches **every non-archived category** — including ones
  that are **not in the user's set** (inactive) and ones the user has
  **hidden**.
- Results are split: matches already **in the set** render on top (exactly
  like today); a fenced **“Not in your set · add with one tap”** section
  below holds the inactive + hidden matches.
- Selecting a result from that section **activates the category and selects
  it in one tap** — "find anything → switch it on and use it immediately".

**Design goal:** make the difference between *in the set* (select as usual)
and *not in the set / hidden* (tap activates + adds) obvious at a glance and
**non-threatening** — the user should feel “this just isn't on view, I can
switch it on with one tap”, never “I'm doing something weird or
irreversible”.

This is **Variant B** of three explored directions; it's the recommended one
(see “Why B” below).

## About the design file

`Category Search B.html` in this bundle is a **visual reference** — a
self-contained React prototype on a pan/zoom canvas. It is **not** production
code; do not port its JSX. Recreate it inside the Vue component per this
README. All tokens already live in `src/assets/base.css` — reuse, don't
invent.

To preview: open the file, click a phone artboard's label (or the ⤢ button)
to focus it; ←/→/Esc move between the three states.

## Fidelity

**High-fidelity.** Colours, type, spacing match Dinary v0.11 tokens. The
“addable” accent deliberately reuses the **blue** the sheet already uses for
`SUGGESTIONS` (`#7aabff`; border `rgba(91,141,239,0.4)`; tint
`rgba(91,141,239,0.12)`). Blue = informational/safe. The pink `--accent`
(`#e94560`) is **not** used here — it reads as action/danger.

## Where it lives in the codebase

```
dinary/webapp/src/
├── components/
│   └── CategorySheet.vue   ← MODIFIED — search spans the whole catalog;
│                             flat results split into in-set + addable
├── stores/
│   └── catalog.js          ← MODIFIED — add a searchable-catalog getter +
│                             reuse the existing reactivate() action
└── api/catalog.js          ← (reuse) adminReactivateCategory already exists
```

No view-level changes. Consumers (`ExpenseForm.vue`, `ExpenseEditSheet.vue`)
keep handling `@select="id"` unchanged — the sheet activates the category
**before** it emits `select` (see Interactions).

## The four catalog states → two row treatments

| State | Meaning | In grouped list? | In search? | Tap behaviour |
|---|---|---|---|---|
| **active** (in set) | `is_active !== false`, not hidden | ✅ | ✅ normal row | select as usual |
| **inactive** (not in set) | `is_active === false` | ❌ | ✅ **addable** | **activate → select** |
| **hidden** (sticky) | user-hidden flag | ❌ | ✅ **addable**, tagged `hidden` | **un-hide → select** |
| **archived** | out of the dictionary | ❌ | ❌ **excluded** | n/a (history only) |

So a search row is only ever **normal** or **“addable”** (inactive + hidden
share the addable treatment; hidden also gets a small `hidden` tag).

## Screens — the flow (3 states, 390-wide iOS frame, dark)

### 1 · Empty query — the set, grouped (unchanged)

Today's behaviour. `Select category` eyebrow, `Search…` placeholder, then the
grouped list: a `GROUP` eyebrow + a 2-column grid of `.cat-btn` chips per
group, active non-hidden categories only. **No “Not in your set” section
here** — that section is purely a search-results construct.

### 2 · Search results (“co”)

```
Select category
🔍 co|                                          ✕
────────────────────────────────────────────────
Food › Coffee
Transport › Commute
────────────────────────────────────────────────   ← 1px --border rule
◆ NOT IN YOUR SET · ADD WITH ONE TAP                ← section eyebrow (blue)
┌──────────────────────────────────────────────┐
│ Leisure › Concerts                       (＋) │   ← blue-tinted row, round + right
└──────────────────────────────────────────────┘
┌──────────────────────────────────────────────┐
│ Work › Coworking      ⊘ hidden           (＋) │   ← hidden gets a small tag
└──────────────────────────────────────────────┘
```

- **In-set results** (`Coffee`, `Commute`) render exactly like today's
  `.flat-item`: `group › name`, no chrome.
- **Section divider + eyebrow:** 1px `--border` top rule + `12px` top
  padding; eyebrow `0.62rem 700 uppercase letter-spacing .07em` in `#7aabff`
  with a 12px `Layers` glyph, then `· add with one tap` in `--muted-2`.
- **Addable row:** `background:rgba(91,141,239,0.12); border:1px solid
  rgba(91,141,239,0.4); border-radius:9px; padding:0.5rem;` label via the
  existing `.flat-item` text, slightly dimmed (`opacity:.92`).
- **Round `+`:** `24×24; border-radius:999px;
  background:rgba(91,141,239,0.20); color:#7aabff;` with a 14px `Plus` glyph.
- **Hidden tag:** `EyeOff` 10px + `hidden`, `--muted`, `0.64rem 600`, in a
  `rgba(148,163,184,0.10)` pill — only on hidden rows.

### 3 · After tap — activation feedback

On tap of an addable row:

1. The row's `+` morphs to a **filled blue check** (`background:#7aabff;
   color:#fff;` 13px `Check`); the row goes to the stronger blue
   (`border:#7aabff; background:rgba(91,141,239,0.20)`), full opacity — ~180ms.
2. A toast (the app's existing info `.toast`, on `--surface-2`) slides up:
   `✓ “Concerts” added to your set`.
3. Then the sheet closes and the category is selected in the form, exactly as
   a normal pick does today.

(In the prototype this is a static end-state; implement as the ~180ms
transition followed by the existing close/emit.)

## Interactions & behaviour

### Search scope — store getter

Replace the active-only search source with a getter that spans the catalog:

```js
// stores/catalog.js
function searchableCategories() {
  if (!snapshot.value) return [];
  return snapshot.value.categories
    .filter((c) => !c.is_archived)               // archived → history only
    .map((c) => {
      const group = snapshot.value.category_groups.find((g) => g.id === c.group_id);
      return {
        id: c.id,
        name: c.name,
        groupName: group?.name ?? "",
        inSet: c.is_active !== false && !c.is_hidden,
        hidden: !!c.is_hidden,
      };
    });
}
```

> Field names (`is_archived`, `is_hidden`) follow the Phase-2 columns in the
> task. If the snapshot exposes them under different keys, map them here — the
> component only needs `{ id, name, groupName, inSet, hidden }`.

### CategorySheet.vue — split the results

Build `flatResults` from `searchableCategories()` matched against the query
(name OR group name, as today), then split:

```js
const inSetResults   = computed(() => flatResults.value.filter((r) => r.inSet));
const addableResults = computed(() => flatResults.value.filter((r) => !r.inSet));
```

Render `inSetResults` first (existing `.flat-item` markup). Then, **only if
`addableResults.length`**, render the fenced section + the addable rows.
Keep the existing group/category ordering within each list. The empty-query
branch (`v-else`, grouped list + suggestions) is **untouched**.

### Selecting an addable result — activate then emit

The sheet activates the category **before** emitting `select`, so parents
need no change:

```js
async function select(item) {
  if (!item.inSet) {
    if (!isOnline.value) {                          // activation is online-only
      toast.show("Not available offline", "error");
      return;
    }
    try {
      await catalog.reactivate("category", item.id); // existing admin action;
                                                      // also clears the hidden flag
      toast.show(`“${item.name}” added to your set`, "info");
    } catch (e) {
      toast.show(e?.message || "Couldn't enable category", "error");
      return;                                         // keep the sheet open on failure
    }
  }
  emit("select", item.id);
  emit("close");
}
```

- `catalog.reactivate('category', id)` already exists and applies the
  returned snapshot, so the category immediately appears in the grouped list
  and the form's quick-picks next time.
- For a **hidden** (but still active) category, "activate" means clearing the
  sticky hidden flag. If the backend's reactivate already means
  "make visible & in-set", the same call covers it; otherwise add a
  `catalog.unhide(id)` analog and branch on `item.hidden`.

### Edge / empty states

- Query matches only in-set categories → render the top list only; **omit the
  section** entirely (no empty header).
- Query matches only addable categories → top list empty, section only.
- No matches at all → existing `No matches` row.

## Design tokens

All from `dinary/webapp/src/assets/base.css`. Do not invent.

| Token / value | Used for |
|---|---|
| `--surface` `#16213e` | sheet background |
| `--field` `rgba(255,255,255,0.04)` | search input, `.cat-btn`, row hover |
| `--border` `rgba(255,255,255,0.08)` | hairlines, section divider, chip border |
| `--border-strong` `rgba(255,255,255,0.12)` | drag handle |
| `--text` `#eee` | category name |
| `--muted` `#94a3b8` | group name, `hidden` tag, eyebrow |
| `--muted-2` `#64748b` | `›` separator, section sub-label |
| `--surface-2` `#0f3460` | info toast background |
| `--accent` `#e94560` | search caret only — **not** the addable accent |

Addable (blue) — already used by `SUGGESTIONS`; promote to tokens if you like
(`--addable`, `--addable-border`, `--addable-tint`); the prototype inlines:

| Use | Value |
|---|---|
| Addable text / icon | `#7aabff` |
| Addable border | `rgba(91,141,239,0.40)` |
| Addable tint bg | `rgba(91,141,239,0.12)` |
| Addable tint (active row / check) | `rgba(91,141,239,0.20)` |

Sizes: section eyebrow `0.62rem 700 uppercase ls .07em`; addable row radius
`9px`, padding `0.5rem`; round `+` `24×24`; `hidden` tag `0.64rem 600`.

## Copy (UI strings)

English, matching the existing component's English strings. Map to your i18n
layer if present.

| Key | String |
|---|---|
| sheet eyebrow | `Select category` |
| search placeholder | `Search…` |
| section eyebrow | `Not in your set` |
| section sub-label | `· add with one tap` |
| hidden tag | `hidden` |
| activation toast | `“{name}” added to your set` |
| offline error | `Not available offline` |
| activation error | `Couldn't enable category` |
| no matches | `No matches` |

## Assets

No new assets. Icons (all already in `lucide-vue-next`):

- `Layers` — “Not in your set” section glyph
- `Plus` — round add button
- `Check` — activation feedback (button + toast)
- `EyeOff` — `hidden` tag glyph

## Why B (vs the inline-badge and unified-list alternatives)

- **Matches the “set” mental model.** The boundary *mine / can be switched on*
  is expressed by structure — exactly the concept the user reasons about —
  not buried in repeated row text.
- **Quiet for the eye.** One section label instead of a badge on every row;
  the frequent case (all matches already in-set) looks identical to today, so
  nothing new competes for attention.
- **Non-threatening.** A blue section with `+` reads as “extras you can pull
  in”, never as an error or warning.
- **One tap, clear feedback.** `+` → check, toast, sheet closes with the
  category already selected — reversible-feeling, not scary.

## Checklist for the implementer

- [ ] Add `searchableCategories()` to `catalog.js` (exclude archived; expose
      `inSet` / `hidden`).
- [ ] In `CategorySheet.vue`, build `flatResults` from it; split into
      `inSetResults` + `addableResults`. Leave the empty-query grouped branch
      untouched.
- [ ] Render in-set rows on top (existing `.flat-item`), then the fenced
      “Not in your set” section with blue addable rows + round `+`, and a
      `hidden` tag on hidden rows.
- [ ] Omit the section when `addableResults` is empty; keep the existing
      no-matches row.
- [ ] On selecting an addable row: online-guard → `await
      catalog.reactivate('category', id)` (+ unhide) → toast → emit `select`
      + `close`. Keep the sheet open and toast the error on failure.
- [ ] Tests: search returns inactive + hidden categories; archived never
      appear; selecting an inactive result calls `reactivate` then emits
      `select`; the section is omitted when all matches are in-set; offline
      blocks activation.
```

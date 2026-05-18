# Backend — Classification Pipeline & API

All server-side work. Deployable independently before any PWA changes.

Sections in implementation order: ungroup → migration → LLM → rules model → task wiring
→ rules feed → catalog/expense API additions → new expense endpoints → POC → tests.

IMPORTANT:
1) Migration 0004 never riched production and can be modified
2) POC will be done on dev machine - we will run all the migrations and have the latest app version.

---

## 1. Ungroup — one expense per receipt item

Remove the `by_category` grouping from the classification pipeline and the split-merge
machinery from the correction controller. No DB migration needed.

### 1a. `src/dinary/background/classification/task.py`

- [ ] Delete the `by_category` defaultdict and the `for cat_id, cat_items` loop in
  `_persist_classification_results`
- [ ] Delete the `unresolved_items` list; handle unresolved items inline (call
  `update_receipt_item` with `ItemClassification(None, 1, None)`, no expense created)
- [ ] Replace grouped INSERT with a per-item INSERT:
  - `amount` and `amount_original` = `item.total_price`
  - `confidence_level` = item's individual `conf`
  - one `enqueue_for_logging` + one `update_receipt_item` + one `create_or_update_rule`
    (same `conf >= 2` guard) per item
- [ ] Drop `from collections import defaultdict` if no longer used

### 1b. `src/dinary/api/controllers/expense_corrections.py`

- [ ] Delete `_split_merge_expenses` entirely
- [ ] Delete `items_by_expense` defaultdict and its population loop in `correct_category_sync`
- [ ] Replace the `_split_merge_expenses(...)` call with a direct UPDATE per row returned
  by `_query_other_items`:
  `UPDATE expenses SET category_id = ?, confidence_level = 4 WHERE id = ?`
  (`_query_other_items` already returns `expense_id` — no change to that function)
- [ ] Drop `from collections import defaultdict` if no longer used

---

## 2. Migration 0004 — extend `classification_rules`

Both new columns are added to `0004_receipt_pipeline.sql` (not in production — amending
the existing migration is safe).

- [ ] Add `alternative_category_ids TEXT` (nullable JSON array) to `classification_rules`
- [ ] Add `tag_ids TEXT NOT NULL DEFAULT '[]'` (JSON array of tag IDs) to
  `classification_rules`
- [ ] Add `CREATE INDEX IF NOT EXISTS idx_cr_store_name ON classification_rules(store_id, name_normalized)`
  (needed for the `has_rule` subquery in §8b)
- [ ] In `0004_receipt_pipeline.rollback.sql`: add `ALTER TABLE … ADD COLUMN` stubs that
  reset both columns to NULL / empty array (SQLite cannot drop columns; acceptable since
  rollback only runs in dev)

---

## 3. LLM prompt — alternatives + tags in one pass

All changes in `src/dinary/adapters/llm_client.py`.

- [ ] Extend `_SYSTEM_PROMPT`:
  - when confidence < 4: include `"alternatives": [<category_id>, ...]` — 2-3 next-best
    category IDs ordered by likelihood; omit when confidence = 4
  - per item: include optional `"tags": [<tag_id>, ...]` — subset of provided tag IDs that
    clearly apply; omit when no tag clearly fits; do not guess
- [ ] `_build_user_message`: add `tags: dict[int, str]` parameter (id → name); append a
  `Tags:` block in the same format as `Categories:`; pass `{}` when no tags desired
- [ ] `ClassificationResult`: add `alternative_category_ids: list[int]`
  (default `field(default_factory=list)`) and `tag_ids: list[int]`
  (default `field(default_factory=list)`)
- [ ] `_parse_response`:
  - extract `entry.get("alternatives", [])`, filter to valid ints, cap at 3
  - extract `entry.get("tags", [])`, filter to ints present in the provided tag id set
- [ ] `ProviderPool.classify_receipt`: accept and forward `tags: dict[int, str]` parameter

---

## 4. Classification rules model — `src/dinary/db/classification_rules.py`

- [ ] Add to `RuleSpec`:
  - `alternative_category_ids: tuple[int, ...] = ()`
  - `tag_ids: tuple[int, ...] = ()`
- [ ] `create_or_update_rule`:
  - `source='llm'`: persist `json.dumps(list(spec.alternative_category_ids))` and
    `json.dumps(list(spec.tag_ids))` into the new columns
  - `source='user_correction'`: overwrite `tag_ids` with user-supplied value; leave
    `alternative_category_ids` unchanged (confidence becomes 4, alternatives not surfaced)

---

## 5. Task wiring — `src/dinary/background/classification/task.py`

- [ ] Add `_load_tags(conn: sqlite3.Connection) -> dict[int, str]` alongside
  `_load_categories`: `SELECT id, name FROM tags WHERE is_active = 1`
- [ ] In `_run_llm_pass`: call `_load_tags(conn)` and pass result to
  `pool.classify_receipt(..., tags=tags)`
- [ ] In `_persist_classification_results` (per-item loop from §1a): pass
  `alternative_category_ids` and `tag_ids` from `ClassificationResult` into `RuleSpec`
  when calling `create_or_update_rule`
- [ ] After creating each expense: insert `expense_tags` rows for each tag id in the rule
  (either from the just-written LLM result or from an existing rule hit)

---

## 6. Rules feed — `src/dinary/api/controllers/rules.py`

- [ ] Include `cr.alternative_category_ids` and `cr.tag_ids` in the `rule_stats` CTE SELECT
- [ ] Resolve alternative IDs to names joined to `categories WHERE is_active = 1`
  (silently drop any ID whose category is inactive); include in each row:
  `"alternative_categories": [{"id": <int>, "name": <str>}, ...]`  — empty list when certain
- [ ] Resolve tag IDs to names; include in each row:
  `"tags": [{"id": <int>, "name": <str>}, ...]`  — empty list when no tags

---

## 7. Catalog API additions — `src/dinary/api/controllers/catalog.py`

- [ ] Add `FrequentCategory(BaseModel)` with fields `id: int`, `name: str`
- [ ] Add `frequent_categories_sync(con, limit=5) -> list[FrequentCategory]`:
  ```sql
  SELECT e.category_id, c.name, COUNT(*) AS cnt
    FROM expenses e JOIN categories c ON c.id = e.category_id
   WHERE c.is_active = 1 AND e.receipt_id IS NULL
     AND e.datetime >= datetime('now', '-3 months')
   GROUP BY e.category_id ORDER BY cnt DESC LIMIT ?
  ```
  (`receipt_id IS NULL` is intentional: LLM-classified items are not user choices and
  would skew the quick-pick suggestions)
- [ ] Add `frequent_categories: list[FrequentCategory]` to `CatalogResponse`; populate
  in `build_catalog_snapshot`
- [ ] Confirm `GET /api/catalog` returns `tags: [{id, name}]` (active only) and
  `events: [{id, name, auto_tags: [name, ...]}]` (active only); add to
  `build_catalog_snapshot` if missing

---

## 8. Expense API additions

### 8a. Expense POST response — `src/dinary/api/controllers/expenses.py`

- [ ] Add `frequent_categories: list[FrequentCategory]` to `ExpenseResponse`
  (import from `dinary.api.controllers.catalog`)
- [ ] Populate in `create_expense_sync` by calling `frequent_categories_sync(con)`

### 8b. `GET /api/expenses/recent`

- [ ] Add route in `src/dinary/api/expenses.py`
- [ ] Add `list_recent_expenses_sync(con)` in `src/dinary/api/controllers/expenses.py`:
  - Join `expenses` → `categories`, `stores`, `expense_tags` → `tags`, `events`
  - Return newest-first, limit 30
  - Per row: `id`, `datetime`, `amount`, `currency_original`, `category_id`,
    `category_name`, `event_id`, `event_name`, `store_id`, `store_name`
    (`stores.chain_name`), `receipt_id`, `confidence_level`,
    `tags: [{id, name}]`, `has_rule: bool`
  - `has_rule`: true when a `classification_rules` row exists for the
    `(store_id, name_normalized)` of the linked `receipt_items` row; always false for
    expenses where `receipt_id IS NULL` (no receipt item to check). Requires an index on
    `classification_rules(store_id, name_normalized)` — add it in the §2 migration if missing.

### 8c. `PATCH /api/expenses/{id}`

- [ ] Add route in `src/dinary/api/expenses.py`
- [ ] Request model:
  ```python
  class ExpenseEditRequest(BaseModel):
      category_id: int | None = None
      tag_ids: list[int] = Field(default_factory=list)
      event_id: int | None = None
      clear_event: bool = False
      scope: CorrectionScope = CorrectionScope.single
      update_rule: bool = False
  ```
- [ ] Response model `ExpenseEditResponse(BaseModel)`: `id: int`, `category_id: int`,
  `category_name: str`, `tag_ids: list[int]`, `event_id: int | None`,
  `event_name: str | None` — returned by the PATCH endpoint
- [ ] `edit_expense_sync(expense_id, req, con)` in `src/dinary/api/controllers/expenses.py`:
  - If `category_id` provided: call `correct_category_sync(expense_id, req.category_id, req.scope, con)` directly
  - Replace `expense_tags` rows **for this expense only** with `req.tag_ids`; other
    expenses updated by the scope correction retain their existing tags
  - Update `event_id`: if `clear_event=True` set NULL; else if `event_id` is non-None
    update to that value; otherwise keep current
  - If `update_rule=True` and expense has a linked rule: call `create_or_update_rule`
    with `source='user_correction'`, new `category_id`, new `tag_ids`
  - Return `ExpenseEditResponse` populated from the updated row

---

## 9. POC — smoke-test with real receipts (`tasks/receipt.py`)

Run against `local/test_receipts.txt` before wiring alternatives/tags to the DB.

- [ ] Extend `classify-receipt` output:
  - `Alternatives` column: `alt1 / alt2` category names, empty when confidence = 4
  - `Tags` column: detected tag names, empty when none
- [ ] Run:
  ```
  inv classify-receipt $(cat local/test_receipts.txt | xargs -I{} echo --url {})
  ```
- [ ] Verify alternatives: items with confidence < 4 show at least one plausible alternative
- [ ] Verify tags: pet food / sports items get correct tags; unrelated grocery items get none
- [ ] If LLM over-tags or alternatives are nonsensical, iterate on the prompt in §3 before
  proceeding to §2 migration and storage

---

## 10. Tests

- [ ] `tests/api/test_receipt_pipeline_e2e.py` — receipt with N items → N separate expense
  rows; update any assertion that expected grouped counts
- [ ] `tests/tasks/test_receipt_drain.py`:
  - receipt with 3 items in 2 categories → 3 expenses, each `amount = item.total_price`
  - `expense_tags` rows inserted when rule carries tag ids; none when tags empty
  - existing circuit-breaker tests unchanged
- [ ] `tests/api/test_expenses.py` — category correction `scope=all` updates each item's
  individual expense directly (no new rows created)
- [ ] `tests/adapters/test_llm_client.py`:
  - `_parse_response` extracts `alternatives` (caps at 3, ignores non-int, handles missing)
  - `_parse_response` extracts `tags` (filters to provided id set, handles missing/non-int)
- [ ] `tests/db/test_classification_rules.py`:
  - `create_or_update_rule` persists `alternative_category_ids` and `tag_ids` for `llm`
  - user_correction leaves `alternative_category_ids` unchanged, overwrites `tag_ids`
- [ ] `tests/api/test_rules.py` — feed returns `alternative_categories` and `tags` with
  names; both empty for certain rules
- [ ] `tests/api/test_catalog.py` — response includes `frequent_categories` (top-5 by
  count, manual-only, last 3 months), `tags`, `events`
- [ ] `tests/api/test_expenses.py`:
  - POST response includes `frequent_categories`
  - `GET /api/expenses/recent` returns newest-first, includes tags + `has_rule`, limit 30
  - `PATCH /api/expenses/{id}` — category, tag, event updates tested independently;
    `update_rule=False` must NOT touch the rule; `update_rule=True` calls
    `create_or_update_rule` with correct args

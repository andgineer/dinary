# Plan: remove initial-import pipeline and personal-catalog migration

## Goal

The prod DB has been migrated to category templates. The one-off personal-catalog
migration code and the entire bulk-import-from-Google-Sheets pipeline are now dead
weight. Remove them cleanly, leaving only the runtime sheet-logging path.

Last commit before removal: `fefa8c4ad2`

---

## Inv tasks removed

| Task | File |
|---|---|
| `import-budget` | `tasks/imports/import_tasks.py` |
| `import-budget-all` | `tasks/imports/import_tasks.py` |
| `import-verify-bootstrap` | `tasks/imports/import_tasks.py` |
| `import-verify-bootstrap-all` | `tasks/imports/import_tasks.py` |
| `import-income` | `tasks/imports/import_tasks.py` |
| `import-income-all` | `tasks/imports/import_tasks.py` |
| `import-verify-income` | `tasks/imports/import_tasks.py` |
| `import-verify-income-all` | `tasks/imports/import_tasks.py` |
| `import-extract-income` | `tasks/imports/import_tasks.py` |
| `import-report-2d-3d` | `tasks/imports/import_tasks.py` |

`seed-categories` in `tasks/db.py` is **not** removed — it is a runtime task.

---

## Files deleted entirely

```
tasks/imports/                                       # all 10 .py + README
tasks/reports/verify_budget.py
tasks/reports/verify_income.py
tests/imports/                                       # all test files
tests/category_templates/test_migrate_personal_catalog.py
tests/reports/test_reports_verify_budget.py
tests/reports/test_reports_verify_income.py
tests/reports/test_report_2d_3d.py
tests/reports/test_report_2d_3d_render.py
tests/reports/test_report_2d_3d_resolve.py
tests/reports/test_report_2d_3d_aggregate.py
tests/reports/_report_2d_3d_helpers.py
specs/reference/income-import.md
```

---

## Files modified

### `src/dinary/db/category_seed.py`

- Delete `CATEGORY_MAP` (35 Russian name → factory code entries).
- Delete `GROUP_MAP` (11 Russian group name → factory code entries).
- Delete `migrate_personal_catalog`, `_backfill_category_codes`, `_backfill_group_codes`.
- Simplify `bootstrap_categories` to unconditionally call `seed_category_templates(con)`;
  remove the `has_null_code` branch entirely.
- Rewrite module docstring to describe the current (template-only) behaviour.

### `src/dinary/sheets/sheet_mapping.py`

- Delete `_TAG_RULES` (personal beneficiary/lifestyle envelope rules).
- Delete `_CATEGORY_ENVELOPES` (personal per-category envelope overrides).
- Delete `_default_template_rows`; in `ensure_default_map_tab` replace the template-body
  generation with an empty body — the created tab will contain only `MAP_TAB_HEADER`.
- Rename `"Расходы"` → `"sheet_category"` and `"Конверт"` → `"envelope"` in
  `MAP_TAB_HEADER`. The parser skips row 0 by index (`get_all_values()[1:]`) and never
  inspects header cell values, so these are purely cosmetic labels written only when
  creating a new empty tab. Update the module docstring comment accordingly.
- Update `tests/sheets/test_sheet_mapping_reload.py` and any other test fixtures that
  hard-code the Russian header row.

### `src/dinary/config.py`

Delete the entire import-sources subsystem:
`_IMPORT_SOURCES_PATH`, `_default_layout_for_year`, `IMPORT_SOURCES_DOC_HINT`,
`KNOWN_LAYOUT_KEYS`, `ImportSourceRow`, `_parse_import_sources_rows`,
`_import_sources_cache`, `_import_sources_cache_lock`, `read_import_sources`,
`get_import_source`.

None of these are referenced outside `tasks/imports/` (verified by grep; the
background sheet-logging code does not use import sources).

### `tasks/__init__.py`

- Remove `from .imports.import_tasks import (…)` block (10 names).
- Remove all 10 task names from `__all__`.

### `tasks/deploy.py`

- Remove `".deploy/import_sources.json"` entry from the deploy sync-files list.
- Remove the four printed `inv import-*` instructions from the post-deploy banner.

### `src/dinary/db/migrations/0006_category_templates.py`

- Rewrite docstring: remove "one-off applied once against the single personal dev DB"
  and the explanation of why `__transactional__ = False` was needed for a pre-existing
  DB. Keep only a factual description of the schema changes (table rebuilds, new columns,
  new tables, new index).
- Migration code itself is unchanged — it defines the correct target schema for all
  instances.

### `src/dinary/db/catalog.py`

- Remove the `seed_config._bump_catalog_version` caller reference from the comment
  near `set_catalog_version`.

### `tests/conftest.py` and `tests/test_config.py`

- Remove fixtures and tests that reference `ImportSourceRow`, `read_import_sources`,
  or `import_sources`.

### `tests/ledger/` fixtures

- Replace all Russian test-data strings with English equivalents: e.g.
  `"еда"` → `"food"`, `"кафе"` → `"cafe"`, `"собака"` → `"dog"`,
  `"релокация"` → `"relocation"`, `"отпуск-2026"` → `"vacation-2026"`,
  `"путешествия"` → `"travel"`.
- These strings are inserted directly into a blank test DB via SQL fixtures —
  they have no link to the real category catalog; the tests are internally
  self-consistent and the names are arbitrary.

---

## Specs

### `specs/reference/sheets.md`

- Remove the "Historical import" bullet from the "Two distinct roles" section.
  Collapse or remove the "Two distinct roles" header — there is now one role.
- Remove the final sentence of the "Idempotency — column J" section
  ("Bootstrap-imported rows carry `NULL` but are never enqueued…").

---

## Russian strings remaining after cleanup

All of these are data literals (CLAUDE.md: data literals stay in their original script).

| Location | String(s) | Reason to keep |
|---|---|---|
| `serbian_receipt_parser.py` | `"Укупно"`, `"Назив"`, `"Цена"`, `"Укупан"` | Serbian fiscal receipt keywords |

---

## Before merging

Tag the last commit that contains the import pipeline so the deleted code and specs remain
findable:

```
git tag archive/import-pipeline fefa8c4ad2
```

To restore any deleted file later: `git checkout archive/import-pipeline -- <path>`.

---

## Verification

After all changes:

1. `uv run inv pre` → "All checks passed!" + `0 errors` from pyrefly.
2. `uv run pytest` → all tests pass.
3. `grep -rn '[А-Яа-яЁё]' src/ tasks/ specs/ tests/` — only Serbian receipt
   keywords in `serbian_receipt_parser.py` produce hits.

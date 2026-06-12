# Plan: squash yoyo migrations 0001–0006 into a single baseline

## Goal

Collapse the six existing migrations (`0001_initial_schema` … `0006_category_templates`)
into **one** baseline migration that creates the final schema directly.

- **New users** (empty DB): one migration runs, builds the 0006-equivalent schema.
- **Personal dev DB + server DB** (already at 0006): must see *nothing to apply*.

## Why this is safe — how yoyo decides "applied"

yoyo records applied migrations in `_yoyo_migration` keyed by
`migration_hash = sha256(migration_id)`, where `migration_id` is the **filename
without extension** (`0001_initial_schema`, `0006_category_templates`, …).
`backend.to_apply()` runs every migration whose hash is **not** already in that
table. It never inspects file *contents* — only the id-hash.

Current `_yoyo_migration` on `data/dinary.db` (verified):
```
0001_initial_schema  0002_exchange_rates_source_target  0003_app_currencies
0004_receipt_pipeline  0005_income_logging  0006_category_templates
```

### Chosen strategy: **reuse the `0001_initial_schema` id for the baseline**

Because the baseline keeps the filename `0001_initial_schema`, its hash is already
present in every existing DB → yoyo treats it as applied and **skips it with zero
manual intervention**. No `yoyo mark` needed. The old `0002`–`0006` rows become
harmless orphans (no matching file; `to_apply` ignores them) and get cleaned up
for hygiene (Step 5).

> Rejected alternative: a fresh id like `0001_baseline`. Existing DBs would then
> lack its hash, yoyo would try to *run* it, and the `CREATE TABLE`s would fail on
> existing tables — requiring `yoyo mark` on every DB. More steps, more fragile.
> The only cost of reusing the id is the filename says "initial_schema" while
> containing the full collapsed schema — which is accurate for a fresh install.

## Steps

### 1. Generate the baseline SQL from a freshly migrated temp DB

Do **not** dump `data/dinary.db` — it is runtime-polluted (Litestream tables,
`catalog_version` already bumped past 1, possible drift). The authoritative final
schema is whatever migrations 0001–0006 produce on an empty file. Build that on a
throwaway DB, then dump it. This guarantees the dump is byte-for-byte what the six
migrations create — nothing more, nothing less.

```bash
cd /Users/andrei_sorokin2/projects/dinary
rm -f /tmp/squash_src.db
uv run python -c "from pathlib import Path; from dinary.db import db_migrations; \
  db_migrations.migrate_db(Path('/tmp/squash_src.db'))"
# Schema only (no rows). Strip yoyo bookkeeping, yoyo's lock table,
# the AUTOINCREMENT-managed sqlite_sequence (errors if re-created on a
# fresh DB), and any Litestream/runtime tables.
sqlite3 /tmp/squash_src.db '.schema' \
  | grep -vE '_yoyo_|yoyo_lock|sqlite_sequence|_litestream' \
  > /tmp/baseline_schema.sql
```

Hand-assemble `src/dinary/db/migrations/0001_initial_schema.sql`:
- Header comment: "Collapsed baseline — schema after migrations 0001–0006."
- Paste the **entire** cleaned DDL from `/tmp/baseline_schema.sql` — every
  `CREATE TABLE` / `INDEX` / `TRIGGER` it contains. Do not curate by hand; the
  migrations create ~24 tables (incl. `app_currencies`, the receipt-pipeline set
  `shop_chains`/`stores`/`receipts`/`receipt_items`/`classification_rules`/
  `receipt_classification_jobs`, `llmbroker_providers`/`llmbroker_call_log`, and
  `income_logging_jobs`), several of which are easy to drop if transcribing from a
  remembered list. The dump is the source of truth; copy all of it.
- **Do NOT** copy `app_metadata` data rows from dev (dev may have
  `catalog_version` > 1 and an `accounting_currency` anchor). Re-add only the
  seed that `0001` always carried — verified to be the single data seed across
  all of 0001–0006:
  ```sql
  INSERT INTO app_metadata (key, value) VALUES ('catalog_version', '1');
  ```

Notes:
- Keep it a plain `.sql`. The `__transactional__ = False` dance in 0006 only
  existed for *table rebuilds* (FK pragma is a no-op inside a txn). On a fresh DB
  the baseline is just `CREATE TABLE`s, so the project's default
  `BEGIN IMMEDIATE` wrapping is fine.
- Manually verify no `AUTOINCREMENT` / `UNIQUE` / FK was lost vs. the dump —
  Step 4's column/constraint tests are the backstop.

### 2. Write the rollback (optional but matches old 0001)

`src/dinary/db/migrations/0001_initial_schema.rollback.sql`: `DROP TABLE` every
table created above (children before parents to respect FKs). If we'd rather not
maintain it, delete the rollback file entirely — yoyo does not require one.

### 3. Delete the now-collapsed migration files

```bash
cd src/dinary/db/migrations
git rm 0002_*.sql 0003_*.sql 0004_*.sql 0005_*.sql 0006_category_templates.py
# (and any *.rollback.sql for 0002–0005)
```

### 4. Update tests — `tests/ledger/test_migrations.py`

- **Remove** `TestMigration0005FKSafety` entirely. It does
  `read_migrations(...)` then filters by `m.id.startswith("0005")`; with the file
  gone the filter is empty and the test is meaningless. The FK-rebuild concern it
  covered no longer exists (no rebuild in the baseline).
- **Remove** the now-dead `from yoyo.migrations import MigrationList` /
  `read_migrations` imports if nothing else uses them.
- **Keep** `TestInitialSchema`, `TestMigration0006CategoryTemplates`,
  `TestInitDbIntegration`, `TestAccountingCurrencyAnchor` — they assert
  *properties of the final schema* via `fresh_db` (full apply) and stay green
  against the baseline. Optionally rename `TestMigration0006CategoryTemplates`
  → `TestCategoryTemplateSchema` since "0006" no longer exists.
- **Add** a regression test for the squash invariant — applying the baseline to a
  DB that already carries the *old* `_yoyo_migration` rows is a no-op. Assert it
  directly: `to_apply` must be empty and the schema must be unchanged. (Inserting
  orphan rows alone proves nothing — the baseline hash recorded by the first
  `migrate_db` already forces a no-op; the test must assert the *mechanism*, not
  just that the second call doesn't raise.)
  ```python
  def test_existing_db_with_legacy_yoyo_rows_is_noop(tmp_path):
      """A DB already at 0006 (legacy 0002–0006 rows present as orphans, baseline
      hash among them) must apply zero migrations — the squash must not re-run
      schema DDL."""
      db = tmp_path / "legacy.db"
      db_migrations.migrate_db(db)            # builds baseline, records its hash
      con = sqlite3.connect(db)
      con.executescript(
          "INSERT INTO _yoyo_migration (migration_hash, migration_id, applied_at_utc) "
          "VALUES ('h2','0002_exchange_rates_source_target','2026-01-01'),"
          "       ('h6','0006_category_templates','2026-01-01');"
      )
      con.commit()
      before = con.execute(
          "SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name"
      ).fetchall()
      con.close()

      # The squash invariant: nothing left to apply despite the orphan rows.
      backend = db_migrations._backend_for(db)
      with backend:
          assert list(backend.to_apply(db_migrations._read_migrations())) == []

      db_migrations.migrate_db(db)            # must be a clean no-op
      con = sqlite3.connect(db)
      after = con.execute(
          "SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name"
      ).fetchall()
      con.close()
      assert before == after                  # no rebuild, no new/dropped tables
  ```

### 5. Clean orphan bookkeeping rows on real DBs (hygiene, not correctness)

The `0002`–`0006` rows have no matching file. They never trigger re-application,
but they clutter `_yoyo_migration` / `_yoyo_log`. Remove them on the dev DB and
the server DB (server path from `tasks/devtools/constants.py:_REMOTE_DB_PATH`,
host from `.deploy/.env`):

```bash
sqlite3 data/dinary.db \
  "DELETE FROM _yoyo_migration WHERE migration_id NOT IN ('0001_initial_schema');
   DELETE FROM _yoyo_log       WHERE migration_id NOT IN ('0001_initial_schema');"
```

Keep `0001_initial_schema` so yoyo continues to skip the baseline. Do the same
over SSH on the server DB (stop the service first; migrations expect no writers).
Take a backup before touching either DB (`data/backups/` already holds rolling
copies).

### 6. Update docs

`src/dinary/db/migrations/README.md`: note that `0001_initial_schema` is a
**collapsed baseline** (history before it lives in git), and that future
migrations continue from `0002` upward. While here, fix the stale backend path in
the README header: the `SQLiteBackend` lives in `src/dinary/db/db_migrations.py`,
not `src/dinary/services/db_migrations.py`.

## Done gate

1. `uv run inv pre` → "All checks passed!" + `0 errors`.
2. `uv run pytest tests/ledger/test_migrations.py` and full `uv run pytest` →
   all green.
3. Manual smoke: `rm -f /tmp/fresh.db && python -c "from pathlib import Path;
   from dinary.db import db_migrations; db_migrations.migrate_db(Path('/tmp/fresh.db'))"`
   then `sqlite3 /tmp/fresh.db '.tables'` shows the full 0006 schema, and a second
   `migrate_db` call is a no-op.
4. On `data/dinary.db`: `uv run inv migrate` reports nothing to apply.

## Risk notes

- Assumes **every** existing DB is at exactly 0006 (confirmed for dev; verify the
  server before cleanup). A DB stuck at an intermediate version would silently
  miss the gap because the baseline reuses the `0001` id — not a concern here, but
  do not reuse this recipe if partially-migrated DBs are in the wild.
- The baseline must be byte-for-byte schema-equivalent to 0001→0006. The dump
  approach guarantees this; do not hand-edit schema while transcribing.

# Plan: squash yoyo migrations 0001–0006 into a single baseline

## Goal

Collapse the six existing migrations (`0001_initial_schema` … `0006_category_templates`)
into **one** baseline migration that creates the final schema directly.

**Primary motivation: a single readable DDL of the current schema.** The point is
to be able to answer "what tables/columns are in the DB?" by reading one file,
not by opening the live DB in DBeaver or mentally replaying six migrations (with
0006's `_new` table rebuilds, that replay is infeasible by hand). The baseline
`0001_initial_schema.sql` becomes the canonical, human-readable schema reference.
Squash performance/hygiene is a secondary, minor benefit.

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
# Export schema only (no rows) by querying sqlite_master and excluding
# yoyo's own bookkeeping tables BY NAME. This selects whole CREATE
# statements, so multi-line definitions are never split.
#
# Do NOT use `.schema | grep` here: yoyo's _yoyo_log / _yoyo_version /
# _yoyo_migration are multi-line CREATEs, and a line-based grep deletes
# only the `CREATE TABLE ...` line, leaving orphan column fragments and
# stray `);` in the output — broken SQL. (The schema DOES have AUTOINCREMENT
# tables, so a `sqlite_sequence` row exists in sqlite_master — but it is
# excluded here both by `name NOT LIKE 'sqlite_%'` and by `sql IS NOT NULL`
# (its `sql` is NULL), so it never reaches the dump. No _litestream_* tables
# can appear here because this is a freshly migrated temp DB that Litestream
# never touched; that NOT LIKE filter is defensive only.)
sqlite3 /tmp/squash_src.db <<'SQL' > /tmp/baseline_schema.sql
.mode list
SELECT sql || ';'
FROM sqlite_master
WHERE sql IS NOT NULL
  AND name NOT LIKE 'sqlite_%'
  AND name NOT LIKE '\_yoyo%'      ESCAPE '\'
  AND name NOT LIKE 'yoyo\_%'      ESCAPE '\'
  AND name NOT LIKE '\_litestream%' ESCAPE '\'
ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END, name;
SQL
# Order by name (not rootpage): rootpage is a physical page number, so two DBs
# with an identical logical schema can dump in different row order, producing a
# spurious `diff` in Step 5a. The CASE rank still emits all tables before any
# index so the replay's CREATE INDEXes always find their table.

# Sanity-check the export round-trips before pasting it into the migration.
# Compare tables AND indexes (indexes are part of the schema and are the easiest
# objects to lose when hand-assembling) — not just table names:
rm -f /tmp/replay.db && sqlite3 /tmp/replay.db < /tmp/baseline_schema.sql
diff <(sqlite3 /tmp/squash_src.db \
        "SELECT type, name FROM sqlite_master WHERE type IN ('table','index') \
         AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%yoyo%' ORDER BY type, name") \
     <(sqlite3 /tmp/replay.db \
        "SELECT type, name FROM sqlite_master WHERE type IN ('table','index') \
         AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%yoyo%' ORDER BY type, name") \
  && sqlite3 /tmp/squash_src.db \
       "SELECT type, count(*) FROM sqlite_master WHERE type IN ('table','index') \
        AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%yoyo%' GROUP BY type" \
  && echo "baseline round-trips cleanly — record the table/index counts printed above as the expected totals"
```

Overwrite the existing `src/dinary/db/migrations/0001_initial_schema.sql`
(replace its entire contents — Step 2 deliberately does **not** `git rm` this
file, only its rollback):
- Header comment: "Collapsed baseline — schema after migrations 0001–0006."
- Paste the **entire** cleaned DDL from `/tmp/baseline_schema.sql` — every
  `CREATE TABLE` / `INDEX` it contains (the current schema has no triggers). Do not curate by hand; the
  migrations create the full table set (incl. `app_currencies`, the receipt-pipeline set
  `shop_chains`/`stores`/`receipts`/`receipt_items`/`classification_rules`/
  `receipt_classification_jobs`, `llmbroker_providers`/`llmbroker_call_log`, and
  `income_logging_jobs`), several of which are easy to drop if transcribing from a
  remembered list. The dump is the source of truth; copy all of it — do not work from
  the count printed in Step 1, that number is informational and Step 3's byte-for-byte
  diff is the real check.
- **Do NOT** copy `app_metadata` data rows from dev (dev may have
  `catalog_version` > 1 and an `accounting_currency` anchor). Re-add only the
  seed that `0001` always carried — verified to be the single data seed across
  all of 0001–0006:
  ```sql
  INSERT INTO app_metadata (key, value) VALUES ('catalog_version', '1');
  ```

Notes:
- **Paste DDL byte-for-byte — do not reflow.** `sqlite_master.sql` stores the
  `CREATE` text verbatim (indentation included), and Step 3 diffs that stored
  text against `/tmp/baseline_schema.sql`. Any re-indent, re-wrap, or
  keyword-case change to a `CREATE` statement makes Step 3's byte-for-byte diff
  fail even when the schema is logically identical. The *only* edits allowed on
  top of the pasted dump are the header comment (comments are not stored in
  `sqlite_master`, so they don't affect the diff) and the single
  `catalog_version` `INSERT` below (not a `CREATE`, so also invisible to the
  diff).
- This `INSERT` is the **only data row** in the baseline, and it is **not**
  covered by any diff in this plan: Step 1's round-trip check and Step 5a's
  live-drift diff both dump `sql FROM sqlite_master` (schema only, no rows).
  The seed is validated solely by the test suite (`get_catalog_version` /
  `TestAccountingCurrencyAnchor` via `fresh_db`) — a dropped or wrong seed shows
  up there, not in the diffs. Do not assume a clean Step 5a diff proves the seed.
- Keep it a plain `.sql`. The `__transactional__ = False` dance in 0006 only
  existed for *table rebuilds* (FK pragma is a no-op inside a txn). On a fresh DB
  the baseline is just `CREATE TABLE`s, so the project's default
  `BEGIN IMMEDIATE` wrapping is fine.
- Manually verify no `AUTOINCREMENT` / `UNIQUE` / FK was lost vs. the dump —
  Step 4's column/constraint tests are the backstop.

### 2. Delete the now-collapsed migration files (and rollbacks)

Delete `0002`–`0006` and every rollback, including `0001_initial_schema.rollback.sql`.
This runs **before** Step 3's verification on purpose: while the old files are
present, `migrate_db` re-runs them on top of the baseline and fails (see Step 3).
If Step 3 later reveals a bad baseline, undo the deletions with
`git restore --staged --worktree 0001_initial_schema.rollback.sql 0002_*.sql
0003_*.sql 0004_*.sql 0005_*.sql 0006_category_templates.py` — `git rm` only
stages the removal (no commit yet), so this brings the files back into both the
index and the working tree. **Do not commit anything until Step 3's diff is
empty** — until then the working tree holds an unverified baseline, and a commit
would put it in history.

**On rollbacks:** yoyo does not require one. The old `0001_initial_schema.rollback.sql`
dropped only the original table set; a baseline rollback would have to `DROP
TABLE` all 26 tables in FK-safe order (children before parents) and stay in sync
as the schema grows — easy to under-deliver and a source of drift. Drop it. Only
write a new one if a real rollback workflow needs it, in which case it must drop
every table created in Step 1.

```bash
cd src/dinary/db/migrations
# The *_*.sql globs already match the matching .rollback.sql files (they end in
# .sql too), so this also removes 0002–0005 rollbacks. 0006 is a .py with no SQL
# rollback. Name 0001's rollback explicitly — a 0001_* glob would also match the
# new baseline just written in Step 1.
git rm 0001_initial_schema.rollback.sql \
       0002_*.sql 0003_*.sql 0004_*.sql 0005_*.sql 0006_category_templates.py
```

### 3. Verify the *committed* migration reproduces the golden dump

Run this **after Step 2's deletion, not before.** With `0002`–`0006` still
present, `migrate_db` on a fresh DB applies the full baseline *and then* re-runs
the old migrations, which fail on the already-created objects (e.g. `0003` →
`table app_currencies already exists`). Once the old files are gone, `migrate_db`
applies **only** the baseline — which is exactly what this step verifies.

Both Step 1's round-trip and Step 5a's drift diff compare against
`/tmp/baseline_schema.sql` — the **generated** dump, never the hand-assembled
`0001_initial_schema.sql`. So a transcription slip (dropped table, mangled
constraint, an edit made after pasting the header) is invisible to them: the
suite's column/constraint asserts in Step 4 cover only a curated subset, and the
Done-gate `.tables` smoke only checks table *names*. The one thing that proves
"committed file == golden dump" is migrating a fresh DB **through the real
migration pipeline** (so FKs are ON, exactly as at runtime — unlike the bare
`sqlite3` CLI replay in Step 1) and diffing its full schema dump:

```bash
rm -f /tmp/committed.db
uv run python -c "from pathlib import Path; from dinary.db import db_migrations; \
  db_migrations.migrate_db(Path('/tmp/committed.db'))"
# Same name-filtered, schema-only, ORDER-BY-name query as Step 1:
sqlite3 /tmp/committed.db <<'SQL' > /tmp/committed_schema.sql
.mode list
SELECT sql || ';' FROM sqlite_master
WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'
  AND name NOT LIKE '\_yoyo%' ESCAPE '\' AND name NOT LIKE 'yoyo\_%' ESCAPE '\'
  AND name NOT LIKE '\_litestream%' ESCAPE '\'
ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END, name;
SQL
diff /tmp/baseline_schema.sql /tmp/committed_schema.sql \
  && echo "committed migration reproduces the golden dump byte-for-byte"
```

An empty diff is mandatory before proceeding to Step 4. Keep
`/tmp/baseline_schema.sql` around until the whole squash is done — it is the
golden reference for both this check and Step 5a.

> Run each generate/verify step in its **own** `uv run python -c` process (the
> commands above already do). `db_migrations._read_migrations()` is `@cache`d,
> so a single interpreter that read the migration list *before* Step 2's
> deletions would keep serving the stale, pre-squash list.

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
- **Do NOT add** a "legacy orphan rows → no-op" regression test. The obvious
  candidate (seed a fresh DB via `migrate_db`, insert fake `0002`/`0006`
  `_yoyo_migration` rows, assert `to_apply` is empty and the schema is unchanged)
  proves nothing the suite doesn't already cover: `to_apply` iterates over
  migration *files*, never over `_yoyo_migration` rows, so the injected orphan rows
  have **zero** effect — the no-op is driven entirely by the baseline hash recorded
  by the first `migrate_db`, making the test a louder duplicate of the existing
  `test_idempotent_reapply`. It also cannot reach the *interesting* risk
  (old-0006-schema vs new-baseline mismatch), because it builds its "legacy" DB by
  running the new baseline, so `before`/`after` are the same schema. Once the old
  migration files are deleted, that mismatch is simply **not automatable** — there
  is nothing left to build a genuine pre-squash DB from. The squash invariant is
  therefore covered where it actually can be:
  - **by construction** — the baseline is dumped from the old migrations' own
    output (Step 1) and proven byte-for-byte equal to them (Step 3);
  - **against real already-migrated DBs** — the live-drift diff (Step 5a).

### 5. Verify real DBs match the baseline, then clean orphan bookkeeping rows

#### 5a. Live-DB drift check (do this BEFORE touching anything)

The baseline's *correctness for new installs* is already covered by the full test
suite — every test builds its DB via `fresh_db` (full migrate), so a broken
baseline fails hundreds of tests immediately. What the suite does **not** see is
whether a real, already-migrated DB has *drifted* from the canonical migration
output (a manual hotfix applied straight to prod, an aborted migration, etc.).
"Nothing to apply" is guaranteed by the id-hash regardless of the actual schema,
so the only thing that catches such drift is a schema diff.

For the dev DB and the server DB, dump the live schema with the **same
name-filtered, `ORDER BY … name` query as Step 1** and diff it against
`/tmp/baseline_schema.sql`. Note the filter already excludes `_yoyo_*` /
`yoyo_lock` / `_litestream_*`, and the `SELECT` is schema-only (no data rows), so
runtime pollution and `catalog_version > 1` cannot show up — a clean run yields
an **empty diff**. **Any non-empty diff is a real table/index definition
difference = blocker** — investigate before proceeding.

```bash
sqlite3 data/dinary.db <<'SQL' > /tmp/live_schema.sql
.mode list
SELECT sql || ';' FROM sqlite_master
WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'
  AND name NOT LIKE '\_yoyo%' ESCAPE '\' AND name NOT LIKE 'yoyo\_%' ESCAPE '\'
  AND name NOT LIKE '\_litestream%' ESCAPE '\'
ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END, name;
SQL
diff /tmp/baseline_schema.sql /tmp/live_schema.sql && echo "dev DB matches baseline"
```

#### 5b. Clean orphan bookkeeping rows (hygiene, not correctness)

The `0002`–`0006` rows have no matching file. They never trigger re-application,
but they clutter `_yoyo_migration` / `_yoyo_log`. The `DELETE`s below keep only
`0001_initial_schema` so yoyo continues to skip the baseline. (`_yoyo_version` is
left untouched: it holds yoyo's bookkeeping-schema version, not per-migration
orphan rows, so there is nothing to clean there.)

**Dev DB** (`data/dinary.db`):

```bash
cp data/dinary.db "data/backups/dinary.pre-squash.$(date +%Y%m%d%H%M%S).db"
sqlite3 data/dinary.db \
  "DELETE FROM _yoyo_migration WHERE migration_id NOT IN ('0001_initial_schema');
   DELETE FROM _yoyo_log       WHERE migration_id NOT IN ('0001_initial_schema');"
```

**Server DB** — same `DELETE`s, but follow this exact order so no writer races the
edit and Litestream stays consistent (server path from
`tasks/devtools/constants.py:_REMOTE_DB_PATH`, host from `.deploy/.env`):

1. **Confirm the server DB is at exactly 0006 first** — run Step 5a's drift diff
   against the server DB and get an empty result. Do not run the `DELETE`s on a DB
   whose schema you have not just verified.
2. Stop the app service (migrations and this edit both expect no writers).
3. Stop the Litestream sidecar (so it is not mid-replication during the edit).
4. Back up the server DB file (copy alongside `data/backups/` convention).
5. Run the two `DELETE`s over SSH.
6. Restart Litestream, then the app service.

The `DELETE`s produce WAL writes Litestream will replicate once restarted — that is
expected and harmless.

### 6. Update docs

`src/dinary/db/migrations/README.md`:
- Note that `0001_initial_schema` is a **collapsed baseline** (history before it
  lives in git).
- **Number the next migration `0007`, not `0002`.** Re-using `0002`–`0006` is not
  a *functional* bug — yoyo keys on `sha256(migration_id)`, and a new
  `0002_<different_name>` hashes differently from the squashed-away
  `0002_exchange_rates_source_target`, so it would still apply. But any DB where
  Step 5b cleanup was skipped still carries orphan `0002`–`0006` bookkeeping rows;
  a new file numbered `0002` collides *numerically* with those orphans and makes
  `_yoyo_migration` actively misleading to read. Continuing from `0007` keeps the
  numbers monotonic and unambiguous.
- Refresh the **"Adding a new migration"** section: bump its example filename out
  of the `0002`–`0006` range (e.g. `0007_add_column.sql`) so it does not imply the
  squashed numbers are free to reuse.
- Fix the stale backend path in the README header: the `SQLiteBackend` lives in
  `src/dinary/db/db_migrations.py`, not `src/dinary/services/db_migrations.py`.

## Done gate

1. `uv run inv pre` → "All checks passed!" + `0 errors`.
2. `uv run pytest tests/ledger/test_migrations.py` and full `uv run pytest` →
   all green.
3. Step 3's diff is empty — the committed `0001_initial_schema.sql`, applied
   through `migrate_db`, reproduces `/tmp/baseline_schema.sql` byte-for-byte (full
   DDL, not just table names). A second `migrate_db` call on that fresh DB is a
   no-op.
4. Step 5a's live-drift diff is empty for **both** the dev DB and the server DB
   (any non-empty diff is a real schema difference and a blocker — see Step 5a).
5. On `data/dinary.db`: `uv run inv migrate` reports nothing to apply.

## Risk notes

- Assumes **every** existing DB is at exactly 0006 (confirmed for dev; verify the
  server before cleanup). A DB stuck at an intermediate version would silently
  miss the gap because the baseline reuses the `0001` id — not a concern here, but
  do not reuse this recipe if partially-migrated DBs are in the wild.
- The baseline must be byte-for-byte schema-equivalent to 0001→0006. The dump
  approach guarantees this; do not hand-edit schema while transcribing.
- **The baseline is non-reversible by design.** Step 2 drops the old
  `0001_initial_schema.rollback.sql` and writes no replacement, so there is no
  `yoyo rollback` path for the collapsed baseline. This is an accepted trade-off
  (a 26-table FK-ordered rollback would drift); rolling back the squash itself
  means restoring from git + the pre-squash DB backup, not `yoyo rollback`.

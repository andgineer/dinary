# Archive-format switch to `.gz` + `inv primary-rollback`

## Context

Two independent problems, one plan.

**Problem A — archive format.** Today's Yandex.Disk backups are
`.db.zst`. zstd gives a slightly better ratio than gzip but it is not
installed by default on macOS or Windows, so opening a snapshot on a
laptop forces the operator to `brew install zstd` first. For a ~1 MB
SQLite DB the compression difference is ~100 KB — not worth the
friction.

**Problem B — no rollback for accidental damage.** If an operator
corrupts data/dinary.db on VM1 (bad DELETE, broken migration, etc.),
there is currently no task to roll the live DB back to a pre-incident
state. `inv backup-cloud-restore` only covers "VM1 is gone" cold-DR
from the daily Yandex snapshot — 24 h resolution at best, and it
downloads through the laptop. What we actually need is "VM1 is alive,
pull the DB back to <timestamp> from VM2's Litestream replica".

VM2 already has continuous WAL-level delta data in
`/var/lib/litestream/dinary/`. Litestream's `restore -timestamp`
recovers the DB to any point within its retention window at WAL-frame
precision — second-level, not snapshot-tick-level. We reuse that
instead of building a parallel snapshot system. No increase to
`snapshot.retention` is needed: the daily Yandex upload already covers
"something is very wrong" via the freshness check in
`inv backup-cloud-status`, and Litestream's default 168 h window is
more than enough for "I just broke something — roll it back now".

## Design

### Problem A — `.db.zst` → `.db.gz`

Done as one atomic cut across every pipeline that names, writes,
reads, lists, or prunes snapshots.

**Write side:**

- Flip `BACKUP_FILENAME_SUFFIX = ".db.zst"` → `".db.gz"` in
  `src/dinary/tools/backup_snapshots.py:22`. All naming helpers
  (`parse_snapshot_lsjson`, `parse_snapshot_timestamp`,
  `pick_snapshot`, `backup_retention._make_pattern`) already read this
  constant, so there is no regex duplication elsewhere.
- When the VM2 daily backup shell script is wired up (referenced by
  `.plans/storage-migration.md:791` as `_build_backup_script`, builder
  not yet in-tree — see "gap" note), it pipes through `gzip -9 -c`
  rather than `zstd -19`.

**Read side (restore on laptop / VM1):**

- `tasks/backups.py:508` (`_download_and_verify`) switches from
  `zstd -q -d …` to `gzip -d -c …`.
- `assert_local_binaries(["rclone", "sqlite3", "zstd"])` at
  `tasks/backups.py:548` becomes `["rclone", "sqlite3"]` — `gzip` is
  preinstalled on macOS, Ubuntu, and modern Windows.

**Transition / back-compat for existing `.db.zst` files on Yandex:**

The Yandex folder already holds historical `.db.zst` snapshots.
Rather than rewrite them, the restore path accepts either suffix —
cheap and self-contained:

- Teach `_make_pattern` a `suffixes: tuple[str, ...]` variant; pass
  `(".db.gz", ".db.zst")` on read paths and the single `".db.gz"` on
  write.
- `_download_and_verify` picks the decompressor from the filename
  suffix (`.zst` → `zstd -q -d`, `.gz` → `gzip -d -c`).
- `assert_local_binaries` conditionally requires `zstd` only when the
  picked snapshot ends in `.zst`.
- GFS retention matches both suffixes during the overlap.
- Once the oldest retained `.db.zst` ages out (≤ 12 months by the
  yearly keeper), the dual-suffix code can be deleted in a follow-up.
  No manual migration, no re-uploading.

### Problem B — `inv primary-rollback`

Single new operator task in `tasks/backups.py`:

```
inv primary-rollback --at TIMESTAMP
```

`--at` is **required** — no default, no `latest`. The task also
**requires interactive confirmation** by typing `yes` at a prompt.
There is no `--yes` skip flag: this is a destructive operation on the
live production DB, and the cost of an accidental rollback outweighs
the convenience of one-liner automation. `restore_from_yadisk`'s
existing `--yes` flag at `tasks/backups.py:527` is NOT copied here.

**Flow (all over SSH from the operator laptop):**

1. **Resolve source**: VM2 is the Litestream replica target. No
   browsing of individual snapshot files is needed — Litestream's own
   WAL/snapshot tree is the source of truth.
2. **Upload a restore-side Litestream config to VM1** (or keep a
   pre-provisioned one at `/etc/litestream-restore.yml` installed by
   `inv setup-server`). It points at VM2 over SFTP as the replica
   source:
   ```yaml
   dbs:
     - path: /home/ubuntu/dinary/data/dinary.db
       replicas:
         - type: sftp
           host: dinary-replica:22
           user: ubuntu
           key-path: /home/ubuntu/.ssh/id_ed25519
           path: /var/lib/litestream/dinary
   ```
3. **Show the confirmation banner** to the operator (locally, before
   any SSH write):
   - Target timestamp (`--at` value, parsed to RFC3339).
   - VM1's current `expenses` row count (via `sqlite_row_count` at
     `src/dinary/tools/backup_snapshots.py:204`, over SSH).
   - The pre-rollback backup path that will be created.
   - Prompt: `Type 'yes' to continue: ` — abort on anything else.
4. **Quiesce VM1**: `ssh VM1 "sudo systemctl stop dinary litestream"`.
5. **Restore into a tmp file on VM1**:
   ```
   litestream restore \
       -config /etc/litestream-restore.yml \
       -timestamp <RFC3339> \
       -o /home/ubuntu/dinary/data/dinary.db.restore-tmp \
       /home/ubuntu/dinary/data/dinary.db
   ```
   Litestream materialises the DB by replaying WAL up to the requested
   timestamp against the closest prior snapshot — this is the point-
   in-time recovery primitive the task is built on.
6. **`PRAGMA integrity_check`** on the tmp file. On failure: delete
   tmp, restart services, abort with a clear error. VM1's live DB is
   untouched.
7. **Atomic swap on VM1**, preserving the escape hatch:
   - `mv data/dinary.db        data/dinary.db.before-rollback-<UTC>`
   - `mv data/dinary.db-wal    data/dinary.db-wal.before-rollback-<UTC>` (if present)
   - `mv data/dinary.db-shm    data/dinary.db-shm.before-rollback-<UTC>` (if present)
   - `mv data/dinary.db.restore-tmp data/dinary.db`
8. **Restart services**: `sudo systemctl start dinary litestream`.
   Litestream detects the new SQLite header and starts a fresh
   generation, which flows through to VM2 automatically.
9. **Resync replica** via `replica_resync()` at
   `tasks/backups.py:450` — forces VM2 to drop the now-obsolete
   generation and pick up the fresh one from VM1.

**Safety properties baked into the flow:**

- `data/dinary.db.before-rollback-<UTC>` is never auto-deleted. The
  operator can roll forward by running `inv primary-rollback --at
  <ts-of-the-before-rollback-file>` if they picked the wrong target.
- The pre-swap integrity check means a failed restore leaves the live
  DB bit-identical to how we found it.
- `--at` is mandatory — no way to accidentally rollback "to whatever
  is latest".
- No `--yes` flag — the typed-`yes` prompt is the irreducible safety
  gate.

### Out of scope

Explicitly NOT in this plan:

- No custom 10-minute snapshot timer on VM2. Litestream already
  streams WAL continuously; adding a tick-granular snapshot loop on
  top would duplicate what is already there at coarser grain.
- No GFS tiered retention for replica-side snapshots. The daily
  Yandex freshness check (`inv backup-cloud-status`) surfaces any
  "pipeline silently stopped" failure within a day, which is what GFS
  would otherwise be insuring against.
- No change to `snapshot.retention` in `.deploy/litestream.yml`. The
  default 168 h window is ample for "I just broke something and need
  to roll back now"; longer history lives in the daily Yandex archive.

## Files to create / modify

- **Modify** `src/dinary/tools/backup_snapshots.py` — flip
  `BACKUP_FILENAME_SUFFIX` to `".db.gz"`; adjust
  `parse_snapshot_timestamp` regex accordingly.
- **Modify** `src/dinary/tools/backup_retention.py` — `_make_pattern`
  accepts a suffix tuple; main picks the single write-suffix.
- **Modify** `tasks/backups.py`:
  - `_download_and_verify` at line 502 — pick decompressor by suffix.
  - `assert_local_binaries` call at line 548 — drop `"zstd"` from the
    unconditional list; require it only when the picked filename ends
    in `.zst`.
  - New `@task(name="primary-rollback")` per the flow above.
  - Factor a small helper for the "stop services → operate → start
    services" pattern so `primary-rollback` and any future restore
    task share the same try/finally structure (services always come
    back up, even on error paths).
- **Modify** the VM2 daily-backup shell script (or its future builder)
  to produce `.db.gz` via `gzip -9`.
- **Modify** existing backup tests
  (`tests/tasks/test_tasks_backups_*.py`) to reflect the suffix change
  and the dual-suffix read path.
- **Create** `tests/tasks/test_tasks_primary_rollback.py` — mocks both
  SSH targets (VM1 and VM2), pins the call order (quiesce → restore →
  integrity_check → swap → start → resync) and asserts:
  - missing `--at` → task rejects before any SSH.
  - wrong confirmation text → task aborts before any SSH.
  - integrity-check failure → tmp file removed, live DB untouched,
    services restarted.
  - happy path → `.before-rollback-<UTC>` file exists on VM1.
- **Modify** `docs/src/{en,ru}/operations.md` — replace `.db.zst` /
  `zstd` references with `.db.gz` / `gzip`; add a section describing
  `primary-rollback`, the required-`yes` gate, and the escape-hatch
  `.before-rollback-<UTC>` file.

## Verification

1. `inv pre` — lint, type-check, unit tests pass (including new
   `test_tasks_primary_rollback.py`).
2. **Format switch round-trip**: on a staging deployment, the next
   daily backup writes `dinary-<UTC>.db.gz` to Yandex. Download on
   macOS → double-click in Finder unpacks without installing anything.
   `file dinary-*.db` reports "SQLite 3.x database".
3. **Dual-suffix read**: leave at least one legacy `dinary-*.db.zst`
   alongside new `.db.gz` files on Yandex. `inv backup-cloud-status`
   lists both; `inv backup-cloud-restore --snapshot <legacy-date>`
   still restores the `.zst` end-to-end with `zstd` present, and
   fails with a clear error if `zstd` is missing.
4. **Rollback end-to-end on staging**: delete some rows in `expenses`
   at a recorded timestamp `T`. Run
   `inv primary-rollback --at <T - 1min>`. At the prompt:
   - Type `n` → the task must abort; no SSH writes to VM1.
   - Type `yes` → the task proceeds, row count on VM1 returns to
     pre-deletion, `data/dinary.db.before-rollback-<UTC>` exists,
     dinary service is active, Litestream on VM1 is active with a new
     generation, VM2 has resynced.
5. **Forward-rollback escape hatch**: immediately after step 4, run
   `inv primary-rollback --at <current-UTC>` (i.e. forward from the
   rolled-back state). Confirm the operator can still recover the
   deletion they just rolled back, using Litestream's post-rollback
   generation as the source.
6. **Missing `--at` fails fast**: `inv primary-rollback` (no flag)
   exits non-zero with a clear message before any SSH.
7. **Integrity-check failure path**: force a corrupt tmp by pointing
   the task at a non-existent timestamp outside Litestream's retention
   window → the task aborts, VM1's live DB is byte-identical to
   before, both services come back up.

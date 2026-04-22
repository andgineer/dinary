# Database, Migrations, Backups, and Replication

This page describes how `dinary` stores data, how schema migrations
run, how the local file-based backup story works, and how to set up
hot off-site replication with Litestream.

## Database file

The server stores everything in a single SQLite database:

- `data/dinary.db` — the live database (categories, groups, tags,
  stores, mappings, expenses, income, and sync-job metadata)
- `data/dinary.db-wal` and `data/dinary.db-shm` — SQLite WAL-mode
  sidecars that hold in-flight writes before they are checkpointed
  back into `dinary.db`

All three files belong together. Do not copy just `dinary.db`: the
WAL sidecar can legitimately contain committed transactions that
have not yet been checkpointed. Any backup flow that does not go
through SQLite's `.backup` API must stop the `dinary` service first.

## Migrations

Schema changes are managed with `yoyo` migrations living in
`src/dinary/migrations/`.

### When migrations run automatically

- On application startup, the SQLite DB is opened and `yoyo` applies
  every outstanding migration against the live file before any
  request is served.
- During `inv deploy`, the deploy script calls `inv migrate` before
  restarting the service so a broken migration fails the deploy
  instead of a running server.

For a fresh installation no manual migration step is required.

### Manual migration

If you want to apply migrations explicitly on the server:

```bash
inv migrate
```

This is safe to re-run: `yoyo` tracks applied migrations in the DB
and only applies new ones.

## Integrity check

SQLite ships two pragmas for post-migration / post-restore sanity
checks:

- `PRAGMA integrity_check` walks every btree page and reports
  structural damage (torn pages, index/table mismatches, orphan
  freelist entries).
- `PRAGMA foreign_key_check` lists every row that violates a
  declared FK.

Both are read-only and cheap. `dinary` wraps them in:

```bash
inv verify-db            # local data/dinary.db
inv verify-db --remote   # snapshot of prod DB, checked over SSH
```

`--remote` first takes a `sqlite3 .backup` snapshot into `/tmp` on
the server and checks that, so you never read a live file whose WAL
is mid-checkpoint.

## Backups

SQLite is a single-file database, so every backup ultimately boils
down to "produce a transactionally consistent copy of
`data/dinary.db`". `dinary` offers two mechanisms depending on how
fresh and how tolerant of latency the copy needs to be.

### Cold backup: `inv backup`

```bash
inv backup                        # copy to ./backups/<timestamp>/
inv backup --dest=./my-backups
```

This SSHes to the server, runs `sqlite3 .backup` on the live DB to
produce a consistent snapshot in `/tmp`, streams the bytes locally,
and writes `./backups/<timestamp>/dinary.db`. The snapshot is
atomic even while the service is writing — SQLite's online backup
API copies pages under a brief lock and retries torn reads.

Use `inv backup` before:

- `inv deploy` (the deploy wrapper also runs its own pre-deploy
  snapshot automatically)
- any manual schema migration
- any ad-hoc DB surgery

### Hot replica: Litestream

`inv backup` is pull-based and fires on demand. For continuous
streaming replication — "my VM 1 got terminated; how much data did
I just lose" — `dinary` supports Litestream (v0.5.x) as a sidecar
that ships LTX segments to an SFTP target continuously.

#### Prerequisites

- A second host you control (Oracle Cloud Free Tier's 4-vCPU Arm VM
  is the reference target) with SSH reachable from VM 1.
- The replica host has a directory you can write to, e.g.
  `/home/ubuntu/replicas/dinary/`.
- VM 1's `~/.ssh/id_ed25519.pub` is in the replica host's
  `~/.ssh/authorized_keys` (one-time `ssh-copy-id`).

#### One-time bootstrap

1. Copy the example config locally and fill in the SFTP target:

   ```bash
   cp .deploy.example/litestream.yml .deploy/litestream.yml
   # edit .deploy/litestream.yml — set host, user, path, key-path
   ```

2. Install the Litestream sidecar on VM 1:

   ```bash
   inv litestream-setup
   ```

   This installs the Litestream binary, uploads the config to
   `/etc/litestream.yml`, creates a `litestream.service` systemd
   unit, and starts it. The sidecar is idempotent — re-running
   `inv litestream-setup` upgrades the binary and reloads the
   config.

3. Confirm replication is healthy:

   ```bash
   inv litestream-status
   ```

   A healthy sidecar shows an active systemd unit and the managed
   DB path listed by `litestream databases`. An empty output means
   the sidecar either never reached the SFTP host or is still
   producing its first snapshot (first one lands within seconds of
   the first DB write after the sidecar starts).

`inv setup` does not start Litestream automatically even when
`.deploy/litestream.yml` is present, because the sidecar needs an
already-reachable SFTP host with VM 1's public key in its
`authorized_keys` — a cross-host trust relationship we cannot set
up from the deploy workstation. Run `inv litestream-setup`
manually once that prerequisite is in place.

#### What the sidecar does

Litestream v0.5 is a passive replicator: it opens the DB
read-only, tails LTX segments out of SQLite's WAL, compacts them
into level files, and ships them to the SFTP target. The app never
talks to it. If the sidecar crashes, the app keeps writing into the
WAL normally — you just stop accumulating replica state until the
sidecar restarts. There is no back-pressure; SQLite's checkpoint
loop is unaffected.

Default settings in the example config: a full snapshot every hour
and 7 days of LTX history (top-level `snapshot: { interval: 1h,
retention: 168h }`). That bounds "how far back can I rewind the DB"
to a week and bounds LTX replay on restore to a one-hour window.
These fields are global in v0.5 — they are NOT valid inside a
per-replica block and Litestream refuses to start if they are
placed there.

#### Restoring from a replica

On any host with Litestream installed and SSH access to the replica
target:

```bash
litestream restore -config /path/to/litestream.yml /path/to/output/dinary.db
```

Litestream reads the most recent snapshot in the replica, replays
LTX forward to the latest committed transaction, and writes a fresh
`dinary.db`. The restored DB is transactionally consistent — no
`PRAGMA integrity_check` is required, but running it does not hurt.

## Restore from cold backup

1. Stop the running service: `inv stop`.
2. Replace `data/dinary.db` with the backed-up file.
3. Remove any stale WAL sidecars: `rm -f data/dinary.db-wal data/dinary.db-shm`.
4. Start the service: `inv start`.
5. Optionally `inv verify-db` to check integrity, then `inv migrate`
   to confirm the restored DB is at the expected schema version.

## Practical guidance

- The pair `inv backup` + Litestream cover the "oops I nuked the DB"
  and "oops I lost VM 1" failure modes respectively. Use both.
- Treat `data/dinary.db` as the source of truth. Do not edit it
  while the service is running, and never hand-edit the
  `-wal`/`-shm` sidecars at all.
- The laptop-side DuckDB-over-SQLite analytics workflow (Phase 5 of
  `.plans/storage-migration.md`) consumes the same Litestream
  replica — no second backup pipeline is needed for analytics.

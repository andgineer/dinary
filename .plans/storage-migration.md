# Storage Migration: DuckDB → SQLite (OLTP) + Litestream (hot replication) + DuckDB (OLAP on laptop)

> **Scope.** This plan documents *why* and *how* we move the server's ledger
> storage off DuckDB onto SQLite, what replication topology keeps data durable
> across free-tier infrastructure, and how analytics keeps its DuckDB-shaped
> OLAP layer on the laptop side. Everything else (PWA, FastAPI surface,
> sheet logging, category tree, etc.) is unchanged.

## 1. Context

We currently run a single DuckDB file (`data/dinary.duckdb`) as the server's
ledger store, opened read-write by the FastAPI process on a 1 GB Oracle Cloud
Free Tier VM. That decision was made when the project was expected to grow into
a DuckDB-native OLAP workload; it has not — analytics has always happened off
the server.

The architectural friction we hit in practice:

1. **DuckDB 1.x holds an exclusive file lock** once a process opens the DB
   read-write. A second process — even one opened `read_only=True` — cannot
   attach, which breaks any out-of-band inspection (`inv sql`, DBeaver, ad-hoc
   scripts) while the server runs. Workarounds today are a `/tmp` snapshot
   `cp` (used by `inv report-*` and `inv sql --remote`) or stopping the
   server first. DuckDB docs are explicit that this is a design choice, not a
   bug, and will not change in 2.x; Turso's DuckLake is the official answer
   for multi-process read-write, which is much too heavy for our profile.
2. **DuckDB has no incremental replication.** There is no WAL-streaming story,
   no logical replication, no change-data-capture. `EXPORT DATABASE` is a full
   dump every time. The best primitive available is `rsync` on the `.duckdb`
   file, which is not a true hot backup and carries the rewrite-on-checkpoint
   caveat.
3. **RAM is the real scaling wall.** The 1 GB VM already dedicates ~300–400 MB
   to uvicorn + FastAPI + Python + the live DuckDB connection. DuckDB's default
   `memory_limit` is 80 % of system RAM, so any non-trivial analytical query
   in the server process is one OOM-kill away from taking uvicorn down and
   losing in-progress writes. Analytics on the server is structurally unsafe.
4. **Analytics is always going to be off-box.** Interactive, AI-driven
   dashboards need a full OLAP engine with enough RAM to breathe. That work
   belongs on the laptop — or, later, a dedicated analytics VM — reading a
   replica, not the live ledger.

None of the above are DuckDB's fault. They are the result of using a
column-store analytical engine in an OLTP role on a constrained VM, with
analytics we then want to run somewhere else anyway.

## 2. Hard constraints from the user

1. **Only free infrastructure.** No S3, no R2, no Turso Cloud, no Fly.io paid
   tiers. The project can spin up one or more Oracle Cloud Free Tier VMs but
   that is the ceiling for always-on servers.
2. **Network topology is Tailscale.** Every node (server VMs, laptop) is a
   Tailscale peer with a stable MagicDNS name. NAT traversal is a non-issue.
   Direction of any replication connection (who initiates, who listens) is
   freely chosen; Tailscale makes both ends mutually reachable.
3. **Laptop is the primary analytics client**. It pulls the SQLite replica
   from VM 2 and runs DuckDB-over-SQLite locally. Laptop is online most of
   the day but may be offline for stretches of up to a couple of weeks
   (travel), so it cannot be the primary home for any durability-critical
   job. Defense-in-depth cloud snapshots (OneDrive / Google Drive / Yandex
   Disk) therefore run on VM 2, not the laptop — see §10 Phase 3.5.
4. **The server must always accept writes**, even if every other node in the
   mesh is unreachable. The ledger is the source of truth for real-time
   expense entries from the PWA, and SaaS primaries (Turso Cloud) that route
   writes through the network are a disqualifying inversion of that
   invariant.
5. **Analytics stays on DuckDB.** The previous architecture decision
   (`.plans/sql-vs-ibis-comparison.md`, `.plans/architecture.md`) concluded
   DuckDB is the right OLAP engine for our workload — full PostgreSQL-style
   SQL, zero-copy integration with pandas/polars/Arrow, native ability to
   `ATTACH` SQLite files, and the richest LLM-SQL story. That conclusion
   is unchanged; this migration moves DuckDB from the server to the laptop,
   where its constraints (memory, single-writer) don't bite.

## 3. Requirements derived from constraints

The new topology must satisfy, in priority order:

1. **Writes never block on the network.** The FastAPI process on VM 1 must be
   able to record an expense even if every other node is offline.
2. **Off-site durability in the face of Oracle reclaiming a free VM.** Oracle
   Free Tier instances can be torn down if idle; the replica must live on
   a different VM or on the laptop — not only on the primary.
3. **Laptop gets a readable copy that is up to date within seconds when
   online, and eventually consistent when the laptop comes back online after
   a multi-week outage.**
4. **DuckDB on the laptop must be able to query the replica directly**, with
   no ETL step and no conversion layer.
5. **Minimum RAM on the server.** Every megabyte the storage stack saves is a
   megabyte the analytics-replacement story does not need to fight for later.
6. **No vendor lock-in.** If any component of this plan stops being
   maintained, we should be able to eject to plain SQLite with zero data
   loss and a bounded amount of code churn.
7. **Tests keep passing.** The 487 existing pytests are mostly schema-shaped
   and must survive the engine swap — not through test rewrites, but because
   SQL stays compatible.

## 4. Alternatives considered

### 4.1 Ruled out in earlier investigation

- **Stay on DuckDB.** Fails (1–3) in section 1. `rsync` of `.duckdb` is not a
  hot backup. `/api/_debug/sql` through the server process sidesteps the lock
  but keeps the OOM risk and does not fix backup.
- **BoltDB / LMDB / other KV stores.** The codebase is SQL-first (schema,
  migrations, 487 tests written as SQL) and porting to a KV API means
  re-implementing secondary indexes, joins, and typed columns by hand. The
  ergonomic cliff is not justified by any gain.
- **RocksDB / LevelDB.** Same KV problem plus LSM-tree write amplification
  that is wrong for a 1 GB VM.
- **Postgres (server).** 200–400 MB base RAM is roughly half of the VM's
  budget. Works, but ejects us from the "embedded DB on a tiny VM" niche
  that motivated using a file-based store in the first place.
- **Turso managed.** Writes route through Turso Cloud, violating
  requirement (1): if Turso is unreachable, the server cannot record an
  expense. Acceptable for stateless edge apps, not for a ledger.

### 4.2 Serious candidates evaluated here

Three options remain that all store data on VMs we control:

- **A) SQLite + Litestream → SFTP on second Oracle VM.** Drop-in swap of the
  storage engine to SQLite, run the Litestream sidecar on VM 1 that pushes
  WAL segments over SSH/Tailscale to a plain directory on VM 2. Laptop runs
  `litestream restore` to pull a consistent DB file before each analytics
  session (or periodically via cron when online). DuckDB `ATTACH`es that
  file.
- **B) libsql self-hosted on VM 1, libsql-server replica on VM 2, embedded
  replica on laptop.** The server embeds libsql as its storage library (API
  is SQLite-compatible, same stdlib `sqlite3` driver works in Python via
  `libsql-experimental`). VM 2 runs `libsql-server` in replica mode, pulling
  from VM 1. Laptop uses `libsql-client` in embedded-replica mode, keeping a
  local `.db` file in sync via HTTP/2 to VM 1 over Tailscale. DuckDB
  `ATTACH`es that local file.
- **C) libsql self-hosted on VM 1 only, Litestream on top for backup,
  laptop-as-embedded-replica.** Hybrid of A and B: libsql for laptop sync
  (real-time streaming) and Litestream for off-site durability to VM 2.

## 5. Decision matrix

Evaluating against requirements (1)–(7) from section 3:

| Axis | A: SQLite + Litestream → SFTP | B: libsql primary + libsql replica + libsql embedded client | C: libsql + Litestream hybrid |
|---|---|---|---|
| **(1) Writes never block on network** | ✓ SQLite is fully local; Litestream buffers on local disk until VM 2 is reachable | ✓ libsql primary is local on VM 1 | ✓ same as B |
| **(2) Off-site durability if VM 1 is reclaimed** | ✓ VM 2 holds WAL history + snapshots, restorable with `litestream restore` | ✓ VM 2 is a live libsql replica with full DB | ✓ both VM 2 and Litestream target |
| **(3) Laptop freshness when online** | Seconds-range: Litestream pushes WAL segments every ~1 s to VM 2, laptop `restore` pulls from VM 2 | Sub-second: direct HTTP/2 streaming from VM 1 to laptop via embedded replica | Sub-second via libsql; plus Litestream off-site |
| **(3) Laptop catches up after weeks offline** | ✓ `litestream restore` reconstructs from snapshot + accumulated WAL segments | ✓ embedded replica resumes sync from last frame index | ✓ embedded replica handles it |
| **(4) DuckDB `ATTACH` on laptop** | ✓ on-disk format is plain SQLite | ≈ format is "SQLite-compatible" in practice, but libsql has added features (vector search, virtual WAL) whose forward-compat with DuckDB's `sqlite` extension is unverified per release | ≈ same caveat as B |
| **(5) Server RAM footprint** | ~20 MB SQLite page cache + ~30–50 MB Litestream sidecar = **~50–70 MB** | ~80–120 MB libsql-server on VM 1 | Same as B plus Litestream = **~120–170 MB** |
| **(6) Vendor lock-in / exit plan** | None. SQLite is the universal baseline; Litestream is BSD-licensed and the on-disk format in the SFTP target is self-describing | Low. libsql "settled" file is SQLite-compatible, so eject is possible, but requires care around the WAL format and any libsql-specific extensions used | Same as B |
| **(7) Test compatibility** | stdlib `sqlite3`, zero driver surprises; migrations portable with minor DECIMAL / PRAGMA review | Works through `libsql-experimental` drop-in but adds a driver dependency the test matrix did not have before | Same as B |
| **Moving parts on VM 1** | uvicorn + SQLite (library, not a process) + Litestream sidecar (one systemd unit) | uvicorn + libsql library **or** separate `libsql-server` process | uvicorn + libsql-server + Litestream = three things |
| **Moving parts on VM 2** | sshd (already there) + one directory. No extra process. | `libsql-server` in replica mode (another service to run) | Same as B |
| **Failure independence** | Litestream failure is independent of uvicorn: sidecar dies, server keeps writing; WAL pile-up on local disk is bounded by our retention config | libsql-server failure on VM 1 = app failure (in embedded mode) or replica failure (in server mode, depending on how we wire it) | Mixed; Litestream still independent |
| **Dependency on a single vendor's roadmap** | None; SQLite and Litestream are independent projects | Both replication and storage depend on Turso's libsql roadmap | Partial |

Every axis that is not explicitly marked as a **tie** between A and the libsql
options is a win for A.

## 6. Decision

**Adopt option A: SQLite on the server, Litestream sidecar replicating via
SFTP over Tailscale to a second Oracle Cloud Free VM. Laptop pulls the replica
with `litestream restore` and opens it for DuckDB analytics via
`ATTACH (TYPE sqlite, READ_ONLY)`.**

Rationale in one paragraph: requirement (1) pushes us to an embedded,
process-local write store; requirements (2) and (3) push us to streaming
WAL replication; requirement (4) pushes us to a format DuckDB reads
natively; requirements (5) and (6) push us away from anything that adds a
long-running server process or a vendor-specific binary format. SQLite +
Litestream is the only candidate that wins all six simultaneously, and it
is simultaneously the most boring and the most battle-tested combination
available. libsql's real-time streaming advantage (sub-second instead of
~second latency) has no user-visible value in a single-user personal-finance
ledger and is not worth the extra service and the format-compat uncertainty
with DuckDB.

## 7. Topology

```
+-------------------------------------------------------------+
|  Tailscale mesh (tailnet = dinary)                          |
|                                                             |
|   VM 1 (Oracle Free, "dinary-primary")                      |
|     +---------------------------------------------------+   |
|     | uvicorn + FastAPI                                 |   |
|     |    stdlib sqlite3 ──▶ data/dinary.db (WAL mode)   |   |
|     | litestream (sidecar, systemd unit)                |   |
|     |    watches dinary.db-wal                          |   |
|     |    push over SFTP ──────────────────────────┐     |   |
|     +----------------------------------------------|----+   |
|                                                    |        |
|                                                    ▼        |
|   VM 2 (Oracle Free, "dinary-replica")                      |
|     +---------------------------------------------------+   |
|     | sshd (standard) + one home directory:             |   |
|     |   ~/replicas/dinary/   ← WAL segments + snapshots |   |
|     | No app process, no DB binary. Just files.         |   |
|     |                                                   |   |
|     | Daily cron (§10 Phase 3.5):                       |   |
|     |   litestream restore → /tmp/snap.db               |   |
|     |   rclone copy → yandex: / onedrive: / gdrive:     |   |
|     |     (cloud-drive snapshots, 30-day retention)     |   |
|     +---------------------------------------------------+   |
|                                                             |
|   Laptop (intermittent)                                     |
|     +---------------------------------------------------+   |
|     | (daemon when online, idle when offline)           |   |
|     | litestream restore -o analytics/dinary.db \       |   |
|     |     sftp://ubuntu@dinary-replica:...              |   |
|     |                                                   |   |
|     | duckdb: ATTACH 'analytics/dinary.db' AS ledger    |   |
|     |           (TYPE sqlite, READ_ONLY)                |   |
|     |   ↓                                               |   |
|     | inv sql / Harlequin / Marimo / dashboards / AI    |   |
|     |                                                   |   |
|     | (optional) local daily snapshot into              |   |
|     | ~/Backups/dinary/ inside a cloud-sync folder —    |   |
|     | personal-account fifth copy, not primary.         |   |
|     +---------------------------------------------------+   |
+-------------------------------------------------------------+
```

The only non-Tailscale cross-network dependency is VM 2's daily upload
to consumer cloud drives. That is defense-in-depth: it protects against
simultaneous loss of both Oracle VMs, but the primary replication chain
(VM 1 → VM 2 → laptop) never touches a third-party cloud service.

## 8. Why two VMs instead of one

On a single VM, the `.db` file and its WAL replica sit on the same disk and
share the same failure domain; if Oracle decides to reclaim the free VM or
the root disk dies, both copies go. With two free VMs — still zero marginal
cost on the Oracle Free Tier — VM 2 gives us a clean off-site copy **even
when the laptop is on holiday for two weeks**, which is the scenario the
laptop-only backup story cannot cover.

VM 2 is cheap in complexity: its core role is sshd plus a directory.
It does not need the application, the Python environment, the FastAPI
process, or the migrations. It does need **two** lightweight additions
on top of sshd (both covered in §10 Phase 3.5):

1. `litestream` binary — used only as a restore client against the
   local replica directory, so that a daily cron can materialize a
   single-file `.db` snapshot on demand. Litestream is not running as
   a long-lived daemon here; VM 1 is the one doing replication, VM 2
   is just the target of the SFTP side and an occasional local
   consumer of the stored replica.
2. `rclone` — used only by the same daily cron to upload that
   snapshot to OneDrive / Google Drive / Yandex Disk.

Both are single-binary installs with no runtime dependencies. From
VM 2's point of view, Litestream on VM 1 is still just an SFTP client
writing files; nothing about VM 2's replication role changes with the
cron layer sitting on top.

## 9. Data on the laptop: DuckDB-over-SQLite (zero-copy)

### 9.1 Why DuckDB at all when the bytes live in SQLite?

A reasonable objection: if DuckDB reads row-oriented SQLite pages via
`ATTACH`, we forfeit DuckDB's columnar compression and per-column
scan advantage. So what does DuckDB actually give us, and would
plain SQLite (CLI + `sqlite3` module) suffice?

We separate **storage choice** from **query-engine choice**:

- *Storage* is SQLite because of the server's OLTP needs: concurrent
  reads, WAL streaming replication, single-file, low RAM, no
  exclusive locks. These constraints have nothing to do with how
  analytics queries the file later.
- *Query engine on the laptop* is a separate pick. What we want there
  is: fast ad-hoc aggregations, rich analytical SQL, DataFrame/Python
  integration, growth headroom for importing CSV/Parquet bank feeds
  later. DuckDB delivers that profile whether or not it uses its own
  columnar storage.

Concrete things DuckDB brings over plain SQLite, even while reading
SQLite pages:

1. **Vectorized, multi-threaded execution.** SQLite processes rows
   one at a time on a single core. DuckDB pulls the same pages
   through the sqlite extension but executes with SIMD-friendly
   vectors across all laptop cores. On `GROUP BY category,
   date_trunc('month', date)` over a year of expenses the
   typical speedup is 5–50×, purely from execution, not storage.
2. **Analytical SQL dialect.** `PIVOT` / `UNPIVOT`, `SUMMARIZE`,
   `DESCRIBE`, `QUALIFY`, `LATERAL`, rich date/time arithmetic,
   `LIST` / `STRUCT`, `FROM 'file.csv'` inline, list aggregates.
   SQLite requires hand-written CTE gymnastics for the same shapes.
3. **Python / Polars / Arrow integration.** `duckdb.sql(...).to_df()`,
   `duckdb.sql("FROM polars_df")`, zero-copy Arrow. Marimo, Evidence,
   Streamlit, AI notebook agents treat DuckDB as first-class;
   SQLite works but through ad-hoc glue.
4. **Growth path.** When we start joining brokerage CSVs, OFX
   statements, or Parquet market data against the ledger, DuckDB
   does it in one `SELECT ... FROM 'file.csv' JOIN ledger.expenses`.
   No pre-ingest needed.
5. **Optional columnar materialization.** If a specific dashboard
   profiles as row-layout-limited, a one-liner
   `CREATE TABLE <name> AS SELECT * FROM ledger.<table>` puts it
   in DuckDB-native columnar storage for that query shape. The
   cache is rebuilt once per replica refresh, not continuously
   maintained. Columnar wins arrive where we actually need them,
   not as a blanket storage cost.

Alternatives rejected for this role:

| Candidate | Why not |
|---|---|
| Plain SQLite CLI + `sqlite3` | Single-threaded, weaker analytical SQL, awkward Python/DataFrame story, verbose for PIVOT/SUMMARIZE shapes. |
| Polars / Pandas alone | DataFrame libraries, not databases. Good for pipelines; weak for ad-hoc SQL exploration and `ATTACH`-style single source of truth. |
| Postgres on laptop with SQLite FDW | Server process, heavier setup, extra thing to keep synchronized. |
| ClickHouse / DataFusion | Overkill for 30 MB – 1 GB, lots of knobs for zero marginal benefit. |

Net: DuckDB on the laptop is justified by its query engine and
ecosystem, not by its storage format. Storage remains a SQLite
file; the analytical layer is DuckDB reading it.

### 9.2 Zero-copy ATTACH mechanics and return-from-holiday behavior

DuckDB has a mature [`sqlite` extension](https://duckdb.org/docs/current/core_extensions/sqlite)
that can `ATTACH` a SQLite file directly. **This is zero-copy**: DuckDB
does not ingest rows into its columnar format on attach; the extension
reads SQLite pages on demand and converts to DuckDB vectors per query.
There is no persistent DuckDB file on disk, no "build step", no
"populate step". The source of truth for analytics is the SQLite file
that Litestream put there.

The analytics code — `inv report-expenses`, `inv report-income`,
`inv import-report-2d-3d`, forthcoming AI dashboards — does not need
an ETL step. A single
`ATTACH 'analytics/dinary.db' AS ledger (TYPE sqlite, READ_ONLY)`
makes all ledger tables addressable as `ledger.expenses`,
`ledger.income`, etc.

**Consequence for the return-from-holiday scenario**: the time from
`inv pull-replica` completion to "dashboards work" is exactly the
Litestream restore time (sub-second for our volume) plus a fresh
DuckDB `ATTACH` (microseconds). There is no slow post-restore rebuild
phase to wait through, because DuckDB has no persistent state of its
own to rebuild. If any optional `expenses_cache`-style columnar
materializations exist, they are re-created by a trivial `inv
refresh-cache` task after pull; this is never on the critical path
for "does DuckDB work right now".

## 10. Migration plan

Phased so that every phase is reversible until the last one.

### Phase 0 — freeze the current state (prep)

- Snapshot current prod `dinary.duckdb` via the existing `inv backup` task.
  This snapshot is the safety net only; it is *not* the data source for
  Phase 1 (see below).
- Record current test counts, `inv verify-income-equivalence-all` results,
  `inv verify-bootstrap-import-all` results as the green baseline the
  migration must preserve.
- Document the exact DuckDB column types used in `src/dinary/migrations/*.sql`
  and cross-check they have SQLite equivalents (principally:
  `DECIMAL(p,s)` → `NUMERIC`, `TIMESTAMP` → `TEXT` with ISO-8601 or
  `INTEGER` with Unix-seconds, everything else is already portable).
- **Source-of-truth check.** Confirm that every row in the DB is
  reproducible from Google Sheets, so the SQLite DB can be rebuilt by
  re-running the existing import pipeline rather than copying DuckDB
  bytes over (see Phase 1 rationale). Concretely:
  1. Run `inv verify-bootstrap-import-all` and
     `inv verify-income-equivalence-all` on the live DuckDB — they
     must pass.
  2. Check `SELECT COUNT(*) FROM expenses WHERE created_at >
     '<last-verify-run-timestamp>'`. Any rows newer than the last
     green verify must already be logged to the sheets via the
     sheet-logging path; `inv verify-bootstrap-import-all` run right
     before Phase 1 covers this.
  3. Note the `app_metadata` keys (`accounting_currency`, etc.) —
     these are server-managed, not in the sheets, and will be
     re-seeded by the migration runner + a small bootstrap step,
     not by imports.

### Phase 1 — prove the storage swap on a branch (no replication yet)

**Data path: fresh bootstrap from sheets, not DuckDB → SQLite copy.**
Google Sheets are the canonical source of truth for `expenses`,
`income`, `budget`, `catalog`, and the 2D/3D reports. The existing
import pipeline (`inv import-catalog`, `inv import-budget-all`,
`inv import-income-all`, `inv import-report-2d-3d`) already handles
type coercion from sheet strings to ledger-native Decimal / date /
source_type values and is exercised in production. Rebuilding the
SQLite DB by re-running these importers is strictly simpler than
writing a one-off DuckDB-dump → SQLite-load script:

- No bespoke DuckDB `DECIMAL(p,s)` → SQLite `NUMERIC` conversion
  script to write and debug.
- No Parquet / CSV intermediate with its own type-roundtrip caveats.
- No risk of carrying DuckDB-specific artifacts (mis-typed rows,
  stale columns) into the new DB.
- The production import pipeline gets exercised end-to-end against
  SQLite from day one — so when Phase 1 completes green, we also
  know the importers survived the engine swap, not just that one
  migration ran once.
- The verification gates (`inv verify-bootstrap-import-all`,
  `inv verify-income-equivalence-all`) already compare the DB
  against sheet-derived expectations, so their passing is exactly
  the equivalence proof we want.

Concrete steps:

- Add a thin `storage/` abstraction if it does not already exist —
  just enough that `DuckDBRepo` and a new `SQLiteRepo` can be
  selected from settings.
- Port `src/dinary/services/duckdb_repo.py` to `sqlite_repo.py` using
  stdlib `sqlite3` (sync) or `aiosqlite` (async) — pick one
  consistent with the existing callers. WAL mode: `PRAGMA
  journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, `PRAGMA
  busy_timeout=5000`.
- Port the migrations runner. The SQL itself should be nearly
  unchanged. Fix any DuckDB-only syntax (`CREATE TABLE IF NOT
  EXISTS` is shared; `PRAGMA memory_limit` is DuckDB-specific and
  drops out; array types — if we use them anywhere — need a
  JSON-text representation in SQLite).
- Run the full test suite against SQLite. Target: 487 passed, zero
  regressions. Fix call sites that depend on DuckDB-specific
  behavior. Expect the bulk of the delta to be in tests that used
  `DECIMAL` arithmetic with column-precision assumptions.
- **Rebuild prod data from sheets**, in the same order that Phase 0
  of the original bootstrap used:
  1. `inv stop`
  2. Wipe `data/dinary.db*` (including `-wal` and `-shm` sidecars).
  3. `inv migrate` — creates fresh SQLite schema.
  4. Seed `app_metadata` (anchor currency, any other server-managed
     keys the migration runner doesn't cover) via a small
     idempotent `inv bootstrap-metadata` task or an explicit
     `inv sql --write` one-liner. This is the only step that does
     not come from the importers.
  5. `inv import-catalog --yes`
  6. `inv import-budget-all --yes`
  7. `inv import-income-all --yes`
  8. `inv import-report-2d-3d` (local path — we are not on the
     server yet in this phase, this is a dev-laptop rebuild).
- Run `inv verify-income-equivalence-all` and
  `inv verify-bootstrap-import-all` against the SQLite copy. They
  must match the Phase 0 baseline results row-for-row.
- Reversibility: the DuckDB snapshot taken in Phase 0 is untouched,
  so if anything looks wrong in the rebuilt SQLite DB we roll
  back by switching the storage setting, not by un-importing.

### Phase 2 — Litestream on VM 1 → VM 2 (server-side durability only)

- Provision VM 2 on Oracle Free. Add to Tailscale, verify SSH reachability
  from VM 1 over the tailnet hostname.
- Install Litestream on VM 1 (`apt install` from the Litestream APT repo,
  or a single binary drop into `/usr/local/bin/`).
- Add `/etc/litestream.yml`:

  ```yaml
  dbs:
    - path: /home/ubuntu/dinary/data/dinary.db
      replicas:
        - type: sftp
          host: dinary-replica:22
          user: ubuntu
          key-path: /home/ubuntu/.ssh/id_ed25519
          path: /home/ubuntu/replicas/dinary
          retention: 168h   # one week of WAL history
          snapshot-interval: 6h
  ```

- Deploy as a systemd unit ordered `After=dinary.service`.
- Monitor for one week: verify `litestream snapshots` lists healthy
  snapshots on VM 2, WAL catchup time stays under a few seconds, and no
  error bursts during PWA-driven write spikes.
- At this point the server has SQLite + hot off-site backup. Reversible:
  stopping Litestream has no effect on the app path.

### Phase 2.5 — retention tuning for long laptop offline windows

The Litestream replica on VM 2 has two knobs that interact:

- `snapshot-interval` — how often Litestream writes a full consistent
  snapshot into the SFTP target. Shorter interval = smaller WAL tail to
  replay on restore, slightly more storage on VM 2.
- `retention` — how long old snapshots and their WAL segments are kept in
  the target before garbage collection.

For the "laptop back from a multi-week holiday" case, the governing
invariant is **not retention, but snapshot-interval**: `litestream
restore` always starts from the most recent snapshot still in the target
and replays WAL forward from there, so any restore request is satisfied
as long as at least one snapshot from any point in time is present. With
a 6 h snapshot interval, that always holds regardless of how long the
laptop was offline.

Retention matters only for point-in-time recovery ("give me state as of
3 August evening"). The OLAP copy on the laptop does not need PITR — it
wants a fresh "now" view. A 24 h retention would therefore be sufficient
for the laptop use case; we keep 168 h (one week) in the proposed config
purely to serve operator-side disaster-recovery needs against VM 1.

Action for this phase: confirm in the VM 2 file listing, one week after
Phase 2 cutover, that there is always at least one snapshot present and
that WAL segments do not accumulate indefinitely. If snapshots ever go
missing (e.g. because the primary stopped writing), raise it as a
monitoring alert — Litestream's absence of snapshots, not WAL gap size,
is the real risk signal.

### Phase 3 — laptop-side restore workflow

- Install Litestream on the laptop.
- Add an `inv pull-replica` task that does a **disposable-replica
  refresh**, not an incremental update on an existing file:

  ```bash
  litestream restore \
    -o analytics/dinary.db.new \
    -config ~/.litestream-dinary-replica.yml

  mv analytics/dinary.db.new analytics/dinary.db
  ```

  The naming (`.new` then atomic `mv`) matters because any DuckDB session
  that has already `ATTACH`ed the old file keeps reading the old inode
  until it reconnects — harmless for short-lived `inv sql` invocations,
  handled in long-running notebooks by a manual `DETACH ledger; ATTACH
  'analytics/dinary.db' AS ledger (TYPE sqlite, READ_ONLY);`.

- **Catch-up after long laptop offline windows is a no-op in the task
  layer.** The Litestream target on VM 2 is the source of truth; the
  laptop's local file is a disposable cache. `litestream restore` on an
  empty destination finds the latest snapshot on VM 2 (always within the
  6 h `snapshot-interval`, regardless of how long the laptop was off the
  network) and replays WAL from there to HEAD. There is no "resume from
  position X" bookkeeping on the laptop, and therefore no state that can
  drift so far it becomes irreconcilable. A two-week vacation and a
  two-minute network blip take identical code paths: delete the old file,
  restore a fresh one, atomic-swap. At our data volume the transfer is a
  fraction of a megabyte either way.

  This is the architectural payoff of choosing Litestream's model over
  libsql's: we trade sub-second sync latency (which we do not need) for
  the stateless-laptop property (which eliminates a whole class of
  catch-up bugs that embedded-replica systems have to handle with
  explicit full-rebootstrap fallbacks when the primary's retention
  rolls past the replica's last-seen frame).

- Add `inv sql` wiring so `--remote` no longer means "SSH + /tmp snapshot"
  but instead "use the already-pulled replica from
  `analytics/dinary.db`". Remove the `_remote_snapshot_cmd` path for the
  `sql` task; keep it for the legacy `inv report-*` tasks until they are
  migrated too.
- Document a laptop cron entry: `*/5 * * * * inv pull-replica` — refreshes
  the replica continuously while online, fails silently while offline.
  When the laptop comes back from a holiday of any length, the next cron
  tick pulls the latest snapshot plus its WAL tail in one go.

### Phase 3.5 — defense-in-depth cloud-drive snapshots

Beyond the server-side Litestream chain (VM 1 → VM 2) and the
laptop replica, we take a **separate** daily snapshot of the SQLite
file into consumer cloud drives (OneDrive, Google Drive, Yandex
Disk — any or all). This gives a fourth copy in an independent
failure domain (Microsoft / Google / Yandex cloud) and survives
the combined loss of VM 1 *and* VM 2.

**Where to run this: VM 2, not the laptop.** Original draft put
this cron on the laptop, but VM 2 is strictly better as the primary
home for it:

| Property | VM 2 | Laptop |
|---|---|---|
| Always online | yes | no (holiday gaps of weeks) |
| Source data freshness | latest Litestream replica, always | last `inv pull-replica` run |
| Depends on laptop powered on | no | yes |
| Uses laptop upload bandwidth | no | yes |

Daily cloud snapshots therefore live on **VM 2's cron**. Laptop-side
cloud backup remains possible as an additional layer (§ 9.2
"laptop variant" below), but is optional, not primary.

**Why the cloud snapshot is a separate file, not the Litestream
replica path itself.** Litestream stores its replica as a tree of
snapshot + WAL segments, not as a single restoreable `.db` file.
Also, we want the cloud file to be usable by "open directly in any
SQLite tool" during disaster recovery. So the cron materializes a
clean single-file `.db` via `litestream restore`, uploads it, and
throws the temp file away.

#### 3.5.a Primary: VM 2 daily snapshot to cloud drive(s)

Add on VM 2 (once-only setup):

1. Install `rclone` (`apt install rclone` — small, no deps).
2. Configure remotes. The cleanest zero-OAuth path is Yandex Disk
   over WebDAV:
   - Generate an app-password in the Yandex account UI.
   - `rclone config` → new remote, type `webdav`, url
     `https://webdav.yandex.com`, vendor `other`, user + the
     app-password. No browser round-trip.
   For OneDrive / Google Drive, do the OAuth step on the laptop
   (which has a browser): run `rclone config` locally, complete the
   browser flow, then `scp ~/.config/rclone/rclone.conf
   vm2:~/.config/rclone/rclone.conf`. VM 2 then uses the refresh
   token headlessly forever.
3. Create `/home/ubuntu/bin/dinary-cloud-snapshot.sh`:

   ```bash
   #!/bin/bash
   set -euo pipefail

   # VM 2-local path where Litestream stores replica segments for VM 1.
   REPLICA_CONFIG=/etc/litestream-dinary-source.yml
   TMPFILE=$(mktemp -p /tmp dinary-snap-XXXXXX.db)
   DATE=$(date +%F)

   trap 'rm -f "$TMPFILE" "$TMPFILE"-wal "$TMPFILE"-shm' EXIT

   # Reconstruct a single consistent .db file from the replica history.
   # -o writes a clean file; no WAL sidecar needed after restore.
   litestream restore -o "$TMPFILE" -config "$REPLICA_CONFIG"

   # Integrity check before uploading (cheap, ~100 ms on our size).
   sqlite3 "$TMPFILE" 'PRAGMA integrity_check;' | grep -q '^ok$'

   # Upload to every configured remote. Names chosen so each remote
   # gets a dated file it can retain/prune independently.
   TARGETS=(yandex:dinary-backups onedrive:dinary-backups gdrive:dinary-backups)
   for t in "${TARGETS[@]}"; do
     rclone copyto "$TMPFILE" "$t/dinary-$DATE.db"
     # Keep 30 daily snapshots per destination.
     rclone delete "$t" --min-age 30d --include 'dinary-*.db'
   done
   ```

4. Crontab entry:

   ```
   0 3 * * * /home/ubuntu/bin/dinary-cloud-snapshot.sh \
       >> /var/log/dinary-cloud-snapshot.log 2>&1
   ```

Retention math: 30 files × ~30 MB = ~900 MB per destination. Fits
comfortably in the free tier of every mainstream cloud drive.

For a single-user setup one cloud destination is usually enough;
the multi-remote loop is for users who want defense-in-depth across
provider failures.

#### 3.5.b Optional: laptop additional snapshot

Equivalent local cron on the laptop can still be set up for a
fifth copy in the user's personal cloud account (independent from
VM 2's cloud credentials). This is convenience, not durability —
VM 2 already covers the durability side. Script:

```bash
#!/bin/bash
set -euo pipefail
SRC=~/projects/dinary/analytics/dinary.db
DEST_DIR=~/Backups/dinary   # inside OneDrive/GDrive/YDisk sync folder
DATE=$(date +%F)
mkdir -p "$DEST_DIR"
sqlite3 "$SRC" ".backup '$DEST_DIR/dinary-$DATE.db'"
find "$DEST_DIR" -name 'dinary-*.db' -mtime +30 -delete
```

Note that the laptop's live replica path (`analytics/dinary.db`)
must stay **outside** any cloud-sync folder: `litestream restore`
rewrites it in full each run, and having it inside a cloud-sync
directory would push ~8.6 GB/day to the cloud at a 5-minute
restore cadence — wasteful and throttle-prone. The sync folder is
for the daily snapshot only.

**What VM 2 cloud snapshots protect against that Litestream does
not**:

- Simultaneous loss of both Oracle VMs (account reclaim, provider
  outage, operator error wiping both).
- Corrupted WAL segments in the Litestream target that propagate
  to the laptop on next restore. The cloud daily snapshot predates
  the corruption and is restorable independently.
- "Something went very wrong" category in general — a `.db` file
  sitting in a cloud drive is the last-line-of-defense restore
  point, openable by any SQLite tool on any machine.

### Phase 4 — decommission DuckDB on the server

- Flip the production config to `DINARY_STORAGE=sqlite` (or just remove the
  switch once we commit).
- Delete `data/dinary.duckdb` and the `.wal` sidecar from the server once
  the SQLite copy has been live and Litestream-replicated for at least 72 h
  with green verify-* checks.
- Remove `duckdb` from `pyproject.toml` on the *server* dependency
  section. Keep it in the dev / laptop dependencies — analytics and the
  CLI tools on the laptop side still need it for `ATTACH sqlite`.
- Update `.plans/architecture.md`'s data-layer section to point to this
  plan as the historical record.

### Phase 5 — analytics uplift (separate plan)

Out of scope for this migration, but unblocked by it: moving all
`inv report-*` tasks + any future AI dashboards to run against the pulled
SQLite replica through DuckDB `ATTACH`, eliminating the `/tmp`-snapshot
path on the server entirely. A separate `.plans/` document will cover that
once this migration is green in prod for a few weeks.

## 11. Risks and mitigations

- **SQLite query performance on larger aggregates.** SQLite is row-oriented;
  a full-table `SUM(amount) GROUP BY category_id` over 100 k rows is slower
  than DuckDB equivalent. Mitigation: analytics moves to DuckDB via
  `ATTACH sqlite` on the laptop, so the comparison that matters is
  *DuckDB-on-laptop reading SQLite* vs *DuckDB-on-server reading DuckDB*.
  Benchmarks to run in Phase 1; SQLite-via-ATTACH has historically been
  within 2–3× of native DuckDB storage on similar shapes, and at our
  data size this is comfortably below one second per query.
- **Litestream sidecar dying unnoticed.** Mitigation: `litestream` has a
  `/metrics` endpoint; add a simple health-check in the existing deploy
  monitoring (or an `inv verify-replica` that lists the latest snapshot
  timestamp on VM 2 and alerts if it is older than 15 min).
- **Oracle reclaiming VM 2.** Mitigation: same failure mode as reclaiming
  VM 1 — but by symmetry VM 1 still has the live DB; we re-provision
  VM 2 and do a fresh Litestream init. The most recent daily cloud
  snapshot from VM 2 (§10 Phase 3.5) also survives in the cloud drive
  as a deeper fallback.
- **Oracle reclaiming both VM 1 and VM 2 simultaneously.** Recovery
  comes from the most recent daily snapshot in OneDrive / Google
  Drive / Yandex Disk, uploaded by VM 2's cron (§10 Phase 3.5 primary
  path). Data loss bounded by at most 24 h. Accepted — this scenario
  requires two independent failure domains (both Oracle VMs) to fail
  between the last cloud upload and recovery.
- **All three — VM 1, VM 2, and every cloud-drive copy — lost at
  once.** Final recovery is the laptop's local replica (or its
  optional fifth-copy laptop-side daily snapshot, §10 Phase 3.5.b).
  Data loss bounded by the last `inv pull-replica` run before the
  catastrophic event. Accepted — this is a single-user personal-
  finance system and this scenario requires four independent failure
  domains (two Oracle VMs plus every chosen cloud provider) to fail
  simultaneously.
- **Laptop offline for longer than retention.** Not a failure mode in
  this topology. The catch-up procedure (§10 Phase 3) does a fresh
  restore from VM 2 regardless of how stale the local file is, and
  `snapshot-interval` (not `retention`) is the governing knob for that
  to keep working — see Phase 2.5. The laptop never gets into an
  "irreconcilable" state because it does not track any sync position of
  its own.
- **WAL mode file locking on VM 1.** SQLite WAL mode uses a `-wal` and
  `-shm` sidecar; mis-configured `inv backup` or `scp` of just the `.db`
  file loses uncommitted transactions. Mitigation: `inv backup` is updated
  to either use `sqlite3 .backup` (online, consistent) or to rely on the
  Litestream target, never a raw `cp` of `.db`.

## 12. What this plan explicitly does *not* change

- FastAPI endpoint surface (`/api/expenses`, `/api/catalog`, admin routes).
- Sheet logging path — it reads from the repo abstraction and does not
  care which engine is underneath.
- PWA behavior, offline queue, catalog cache.
- Imports (`inv import-catalog`, `inv import-income-all`) other than their
  underlying SQL.
- Test shape — the expectation is that the same 487 tests run green
  against SQLite, with the only surface-level change being the engine
  fixture.
- `inv backup` as an operator tool — its implementation becomes "pull from
  Litestream target" but its contract ("give me a restorable snapshot of
  prod state") is preserved.

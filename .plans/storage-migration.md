# Storage Migration: DuckDB → SQLite (OLTP) + Litestream (hot replication) + DuckDB (OLAP on laptop)

> **Scope.** This plan documents *why* and *how* we move the server's ledger
> storage off DuckDB onto SQLite, what replication topology keeps data durable
> across free-tier infrastructure, and how analytics keeps its DuckDB-shaped
> OLAP layer on the laptop side. User-visible behavior (PWA flows, FastAPI
> endpoint surface, sheet semantics, category model) is intended to stay the
> same, but the storage boundary, migrations, runtime repo wiring, operator
> workflows, and some internal service implementations will change.

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
3. **Laptop gets a readable copy that is up to date within a few minutes
   automatically when online, and on demand within seconds of an explicit
   refresh.** After a multi-week outage, the same refresh path must converge
   without any operator-only recovery procedure.
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

Phased so that early phases are fully reversible, and the production
cutover remains one-step reversible only until the first post-cutover
SQLite write is accepted.

### Phase 0 — freeze the current state (prep)

- Snapshot current prod `dinary.duckdb` via the existing `inv backup` task.
  This snapshot is the safety net only; it is *not* the data source for
  Phase 1 (see below).
- Record the current green gate as the baseline the migration must preserve:
  `uv run inv pre`, `uv run pytest`, `inv import-verify-income-all`,
  and `inv import-verify-bootstrap-all`.
- Document the exact DuckDB column types used in `src/dinary/migrations/*.sql`
  and cross-check they have SQLite equivalents (principally:
  `DECIMAL(p,s)` → `NUMERIC`, `TIMESTAMP` → `TEXT` with ISO-8601 or
  `INTEGER` with Unix-seconds, everything else is already portable).
- **State inventory and source-of-truth check.** Split the current DB into
  three buckets before designing the cutover:
  1. **Sheet-derived durable state**: catalog, mappings, expenses,
     expense tags, income, and report-imported rows. These must be
     reproducible by re-running the existing import pipeline, so the
     SQLite DB can be rebuilt from sheets rather than from copied
     DuckDB bytes (see Phase 1 rationale).
  2. **Server-managed durable state**: `app_metadata`. These keys are
     not in the sheets and must be re-seeded explicitly as part of the
     rebuild.
  3. **Ephemeral/cache state**: `exchange_rates`, `sheet_logging_jobs`,
     and the migration bookkeeping tables. These are not treated as
     migrated business data: `exchange_rates` can be repopulated after
     cutover; `sheet_logging_jobs` must be drained to zero before the
     final stop; migration tables are recreated by the runner.

  Concretely:
  1. Run `inv import-verify-bootstrap-all` and
     `inv import-verify-income-all` on the live DuckDB — they
     must pass.
  2. Enumerate the actual `app_metadata` keys in production
     (`accounting_currency`, `catalog_version`, and any future keys)
     and decide which are seeded by migrations vs a dedicated
     bootstrap task.
  3. Confirm `sheet_logging_jobs` is operationally drainable: no
     permanently poisoned rows are being relied on as the only record
     of unsent work, and a pre-cutover drain-to-zero is realistic.
  4. Confirm `exchange_rates` is acceptable as a warm cache, not a
     durable source of truth; the post-cutover system must be able to
     repopulate it lazily without data loss.

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
- The verification gates (`inv import-verify-bootstrap-all`,
  `inv import-verify-income-all`) already compare the DB
  against sheet-derived expectations, so their passing is exactly
  the equivalence proof we want.

Concrete steps:

- Treat this as a **repo-wide engine-boundary refactor**, not a thin
  file swap. The current code imports `ledger_repo` directly across
  API handlers, imports, reports, and tests; uses DuckDB connection
  types in annotations; ships a DuckDB-only yoyo backend; and embeds
  DuckDB-specific SQL constructs (`CREATE SEQUENCE`, `nextval(...)`,
  `LIST(...)`, `[]::INTEGER[]`, DuckDB exception classes, and
  `read_only=True` connection assumptions). Inventory those call
  sites first so the branch scope is explicit.
- Introduce the narrowest compatibility boundary that keeps the
  branch reviewable. That may still preserve the `ledger_repo`
  module path temporarily as a facade backed by SQLite; the key is
  that application code stops depending on DuckDB-only types and
  behaviors.
- Port the runtime repository to SQLite using one access model that
  matches the existing callers. WAL mode: `PRAGMA journal_mode=WAL`,
  `PRAGMA synchronous=NORMAL`, `PRAGMA busy_timeout=5000`.
- Port the migrations runner and the schema/query layer. Assume the
  SQL is **not** "nearly unchanged" until proven otherwise; convert
  every DuckDB-only construct explicitly:
  - `CREATE SEQUENCE` / `nextval(...)` → SQLite rowid or explicit id
    allocation strategy.
  - `LIST(...)` / `[]::INTEGER[]` → JSON-text storage or Python-side
    aggregation.
  - DuckDB-specific exception handling / transaction semantics →
    SQLite equivalents.
- Build a branch-local SQLite database from sheets, not from a
  copied DuckDB file:
  1. `inv restart-server` — starts server, yoyo creates fresh schema, then stop it.
  2. Seed `app_metadata` via an idempotent bootstrap step.
  3. `inv import-catalog --yes`
  4. `inv import-budget-all --yes`
  5. `inv import-income-all --yes`
  6. `inv import-report-2d-3d`
  7. Warm `exchange_rates` only if a test or verify task requires it;
     otherwise leave it cold and confirm lazy refill works.
- Update `inv backup` on the branch before any prod cutover work begins.
  Under SQLite WAL, it must produce a consistent snapshot via `sqlite3
  .backup` or by restoring from the Litestream target; a raw file copy
  of `.db` is no longer an acceptable implementation.
- Run the full project gate against the SQLite branch copy:
  `uv run inv pre`, `uv run pytest`,
  `inv import-verify-income-all`, and
  `inv import-verify-bootstrap-all`. Fix every regression before
  moving on. This is the **code-health / behavior-regression gate** for
  the migration branch; it proves the repo is ready to deploy, not that a
  specific production SQLite file has already been validated.
- Reversibility: this phase does **not** touch production. The
  DuckDB snapshot taken in Phase 0 stays untouched, and the branch
  can be thrown away without any operator action.

### Phase 1.5 — production cutover rehearsal and final cutover

The branch proof above is necessary but not sufficient: production
needs an explicit "stop writes, rebuild from the latest durable
inputs, validate, then flip traffic" sequence so no post-verify rows
or queue state are silently lost.

Concrete steps:

- **Rehearsal on non-prod first.** Run the exact cutover checklist on a
  staging/dev environment using a recent prod snapshot plus current
  sheets. Measure wall-clock downtime and document every manual step.
- **Provision durability infrastructure before first SQLite write is
  accepted.** VM 2, SSH/Tailscale reachability, the replica directory,
  and the Litestream binary/service wiring on VM 1 must all be prepared
  before the prod flip. The post-cutover SQLite primary must never spend
  time as a single-VM-only source of truth.
- **Pre-cutover quiescence on prod.**
  1. Disable external writes (maintenance mode or stop `dinary`
     cleanly).
  2. Drain `sheet_logging_jobs` to zero while DuckDB is still the
     primary. Any poisoned jobs must be resolved or explicitly
     accepted before proceeding.
  3. Run the final `inv import-verify-bootstrap-all` and
     `inv import-verify-income-all` against live DuckDB with the
     service quiesced, so the sheets and DB are known to agree at the
     cut line.
- **Rebuild the new prod SQLite primary on VM 1 from the quiesced
  source-of-truth inputs**, in the same order as the verified branch
  flow:
  1. Create fresh `data/dinary.db`: `inv restart-server` (yoyo applies schema), then stop the service.
  2. Seed `app_metadata`.
  3. Run the import sequence from sheets.
  4. Do **not** carry over `sheet_logging_jobs`; it should already be
     empty. Do **not** copy `exchange_rates`; let it repopulate.
- **Establish off-site replication while prod is still quiesced.**
  1. Point Litestream at the freshly-built `data/dinary.db`.
  2. Start the Litestream service.
  3. Confirm that VM 2 receives at least one valid snapshot / WAL chain
     for this SQLite file before production accepts writes again.
- **Validate before accepting writes.** Split validation into two
  independent buckets:
  1. **Code / release validation**: on the exact commit being deployed,
     `uv run inv pre` and `uv run pytest` must already be green. This
     is a property of the codebase and release artifact, not of the
     specific prod DB file.
  2. **Rebuilt-prod-data validation**: against the just-built SQLite
     file on VM 1, run direct checks that prove *this database* is
     usable: `PRAGMA integrity_check`, the sheet-equivalence verifies
     (`inv import-verify-income-all`,
     `inv import-verify-bootstrap-all`), explicit validation of
     server-managed `app_metadata` keys (`accounting_currency`,
     `catalog_version`, and any other keys inventoried in Phase 0), and
     a minimal app-level smoke pass against the quiesced service or
     equivalent local runner.
  Only after both buckets are green do we enable the app with
  `DINARY_STORAGE=sqlite`.
- **Rollback path.**
  1. **Before first post-cutover write is accepted**: rollback is
     one-step — stop the SQLite app, switch config back to DuckDB,
     restart, and discard the rebuilt SQLite file.
  2. **After SQLite has accepted new writes**: rollback is no longer a
     one-step config flip. At that point, reverting requires freezing
     writes again and rebuilding/synchronizing the target engine from
     the durable source of truth; do not promise instant fallback once
     the new primary has diverged.

### Phase 1.9 — provision VM 2 (replica)

Target shape in the Oracle Cloud Always Free tier:

- Instance: `VM.Standard.E2.1.Micro` (1 OCPU, 1 GiB RAM — the only
  shape actually available under "Always Free" on most regions once
  A1.Flex capacity is gone).
- Image: `Canonical-Ubuntu-22.04-Minimal` (same minimal variant as
  VM 1; shrinks the attack surface and matches VM 1's patch cadence
  profile).
- Boot volume: 50 GiB (leaves headroom for LTX history + OS; the
  actual replica payload is under 1 GiB even with aggressive
  retention).
- Network: **same VCN as VM 1**, placed in the **same public subnet
  `10.0.0.0/24`** with a public IPv4 assigned. We deliberately do
  **not** use a separate private subnet — the public IP is closed
  off at the sshd layer by `inv setup-server --tailscale`/`inv setup-replica` immediately after
  bootstrap, so defense-in-depth vs. a private subnet is marginal
  against the cost of the extra NAT Gateway + route table + bastion
  plumbing. This matches VM 1's topology exactly, so both machines
  are operationally interchangeable from the operator's perspective.
- Hostname (OCI): any unique label — the operator-facing handle is
  the Tailscale MagicDNS name, not the OCI instance name.
- SSH key: the same operator key that is authorised on VM 1
  (`~/.ssh/id_ed25519.pub` on the laptop). Reusing the key keeps the
  list of credentials to rotate small and means `inv setup-replica`
  can land on VM 2 with the operator's regular SSH identity.

Tailscale must be brought up on VM 2 manually (one-off step):

```
ssh ubuntu@<vm2-public-ip>
curl -fsSL https://tailscale.com/install.sh | sudo sh
sudo tailscale up --hostname=dinary-replica --ssh=false
# click the login URL and approve the device in the tailnet admin
```

We do not automate the OAuth flow. An auth key would eliminate the
browser click but we would have to either ship it through `.deploy/.env`
(broadens the blast radius of an env leak) or keep it alongside a
password manager (still an extra long-lived credential to rotate).
For a step that runs once per replica-VM lifetime, the manual OAuth
click is cheaper than either alternative.

Once Tailscale is up, set `DINARY_REPLICA_HOST=ubuntu@dinary-replica`
(MagicDNS) in `.deploy/.env` and run `inv setup-replica` from the
operator laptop. The task wraps the four idempotent shell blocks:

1. `apt-get install -y unattended-upgrades` (CVE coverage without
   `inv deploy` ever touching this host).
2. `/var/lib/litestream` at mode `0750`, owned by `ubuntu:ubuntu` —
   matches the SFTP login identity Litestream uses from VM 1, and
   the path referenced as the replica URL in `/etc/litestream.yml`.
3. 1 GiB `/swapfile` (same `_build_setup_swap_script` used by VM 1 —
   an `apt-get upgrade` on 956 MiB of RAM is an OOM candidate without
   it).
4. Tailscale-only SSH applied on VM 2 (the `--tailscale` behaviour
   now built into `inv setup-replica`) — binds sshd to the
   Tailscale IPv4 + loopback, closes public TCP/22.

`inv setup-replica` is idempotent: each step short-circuits on
re-apply, so a partial rollout can be finished by simply re-running
the task. The only non-idempotent manual step is the initial
`tailscale up` above.

**Current production state (April 2026): step 4 is intentionally
reverted.** After applying the Tailscale-only SSH posture on both VM 1 and
VM 2, the resulting topology made every ingress path — SSH, the API
served through Tailscale Serve, the Litestream SFTP stream —
depend on the same tailnet tunnel. A single `tailscaled` crash or a
regional Tailscale coordination-server blip would simultaneously
kill operator access and the PWA's data plane, with no independent
break-glass because the `ubuntu` user has no password set in
`/etc/shadow` and therefore the Oracle Cloud Serial Console cannot
authenticate. The trade-off was judged worse than the risk
`ssh-tailscale-only` was supposed to remove (public SSH brute-force
noise, not actual compromise — key-only auth already defeats
brute-force). The Tailscale-only drop-in
`/etc/ssh/sshd_config.d/10-tailscale-only.conf` was removed on both
hosts, sshd listens on `0.0.0.0:22` again, and `fail2ban`
(`backend=systemd`, 1 d initial ban, doubling up to 30 d) absorbs
the log noise and auto-bans the repeat offenders. The
`inv setup-replica` task still emits step 4 so a future re-bootstrap
starts from the conservative "closed" state; operators who want the
open-public posture delete the drop-in with a one-line `ssh ubuntu@
<replica> 'sudo rm /etc/ssh/sshd_config.d/10-tailscale-only.conf &&
sudo systemctl reload ssh'` as the tail of the setup run.

The replica bootstrap deliberately does **not**:

- Install Python, `uv`, or the `dinary` codebase — the replica runs
  no application, only the SFTP daemon that Litestream pushes to.
- Install or configure `litestream` itself — Litestream runs on the
  *primary* (VM 1), pushing to VM 2 as a plain SFTP endpoint. Only
  VM 1 needs `litestream-setup`.
- Open a tunnel to the public internet for the replica — there is
  no HTTP service to expose. The only ingress is SFTP over SSH, and
  that is already reachable over the tailnet via `ubuntu@dinary-replica`.

### Phase 2 — Litestream steady state on VM 1 → VM 2

- Precondition: Phase 1.5 has already produced a validated
  `data/dinary.db` on VM 1, production is serving from SQLite, and VM 2
  is already receiving Litestream snapshots.
- Keep `/etc/litestream.yml` on VM 1 as (Litestream v0.5.x schema —
  `replica` is singular, and `snapshot`/`retention` live at the top
  level, not per-replica):

  ```yaml
  snapshot:
    interval: 1h         # one full snapshot per hour
    retention: 168h      # keep one week of LTX history
  dbs:
    - path: /home/ubuntu/dinary/data/dinary.db
      replica:
        type: sftp
        host: dinary-replica:22
        user: ubuntu
        key-path: /home/ubuntu/.ssh/id_ed25519
        path: /home/ubuntu/replicas/dinary
  ```

- Monitor for one week: verify `litestream databases` lists the
  managed DB, LTX catchup time stays under a few seconds, and no
  error bursts during PWA-driven write spikes. (The v0.4 `litestream
  snapshots` subcommand was removed in v0.5 when the storage layer
  was rewritten around LTX files; v0.5 replaces it with
  `databases`/`list`/`status`.)
- At this point the server has SQLite + hot off-site backup. Reversible:
  stopping Litestream has no effect on the app path.

### Phase 2.5 — retention tuning for long laptop offline windows

The Litestream replica on VM 2 has two knobs that interact:

- `snapshot.interval` — how often Litestream writes a full consistent
  snapshot into the SFTP target. Shorter interval = smaller LTX tail to
  replay on restore, slightly more storage on VM 2.
- `snapshot.retention` — how long old snapshots and their LTX segments
  are kept in the target before garbage collection.

For the "laptop back from a multi-week holiday" case, the governing
invariant is **not retention, but `snapshot.interval`**: `litestream
restore` always starts from the most recent snapshot still in the target
and replays LTX forward from there, so any restore request is satisfied
as long as at least one snapshot from any point in time is present. With
a 1 h snapshot interval, that always holds regardless of how long the
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
  1 h `snapshot.interval`, regardless of how long the laptop was off the
  network) and replays LTX from there to HEAD. There is no "resume from
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
- Document a laptop cron entry: `*/5 * * * * inv pull-replica` — the
  **automatic freshness** path, good for "within a few minutes when
  online". Also document that `inv pull-replica` is safe to run on
  demand before a dashboard/notebook session when the user wants the
  latest data immediately. When the laptop comes back from a holiday
  of any length, the next cron tick (or a manual run) pulls the latest
  snapshot plus its WAL tail in one go.

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

#### 3.5.a Primary: VM 2 daily snapshot to Yandex.Disk — IMPLEMENTED

Shipped as `inv setup-replica` (bootstrap) + `inv backup-cloud-restore` (restore). See
[`docs/src/en/operations.md`](../docs/src/en/operations.md#off-site-backup-yandexdisk-daily-gfs-retention)
("Off-site backup: Yandex.Disk" and "Point-in-time restore from
Yandex.Disk") for the operator-facing runbook. Key deviations from
the original draft above:

- **GFS retention, not 30-day flat.** 7 daily / 4 weekly / 12
  monthly / all yearly indefinitely. On a 10-year horizon this is
  ~29 files total, ~9 MB on disk — orders of magnitude cheaper
  than the 30×30 MB = 900 MB/destination math in the original
  draft. Rationale: user explicitly rejected the flat 30-day
  window as "ой, почему самый старый бэкап — месячной давности".
- **Yandex.Disk only (not multi-remote).** Keeps the bootstrap
  story single-OAuth and the bash pipeline small. Nothing prevents
  adding more `rclone` remotes later.
- **zstd -19 compression.** An uncompressed ~1 MB `dinary.db`
  compresses to ~300 KB; the `.db.zst` filename makes the format
  obvious to anyone browsing the Yandex folder.
- **systemd oneshot + timer, not crontab.** Matches the rest of
  the prod systemd-native tooling (`dinary.service`,
  `litestream.service`). `Persistent=true` closes the
  reboot-on-the-hour retention-gap footgun cron could not.
- **`rclone config` stays manual.** User confirmed that the
  one-time interactive OAuth browser click is acceptable friction
  and explicitly asked for rclone itself to be pre-installed so
  disaster-recovery does not hit `apt install` in a stressed
  terminal; `inv setup-server` and `inv setup-replica` now both install
  rclone themselves.

The pure-string builders (`_build_backup_script`,
`_build_backup_retention_script`, `_build_backup_service_unit`,
`_build_backup_timer_unit`) in `tasks.py` are the authoritative
source for what runs on VM 2; the operations doc summarizes the
operator-facing surface.

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
  with green `uv run inv pre`, `uv run pytest`, and verify-* checks.
- Remove `duckdb` from `pyproject.toml` on the *server* dependency
  section. Keep it in the dev / laptop dependencies — analytics and the
  CLI tools on the laptop side still need it for `ATTACH sqlite`.

  **Deviation (implementation): `duckdb` has been fully removed from
  `pyproject.toml` for now, including from dev dependencies.** The
  server code no longer imports `duckdb` anywhere (the `ledger_repo`
  module is a pure-`sqlite3` compatibility facade), so shipping
  `duckdb` as a dev dep would be dead weight until Phase 5 actually
  wires up the `ATTACH sqlite` laptop analytics path. `duckdb` should
  be re-added as a dev dep in the Phase 5 implementation PR together
  with the first piece of code that imports it.
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
  `snapshot.interval` (not `snapshot.retention`) is the governing knob for that
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
- PWA behavior, offline queue, catalog cache, and the observable sheet
  logging contract.
- The meaning of catalog data, mapping semantics, and imported ledger rows.
- High-level operator contracts such as "`inv backup` gives me a
  restorable snapshot of prod state".
- This is **not** a promise that internals stay untouched. The storage
  implementation, repo boundary, migrations backend, runtime connection
  handling, and some service internals *will* change to support SQLite.
- Imports (`inv import-catalog`, `inv import-income-all`) other than their
  storage adapter / underlying SQL.
- Test shape — the expectation is that the same 487 tests run green
  against SQLite, with the only surface-level change being the engine
  fixture.
- `inv backup` as an operator tool — its implementation becomes "pull from
  Litestream target" but its contract ("give me a restorable snapshot of
  prod state") is preserved.

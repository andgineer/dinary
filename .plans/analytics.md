# Analytics layer (OLAP on the laptop)

> **Status.** Stub. This document captures architectural decisions that
> are already firm (monorepo placement, runtime tiering, SQL-first
> design) and leaves placeholders for decisions to be made once
> implementation starts. Implementation itself is expected to begin
> only after the storage migration (`storage-migration.md` Phases
> 0–4) has landed.

## 1. Scope

The analytics layer covers everything that reads the ledger for
insight rather than to write back into it:

- Existing CLI reports (`inv report-expenses`, `inv report-income`,
  `inv import-report-2d-3d`).
- Forthcoming AI-driven interactive dashboards.
- Ad-hoc exploration via notebooks.
- Periodic complex reports (monthly / yearly roll-ups, envelope
  accounting summaries, budget vs actual).

It explicitly does **not** cover: OLTP writes, sheet logging, imports
from Google Sheets, migrations, API surface. Those stay in the
existing service layer and keep SQLite (via the `storage/`
abstraction in `storage-migration.md` Phase 1) as their sole engine.

## 2. Relationship to storage-migration.md

This plan builds on top of the storage migration and does not
repeat its content. Key cross-references:

- Storage engine on the laptop: `.plans/storage-migration.md` §9.2
  (zero-copy DuckDB-over-SQLite ATTACH).
- Why DuckDB is the right query engine despite row-oriented storage:
  `.plans/storage-migration.md` §9.1.
- Replica refresh workflow (`inv pull-replica`):
  `.plans/storage-migration.md` §10 Phase 3.
- Daily cloud snapshots on VM 2: `.plans/storage-migration.md` §10
  Phase 3.5.

The invariant this plan inherits: **analytics reads a read-only
SQLite replica; it never writes back to `ledger.*`**. Any
materialized caches are DuckDB-local and disposable.

## 3. Repository placement: monorepo (decided)

Analytics code lives in the same `dinary-server` repository as the
server, for these reasons (unchanged from the design discussion that
produced this plan):

- Schema changes in the server ripple into analytics instantly; one
  PR edits the migration and the dependent report together.
- Shared utilities (Decimal / Currency coercers, category tree
  walkers, envelope classifiers, source_type normalizers, sheet
  column mappers) are imported directly rather than vendored or
  published as a side package.
- AI coding assistants see server schema, business rules, and
  analytics in one context — critical for the "AI-driven dashboards"
  use case.
- Single `inv` task surface for both sides.

Heavyweight analytical dependencies (DuckDB, Polars, Marimo,
plotting, LLM SDKs) are isolated via `[project.optional-dependencies]`
in `pyproject.toml`, not via a separate repo. VM 1 installs without
the `analytics` extra; the laptop installs with it. Install commands
and the exact split live in §7 below (TBD).

Proposed layout under `src/`:

```
src/dinary/
  analytics/
    __init__.py
    connection.py     # open_ledger() helper: duckdb + ATTACH sqlite
    queries/          # named reusable SQL (.sql files or constants)
    reports/          # Python functions returning DataFrames
    caches.py         # optional CREATE TABLE AS SELECT definitions
notebooks/            # Marimo *.py notebooks (tracked in git)
analytics/            # laptop runtime data: replica + caches (gitignored)
```

An import-time guard will ensure `dinary.analytics` is never imported
from server-side modules, so the `analytics` extra stays truly
optional on VM 1.

## 4. Runtime tiers

Three levels of "where analytics runs" trade install footprint against
interactivity and AI capability. The project targets Tier 1 first;
Tier 2 is deferred until a concrete need arises; Tier 3 is rejected.

### 4.1 Tier 1 — Python on developer laptop (primary, decided)

- Dependencies installed via `uv sync --extra analytics` into the
  existing project venv. Incremental footprint ~200 MB (DuckDB,
  Polars, Marimo, Altair/matplotlib). Zero additional install for
  the developer who already runs `inv dev`.
- Notebooks are [Marimo](https://marimo.io/) reactive `*.py` files —
  dif-friendly, tracked in git, executed via `inv notebook`
  (`marimo edit notebooks/<name>.py`).
- Analytical logic is Python-over-SQL: business rules expressed as
  SQL strings executed against the DuckDB-attached SQLite replica;
  Python handles orchestration, plotting, and AI-tool glue.
- Full AI integration: LLM SDK calls from notebook cells, MCP servers
  for DuckDB, Cursor / Claude exercising the notebook directly.
- Runtime: everything is local to the laptop. No extra services to
  deploy.

This is the default tier for all single-user / developer-mode use.

### 4.2 Tier 2 — DuckDB-WASM in the browser (optional, deferred)

Considered and pre-approved as an additive path if/when browser-only
access matters (phone dashboards, sharing with a non-technical user,
multi-device view).

Building blocks:

- [DuckDB-WASM](https://duckdb.org/docs/api/wasm/overview) for
  in-browser SQL execution (~25 MB cached).
- [Evidence.dev](https://evidence.dev/) or
  [Marimo-WASM](https://docs.marimo.io/guides/wasm.html) for the
  dashboard framework (static site build output).
- SQLite replica served over HTTPS from a Tailscale-reachable
  endpoint (VM 2 with a static file server) using HTTP Range
  requests; DuckDB-WASM supports this natively.

Implementation cost at that point is roughly one afternoon of
wiring, *provided* the SQL queries from Tier 1 were written without
pandas-specific transformations (see §5 SQL-first design).

Not built now. Mentioned so the path stays open.

### 4.3 Tier 3 — Static HTML from nightly render (rejected)

Pre-rendered static dashboards generated by a cron job. Rejected
because it sacrifices interactivity and AI-driven exploration,
which are the main reasons to build the analytics layer in the
first place. If Tier 2 becomes necessary we use DuckDB-WASM
(interactive in the browser), not pre-baked HTML.

## 5. Design principle: SQL-first business logic

A deliberate constraint, motivated by both performance and the
Tier 1 → Tier 2 migration path:

**Analytical business logic is written as SQL executed by DuckDB,
not as Python manipulating DataFrames.** Python owns orchestration,
caching, plotting, and AI glue; SQL owns the "what the numbers
mean" part.

Concrete example:

```python
# Discouraged: business logic hidden inside pandas
def monthly_totals(df):
    return df.groupby(pd.Grouper(key="date", freq="ME"))["amount_rsd"].sum()

# Preferred: business logic in SQL, Python is only the call site
def monthly_totals(ledger):
    return ledger.sql("""
        SELECT date_trunc('month', date) AS month,
               SUM(amount_rsd) AS total
        FROM expenses
        GROUP BY month
        ORDER BY month
    """).to_df()
```

Reasons:

1. **Portability to Tier 2.** DuckDB-WASM runs the same SQL dialect.
   A Tier 1 report written as SQL ports to a Tier 2 browser dashboard
   as a copy-paste, not a rewrite.
2. **Performance.** DuckDB's vectorized engine beats pandas on every
   aggregation shape relevant here. Leaving the work in SQL keeps
   the fast path intact.
3. **Reviewability.** SQL is easier to read for "is this business
   rule correct?" than a chain of pandas calls with implicit index
   semantics.
4. **AI-assisted coding.** LLMs write reliable DuckDB SQL; they are
   less reliable at writing idiomatic pandas, especially with
   date arithmetic and group-by semantics.

Not a dogma: there are places where Python is the right tool
(plotting, interactive widgets, LLM tool-use loops, I/O to
CSV/Parquet imports). The rule applies to *business semantics*
— "what does 'monthly expense total by envelope' mean" belongs
in SQL. "How do I render that as an interactive chart" belongs
in Python.

## 6. Invariants (inherited + plan-specific)

- Analytics is strictly read-only against `ledger.*`. `ATTACH` with
  `READ_ONLY` is enforced at the connection level. No code path
  opens the replica writable.
- Analytics never touches the live OLTP SQLite on VM 1 directly.
  The replica on VM 2 / the laptop is the only legitimate source.
- Any materialized DuckDB-native caches (§4.1 optional
  `CREATE TABLE ... AS SELECT`) are regenerated from the replica,
  never hand-edited. They are treated as disposable; a valid
  recovery from any cache bug is `DROP TABLE` + rerun the builder.
- The `analytics` dependency group is optional. Server-side code
  must remain import-clean when that group is not installed. CI
  should enforce this by running a server-only test profile without
  the extra.

## 7. Dependency groups in `pyproject.toml` (to be finalized)

Exact split to be decided when implementation starts. Sketch:

```toml
[project.optional-dependencies]
analytics = [
    "duckdb>=1.1",
    "polars>=1.5",
    "marimo>=0.9",
    "altair>=5.4",
    # + LLM SDK once chosen
]
```

Outstanding decisions before committing this to the repo:

- Sync vs async SQLite driver on the server (decided in storage-
  migration.md Phase 1; mirrored here for the analytics connection
  helper).
- Exact DuckDB version pin and extension list (sqlite, json, icu).
- Plotting stack: Altair + Vega alone, or also matplotlib for
  static exports.
- LLM SDK choice (Anthropic / OpenAI / litellm wrapper): drives
  how `src/dinary/analytics/ai/` is shaped.

## 8. Placeholder: dashboard framework

To be decided during the first concrete dashboard. Candidates and
their fit:

- **Marimo** (Tier 1 primary): reactive notebooks, excellent Python
  integration, single-file, good for iterative exploration.
- **Evidence.dev** (Tier 2 candidate): Markdown + SQL, static site
  output, native DuckDB, excellent published-dashboard UX.
- **Streamlit**: popular but stateful-per-session, less ideal for
  AI tool-use flows, heavier than Marimo for our scale.
- **Rill Developer**: DuckDB-native, opinionated dashboards,
  strong for time-series metrics.

The decision gates on the first concrete requirement, not now.

## 9. Placeholder: AI integration

To be decided. Likely shape:

- MCP server wrapping DuckDB queries for use from Claude Desktop /
  Cursor.
- Prompt scaffolding for "describe this expense trend" / "classify
  this anomaly" / "suggest an envelope rebalancing" tasks.
- Guardrails preventing LLM-generated SQL from running with write
  access (already guaranteed by the read-only invariant in §6,
  reinforced by connection-level config).

Deferred until there is a concrete dashboard that needs it.

## 10. Placeholder: module catalog and first reports

When implementation starts, the first wave will likely be:

1. Port `inv report-expenses` and `inv report-income` to read
   through `src/dinary/analytics/connection.py` (DuckDB + ATTACH)
   instead of the current DuckDB server-local read. Zero semantic
   change; mechanical.
2. Port `inv import-report-2d-3d` similarly.
3. Add a first exploratory Marimo notebook (e.g.
   `notebooks/expenses-explore.py`) to exercise the connection
   helper and the SQL-first pattern.
4. Revisit the module layout in §3 with real usage data.

## 11. What this plan explicitly does *not* cover

- Storage choice on the server (covered by `storage-migration.md`).
- Replication mechanics (covered by `storage-migration.md` §10
  Phases 2–3.5).
- PWA UI, API surface, sheet logging, imports — untouched by the
  analytics work.
- A public multi-user analytics service. The scope is one
  single-user ledger owned by the repository maintainer.

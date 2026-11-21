# Plan: the chat and MCP clients see only the bookkeeping tables

## Decision

The ledger snapshot the laptop downloads stays a full copy of the server database. The
restriction is applied on the laptop: SQL written by a model — the dashboard chat's
`query_ledger` tool and the MCP `query` tool — can reach the bookkeeping tables and nothing
else.

Accepted consequence of keeping the full copy: the replica file on the laptop's disk still
contains the non-bookkeeping tables, including `llmbroker_secrets`. This plan closes the path
from a model to those tables; it does not remove them from the file.

## Facts (checked 2026-11-12/13)

- `GET /api/analytics/db-snapshot` backs up the whole SQLite database. The replica on the
  maintainer's laptop holds `llmbroker_secrets` (4 rows), `llmbroker_calls`,
  `llmbroker_registry`, the job queues, `import_mapping*`, `sheet_mapping*` and the yoyo and
  litestream tables next to the ledger.
- `open_ledger()` in `src/dinary_analytics/connection.py` runs
  `ATTACH '<replica>' AS ledger (TYPE sqlite, READ_ONLY)`, so `SELECT * FROM
  ledger.llmbroker_secrets` works from both model-facing tools.
- `ledger_schema()` already describes only the tables in `LEDGER_TABLES`, so a model is not
  told the other tables exist. That is not a restriction: it can list them through
  `information_schema` or `duckdb_tables()`.

### Experiments that shaped the design

Both ran against the real replica with the project's DuckDB.

1. **Copying the allowed tables into memory and detaching is not enough.** After
   `CREATE TABLE ledger.t AS SELECT * FROM src.t`, `DETACH src`,
   `SET enable_external_access = false` and `SET lock_configuration = true`, these still
   succeeded: `ATTACH '<replica>' AS again (TYPE sqlite, READ_ONLY)` and
   `SELECT count(*) FROM sqlite_scan('<replica>', 'llmbroker_secrets')`. The SQLite extension
   does its own file I/O, and `enable_external_access` does not cover it once the extension
   is loaded in the instance.
2. **A connection whose instance never loaded the SQLite extension is closed.** With
   `duckdb.connect(":memory:", config={"autoload_known_extensions": False,
   "autoinstall_known_extensions": False})`, tables filled without the extension, then
   `SET enable_external_access = false` and `SET lock_configuration = true`, every one of these
   was refused: `ATTACH … (TYPE sqlite)`, `sqlite_scan(…)`, `read_text(…)`, `LOAD sqlite`,
   `INSTALL sqlite`, `SET autoload_known_extensions = true`, plain `ATTACH '<file>'`, and
   `COPY … TO '<file>'`.
3. Filling the tables row by row with `executemany` took 2 s for the 5 820-row `expenses`
   table, which is too slow to do on every query. The extension-based copy of all eight tables
   took 0.06 s.

## Design

Two kinds of connection, built from one function each in `connection.py`:

- **Trusted connection** — today's `open_ledger()`, unchanged. Used only by SQL that ships with
  the package: `queries/*.sql`, the dashboard's own cells, `views.py`.
- **Restricted connection** — new `open_ledger_restricted()`. Used by every tool that executes
  SQL a model wrote.

The restricted connection reads a **sanitized cache**: a DuckDB database file
`ledger-cache.duckdb` in the app-data directory, next to the replica.

1. `build_ledger_cache(replica_path, cache_path)`:
   - opens a throwaway in-memory DuckDB connection, attaches the replica with the SQLite
     extension and attaches a new temporary DuckDB file;
   - for each table in `LEDGER_TABLES` that exists in the replica, runs
     `CREATE TABLE cache.ledger.<t> AS SELECT * FROM src.<t>` (schema `ledger`, so the
     `ledger.` prefix in existing prompts and saved queries keeps working);
   - closes the connection and `os.replace`s the temporary file onto `cache_path`.
2. The cache is rebuilt when it is missing or older than the replica file (compare mtimes in
   `open_ledger_restricted()`), so both processes — the background service and the dashboard —
   pick up a refresh without talking to each other. The build is atomic, so a concurrent
   reader keeps its old file.
3. `open_ledger_restricted()` opens `cache_path` with `read_only=True` and the two
   `autoload/autoinstall = False` config values, then runs
   `SET enable_external_access = false` and `SET lock_configuration = true`.

The cache holds no application state and is rebuilt from the replica, which is what
`analytics-ai.md` already allows DuckDB files to be.

### Verify before building on it

- A DuckDB file opened `read_only=True` stays queryable after
  `SET enable_external_access = false` on that connection. Experiment 2 used in-memory tables,
  not a file. If it does not hold, fall back to: open the cache file read-only, copy its tables
  into a second in-memory instance with `ATTACH` + `CREATE TABLE … AS`, `DETACH`, then lock.
  Plain DuckDB `ATTACH` was refused after locking in experiment 2, so the fallback is closed too.
- Column types in the cache equal the types the SQLite attach reports (the CTAS should preserve
  them: `amount` arrives as `DOUBLE`, `datetime` as `TIMESTAMP`). `queries/*.sql` are not run on
  the restricted connection, but a model's SQL written against the described schema must work.

## Changes

| File | Change |
|---|---|
| `src/dinary_analytics/paths.py` | `LEDGER_CACHE_PATH` |
| `src/dinary_analytics/connection.py` | `build_ledger_cache()`, `open_ledger_restricted()` |
| `src/dinary_analytics/ai_service.py` | `_run_query()` uses the restricted connection |
| `src/dinary_analytics/notebooks/dashboard.py` | the `query_ledger` tool uses the restricted connection; `query_summary` stays on the trusted one (packaged SQL) |
| `src/dinary_analytics/backup.py` | confirm the cache file is not part of the analytics backup |

## Tests (`tests/analytics/test_connection.py`, `test_ai_service.py`, `test_dashboard.py`)

Fixture: a replica with ledger tables plus `llmbroker_secrets` holding a marker value.

- `SELECT * FROM ledger.expenses` works on the restricted connection.
- Each of these raises on the restricted connection, and the marker value never appears in an
  error message: `SELECT * FROM ledger.llmbroker_secrets`; `ATTACH '<replica>' (TYPE sqlite)`;
  `sqlite_scan('<replica>', 'llmbroker_secrets')`; `read_text('<replica>')`; `LOAD sqlite`;
  `SET enable_external_access = true`; `COPY ledger.expenses TO '<tmp>'`.
- `duckdb_tables()` on the restricted connection lists exactly the `LEDGER_TABLES` present.
- The cache is rebuilt after the replica file changes and reused when it has not.
- The MCP `query` tool and the dashboard `query_ledger` tool both refuse the secrets query.

## When done

Add to `specs/reference/analytics-ai.md`, as an invariant: SQL written by a model runs against
the bookkeeping tables only and cannot open any other file; the snapshot itself is a full copy
and the replica file is to be treated as holding the server's secrets.

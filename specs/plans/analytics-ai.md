# Analytics — standalone app (dinary-analytics)

## Status

MVP complete. Full implementation pending.

## Scope

A standalone application installed locally by each user. Opens a browser dashboard
(Marimo in `run` mode — no code visible), provides a natural-language AI chat, and
connects to the dinary ledger via a local read-only replica. Non-technical users
see a clean web app; power users additionally connect Claude Code or Claude Desktop
via the MCP server.

Out of scope: OLTP writes, sheet logging, imports, migrations, API surface.

## Repository placement

`src/dinary_analytics/` alongside `src/dinary/` in the monorepo root. One-way
dependency: `dinary_analytics` imports from `dinary`; `dinary` never imports
from `dinary_analytics`. Heavy deps (DuckDB, Polars, Marimo, LLM SDKs) live in
the `analytics` dependency group in the root `pyproject.toml`.

`uv sync` on the laptop installs everything. The deploy task runs
`uv sync --no-dev --no-group analytics` on VM 1, keeping the server image lean.

## Package structure

```
src/dinary_analytics/
  connection.py       # open_ledger(): DuckDB ATTACH ledger-replica.db READ_ONLY
  mcp_server.py       # MCP server: DuckDB queries + analytics.db config writes
  settings.py         # read/write analytics.db (LMDB)
  backup.py           # analytics.db backup/restore CLI
  queries/            # named .sql files for reusable analytical queries
  notebooks/          # template Marimo notebooks, committed to git
    dashboard.py      # main app: configurable widgets + Gemini chat
    events.py         # event/trip cost breakdown
    tags.py           # tag-bucket comparison
```

## Runtime directory

`.analytics/` at the repo root, gitignored. Created on first `inv analytics`.

```
.analytics/
  ledger-replica.db     # read-only SQLite replica of dinary ledger
  analytics.db          # app database: configs, history (PoloDB or LMDB)
```

Backup = copy of `analytics.db`. `ledger-replica.db` is not backed up — it is
regenerated from the server on any `inv analytics` run.

## Storage

### ledger-replica.db

Read-only SQLite replica of the dinary server DB. Synced automatically on every
`inv analytics` run before Marimo starts. DuckDB opens it with `ATTACH ...
(READ_ONLY)` — never writable.

### analytics.db — LMDB

`analytics.db` holds:
- Dashboard configurations (widget list, order, parameters).
- Tag bucket definitions (which tags → which bucket; which stay in baseline).
- LLM conversation history (append-only).

DuckDB never stores application data. Any materialized caches are DuckDB-local,
disposable, and regenerated from the replica.

## SQL-first design

Analytical business logic lives in SQL executed by DuckDB, not in Python
DataFrames. Python owns orchestration, plotting, and LLM glue. This keeps queries
portable to DuckDB-WASM (Tier 2, deferred) without rewrite.

## LLM strategy

Two tiers configured in `analytics.db`:

**Default — Gemini Free API.** Built into the Marimo dashboard chat. User provides
a Google AI Studio API key (free tier). The model receives the ledger schema and
executes DuckDB queries via tool calls to answer questions.

**Power users — Claude Code / Claude Desktop via MCP.** User connects their Claude
subscription to the `dinary-analytics` MCP server. No per-token cost. Claude can
answer arbitrary questions AND reconfigure dashboards (tag buckets, pinned events,
widget order) by writing to `analytics.db` — without the user editing code or
config files. This is the primary interface for technically proficient users.

## inv analytics

Single entry point. On every run:

1. Syncs `ledger-replica.db` from the dinary server.
2. Starts the MCP server.
3. Opens `notebooks/dashboard.py` via `marimo run`.

## MCP server

`dinary_analytics.mcp_server` exposes:

- `query(sql)` — executes a read-only DuckDB query against the ledger replica,
  returns JSON.
- `schema()` — returns ledger schema for LLM context.
- `get_config(key)` — reads a config entry from `analytics.db`.
- `set_config(key, value)` — writes a config entry to `analytics.db`.

`set_config` is the only write path to `analytics.db`. Marimo uses it internally
for dashboard persistence; Claude Code / Claude Desktop connect to it externally.

## PWA integration

The standalone app syncs tag bucket definitions and pinned events to the PWA by
calling `PUT /api/analytics/config` on the dinary server (see `analytics-pwa.md`).
This updates the PWA embedded view without code changes.

## Runtime tiers

**Tier 1 — laptop/desktop (primary, current target).** `inv analytics` opens
Marimo in the browser. Full AI integration. Zero extra services.

**Tier 2 — DuckDB-WASM in browser (deferred).** Pre-approved additive path for
phone or sharing use cases. SQL-first design keeps this achievable without rewrite.
Building blocks: DuckDB-WASM, Evidence.dev or Marimo-WASM, SQLite replica over
HTTPS Range requests.

## Invariants

- Analytics is strictly read-only against `ledger.*`. `ATTACH (READ_ONLY)` enforced
  at connection level.
- `dinary` server package never depends on `dinary-analytics`.
- DuckDB is a query engine only — no application state stored there.
- `analytics.db` is the sole write target for configs and history.
- `analytics.db` must be backed up. `ledger-replica.db` is reproducible and
  excluded from backup.

## Deliverables

### Phase 0 — DB selection ✓ LMDB

### MVP ✓ DONE

End-to-end slice covering all three layers (data / chat / MCP) in minimal form.
Goal: validate the full stack is viable before investing in polish.

1. `analytics` dependency group in root `pyproject.toml`. ✓
2. `connection.open_ledger()` + replica sync logic. ✓
3. `settings.py` with `analytics.db` (DB chosen in Phase 0). ✓
4. `dashboard.py` — one chart block + Gemini chat (`query` and `schema` tool calls). ✓
5. MCP server — `query` and `schema` tools only. ✓
6. `inv analytics`: syncs replica, starts MCP server, opens `dashboard.py`. ✓

### Full implementation

7. Template notebooks: `events.py`, `tags.py`.
8. `dashboard.py` extended to full configurable widget set.
9. MCP server extended with `get_config` and `set_config`.
10. `PUT /api/analytics/config` endpoint in dinary server + `analytics_pwa_config`
    migration (may land in a separate PR as part of `analytics-pwa.md` work).

### Post-completion

Create `specs/plans/analytics-followup.md` (empty skeleton) for future
improvements backlog.

# Analytics standalone app

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
  connection.py       # read-only DuckDB ATTACH to ledger-replica.db
  mcp_server.py       # MCP server: DuckDB queries + analytics.db writes
  settings.py         # analytics.db read/write (LMDB)
  backup.py           # analytics.db backup/restore CLI
  queries/            # named .sql files for reusable analytical queries
  notebooks/
    dashboard.py      # main app: charts + Gemini chat + AI views
    events.py         # event/trip cost breakdown
    tags.py           # tag-bucket comparison
```

## Runtime directory

`.analytics/` at the repo root, gitignored. Created on first `inv analytics`.

```
.analytics/
  ledger-replica.db     # read-only SQLite replica of dinary ledger
  analytics.db          # app database: view configs, history (LMDB)
```

## Storage

### ledger-replica.db

Read-only SQLite replica of the dinary server DB. Synced on every `inv analytics`
run before Marimo starts. DuckDB opens it with `ATTACH ... (READ_ONLY)` — never
writable.

### analytics.db — LMDB

Holds: analytics view configs, dashboard configurations, LLM conversation history.
DuckDB never stores application state; any materialized caches there are disposable
and regenerated from the replica.

`analytics.db` must be backed up. `ledger-replica.db` is reproducible and excluded
from backup.

## SQL-first design

Analytical business logic lives in SQL executed by DuckDB, not in Python DataFrames.
Python owns orchestration, plotting, and LLM glue. Keeps queries portable to
DuckDB-WASM without rewrite.

## LLM strategy

**Default — Gemini Free API.** Built into the Marimo dashboard chat. User provides
a Google AI Studio API key. The model receives the ledger schema and executes DuckDB
queries via tool calls.

**Power users — Claude Code / Claude Desktop via MCP.** User connects their Claude
subscription to the `dinary-analytics` MCP server. Claude can answer arbitrary
questions and reconfigure dashboards (view configs, widget order) by writing to
`analytics.db`.

## inv analytics

Single entry point. On every run:

1. Syncs `ledger-replica.db` from the dinary server.
2. Starts the MCP server.
3. Opens `notebooks/dashboard.py` via `marimo run`.

## MCP server

- `query(sql)` — read-only DuckDB query against the ledger replica, returns JSON.
- `schema()` — ledger schema for LLM context.
- `get_config(key)` — reads a config entry from `analytics.db`.
- `set_config(key, value)` — writes a config entry to `analytics.db`.
- `list_views()`, `get_view(id)`, `save_view(config)`, `delete_view(id)` — manage analytics view configs.

`set_config` and the view management tools are the only write paths to `analytics.db`.

## PWA integration

View configs and tag bucket definitions sync to the PWA via `PUT /api/analytics/config`
on the dinary server (see `analytics-pwa.md`).

## Runtime tiers

**Tier 1 — laptop/desktop (primary).** `inv analytics` opens Marimo in the browser.
Full AI integration. Zero extra services.

**Tier 2 — DuckDB-WASM in browser (deferred).** SQL-first design keeps this
achievable without rewrite.

## Invariants

- Analytics is strictly read-only against `ledger.*`. `ATTACH (READ_ONLY)` enforced at connection level.
- `dinary` server package never depends on `dinary_analytics`.
- DuckDB is a query engine only — no application state stored there.
- `analytics.db` is the sole write target for configs and history.

---

## Analytics Views

### Concept

An analytics view is a named, reusable grouping configuration that organises expenses
into user-defined baskets for charting. Each view is live: when opened it re-executes
against current ledger data. The user selects the time period at open time.

Multiple views can be saved, renamed, copied, and deleted.

### Basket structure

A view contains an ordered list of baskets plus a default basket name for unmatched
expenses. A basket matches an expense if any of its trigger conditions are met (OR
logic within triggers). Priority is first-match: an expense is assigned to the first
basket whose triggers match it.

Basket triggers:
- `events` — list of event IDs: matches any expense belonging to those events.
- `tags` — list of tag IDs: matches any expense carrying any of those tags.

Within each basket the breakdown by category group is always available as a
drill-down.

View config stored in `analytics.db` under key `view:<uuid>`:

```json
{
  "id": "<uuid>",
  "name": "По смыслу жизни",
  "baskets": [
    { "name": "Релокация", "triggers": { "events": [3], "tags": [] } },
    { "name": "Путешествия", "triggers": { "events": [], "tags": [7] } }
  ],
  "default_basket": "Основное",
  "chart_type": "stacked_bar_monthly"
}
```

### LLM interaction model

The LLM is the analyst; the user is the client who reacts to proposals.

**Creating a new view.** The LLM calls `query_spending_summary()` to examine actual
spending patterns before saying anything. It then presents an initial basket proposal
with a rendered chart and justifies each basket with concrete data: "Релокация —
45k за 3 мес (40% квартала), выделил в отдельный блок." The user never has to name
categories, events, or tags.

**Refining a view.** The user expresses dissatisfaction about what they see in the
chart ("поездки выглядят странно"). The LLM consults the data and proposes specific
alternatives: "Могу объединить все поездки в один блок Путешествия — тогда видна
общая сумма за год. Или оставить детализацию с итоговой строкой. Что ближе?" The
user only approves or redirects; the LLM modifies the config via in-session tools
and the chart re-renders immediately.

Users never compose basket definitions by naming categories or writing rules. The
LLM does this from data.

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
  connection.py       # read-only DuckDB ATTACH to the ledger replica
  ai_service.py       # background app: MCP server + health/refresh-now HTTP routes + replica-refresh daemon
  settings.py         # analytics.db read/write (LMDB)
  backup.py           # analytics.db backup/restore CLI
  queries/            # named .sql files for reusable analytical queries
  notebooks/
    dashboard.py      # main app: charts + Gemini chat + AI views
    events.py         # event/trip cost breakdown
    tags.py           # tag-bucket comparison
```

## Runtime directory

Both files live side by side in the platform-specific local app-data directory
(e.g. `~/Library/Application Support/dinary/` on macOS):

```
  dinary-ai.db          # read-only SQLite replica of the dinary ledger, refreshed by the daemon
  analytics.db          # app database: view configs, history (LMDB)
```

## Storage

### Ledger replica

Read-only SQLite replica of the dinary server DB, refreshed by a background daemon
that periodically downloads a consistent snapshot over HTTP from the dinary server —
on its own schedule, independently of `inv analytics`, and on demand via the
dashboard's refresh control. DuckDB opens it with `ATTACH ... (READ_ONLY)` — never
writable.

### analytics.db — LMDB

Holds: analytics view configs, dashboard configurations, LLM conversation history.
DuckDB never stores application state; any materialized caches there are disposable
and regenerated from the replica.

`analytics.db` must be backed up. The ledger replica is reproducible and excluded
from backup.

## SQL-first design

Analytical business logic lives in SQL executed by DuckDB, not in Python DataFrames.
Python owns orchestration, plotting, and LLM glue. Keeps queries portable to
DuckDB-WASM without rewrite.

## LLM strategy

**Default — shared provider pool.** The Marimo dashboard chat uses the same
OpenAI-compatible providers as the receipt pipeline, read from
`.deploy/llm_providers.toml` (path overridable via `DINARY_LLM_PROVIDERS_FILE`).
Providers are tried in declaration order; a rate-limited (429/503) provider is
skipped for the next. The request/response transport and tool-calling loop are
shared with the server's LLM broker — analytics never calls the running server.
The model receives the ledger schema and executes DuckDB queries via tool calls.

**Power users — Claude Code / Claude Desktop via MCP.** User connects their Claude
subscription to the `dinary-analytics` MCP server. Claude can answer arbitrary
questions and reconfigure dashboards (view configs, widget order) by writing to
`analytics.db`.

## inv analytics

Single entry point. Ensures `dinary-ai` is reachable — installing and starting it
via `setup-dinary-ai` if it isn't already running — then opens
`notebooks/dashboard.py` via `marimo run`. Refreshing the ledger replica is the
background daemon's responsibility, not a step of this flow; the dashboard surfaces
replica readiness and lets the user trigger a refresh on demand.

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

### Basket design goal

The goal of a basket set is to surface non-obvious, actionable spending patterns —
not to confirm obvious ones (e.g. rent dominates). A good top-level set has 5–10
baskets where each basket is both meaningful in share and something the user can
actually track or influence.

The LLM starts from PWA category groups as a baseline and reorganises freely:
splitting a group, merging negligible ones, or extracting a cross-cutting basket
(e.g. "Travel" by tag, "Relocation" by event). Items that are each individually
negligible and not worth tracking separately are merged into the default basket.

Drill-down into any basket produces a second-level basket set (5–10 items) breaking
down that basket by sub-category, sub-tag, or sub-event, generated by the LLM on
demand using the same methodology.

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

The LLM is the analyst; the user is the client who reacts to proposals. There is a
single conversational surface: the dashboard chat. Everything — proposing, refining,
and saving views — happens in that one conversation.

**Starting a dialogue.** Above the conversation sits a row of clickable suggestion
buttons (the obvious entry point is rebuilding spending into baskets). Clicking a
suggestion sends it straight into the same conversation — the user never copies or
retypes a prompt. A free-text box below the log handles everything else. There is no
separate one-shot area.

**Feedback.** Clicking a suggestion or pressing Send immediately echoes the user's
message and shows an "analyzing" indicator while the reply is generated.

**Creating a view.** The LLM calls `query_summary()` to examine actual
spending patterns before saying anything. It then presents an initial basket proposal;
the draft chart renders inline directly under that reply, captioned as a draft to pin,
and the reply justifies each basket with concrete data: "Релокация — 45k за 3 мес
(40% квартала), выделил в отдельный блок." Each reply offers 3–5 follow-up questions
as clickable buttons (the LLM emits them via a tool, not as prose); clicking one sends
it straight into the conversation. The user never has to name categories, events, or tags.

**Refining a view.** The user reacts to what they see in the draft chart ("поездки
выглядят странно"). The LLM consults the data, proposes specific alternatives, and on
approval modifies the draft via in-session tools; the chart re-renders immediately.

**Pinning a view.** The live draft becomes permanent either when the user presses the
Pin control beneath the draft or when the LLM calls its save tool on request. Pinned
views appear as a gallery of live cards on the dashboard; each card re-executes against
current ledger data for the selected period and can be reopened as a draft or deleted.

Users never compose basket definitions by naming categories or writing rules. The
LLM does this from data.

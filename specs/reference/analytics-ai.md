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
  connection.py       # read-only DuckDB ATTACH to the ledger replica; ledger schema and currency
  ai_service.py       # background app: MCP server + health/refresh-now HTTP routes
  refresh.py          # replica-refresh daemon
  llm.py              # chat turn against the paid model
  views.py            # basket view data
  charts.py           # chart builders
  settings.py         # analytics.db read/write (LMDB)
  backup.py           # analytics.db backup/restore CLI
  queries/            # named .sql files for reusable analytical queries
  notebooks/
    dashboard.py      # the one dashboard: charts + AI chat + basket views
```

## Runtime directory

These files live side by side in the platform-specific local app-data directory
(e.g. `~/Library/Application Support/dinary/` on macOS):

```
  dinary-ai.db          # read-only SQLite replica of the dinary ledger, refreshed by the daemon
  analytics.db          # app database: pinned views, dashboard selections (LMDB)
  ledger-cache.duckdb   # bookkeeping tables only, rebuilt from the replica; what model-written SQL reads
```

## Storage

### Ledger replica

Read-only SQLite replica of the dinary server DB, refreshed by a background daemon
that periodically downloads a consistent snapshot over HTTP from the dinary server —
on its own schedule, independently of `inv analytics`, and on demand via the
dashboard's refresh control. DuckDB opens it with `ATTACH ... (READ_ONLY)` — never
writable.

### analytics.db — LMDB

Holds the pinned analytics views and the dashboard's remembered selections. The chat
conversation lives in the open dashboard page and ends with it; what the user wants
to keep from a conversation is a pinned view.

DuckDB never stores application state; any materialized caches there are disposable
and regenerated from the replica.

`analytics.db` must be backed up. The ledger replica and its cache are reproducible and excluded
from backup.

## SQL-first design

Analytical business logic lives in SQL executed by DuckDB, not in Python DataFrames.
Python owns orchestration, plotting, and LLM glue. Keeps queries portable to
DuckDB-WASM without rewrite.

## LLM strategy

**Default — one paid model, called directly.** The Marimo dashboard chat does not use
the free provider pool the receipt pipeline runs on. It calls one model from
llmbroker's curated paid catalog by the `"gpt-fast"` alias, so a new model version
reaches the chat without a dinary change. There is no failover: a rate limit or an
outage of that model is reported in the chat and the user retries. The model's key
resolves from the dashboard process's environment, which the launcher fills from the
deploy env file, so analytics reads neither the server nor its database. The
request/response transport and tool-calling loop are shared with the server's LLM
broker. The model executes DuckDB queries via tool calls.

**What the model is told about the ledger.** The schema of the bookkeeping tables —
expenses, categories, category groups, events, tags, income, exchange rates, stores,
shop chains, receipts and receipt items — and the ledger's accounting currency. Both are
read from the replica itself, so the description always matches the data the queries run
against.

Money by store or by receipt item is always taken from expense amounts, which are in the
accounting currency; receipt totals and item prices are in the receipt's own currency and
are never added to them. Money by store sums the expenses booked at that store. Money by
item takes the expense the item was booked into and scales it by the item's price over the
amount the user entered for that expense. The lines of one expense sum exactly to that
entered amount, so the shares add back up to the expense, and an item's share stays the
same whatever other items a question filters out.

The receipt's fiscal-portal link identifies the receipt on the tax authority's site and is
not needed for analysis, so model-written SQL cannot read it and the schema given to the
model does not show it.

**What leaves the machine.** The charts are computed locally and send nothing. The
chat sends the user's question, the ledger description above and the results of the
queries the model runs to the model's provider. An external MCP client does the same
with whatever provider it uses.

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

Runs inside the background service, so it is available whether or not the dashboard is
open. It gives an external client four things: a read-only DuckDB query against the
ledger replica, the same ledger description the dashboard chat gets, the pinned views
(list, read, save, delete), and the dashboard's remembered selections (read, write).

Saving or deleting a view and writing a selection are the only write paths to
`analytics.db`.

## Currency

Every amount on the dashboard and in the chat is in the ledger's accounting currency
and is labelled with it. The currency is read from the replica.

## Runtime tiers

**Tier 1 — laptop/desktop (primary).** `inv analytics` opens Marimo in the browser.
Full AI integration. Zero extra services.

**Tier 2 — DuckDB-WASM in browser (deferred).** SQL-first design keeps this
achievable without rewrite.

## Invariants

- Analytics is strictly read-only against `ledger.*`. `ATTACH (READ_ONLY)` enforced at connection level.
- `dinary` server package never depends on `dinary_analytics`.
- DuckDB is a query engine only — no application state stored there.
- `analytics.db` is the sole write target for pinned views and dashboard selections.
- SQL written by a model, in the dashboard chat or through an MCP client, runs against the
  bookkeeping tables only: it cannot see any other table, read or write any file, or load
  an extension. The snapshot itself is a full copy of the server database, so the replica
  file is to be treated as holding the server's secrets.

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

A basket collects expenses by category, by category group, by event or by tag. The
analyst starts from the category groups as a baseline and reorganises them: splits a
large group into its categories, merges negligible ones, and pulls out cross-cutting
baskets such as "Travel" by tag or "Relocation" by event. Categories and groups matter
because a large share of spend carries neither an event nor a tag, and no event or tag
basket can ever split it; the analyst is told how large that share is. Items that are
each individually negligible and not worth tracking separately are left to the default
basket, which also receives every expense no basket matches.

### Basket structure

A view contains an ordered list of baskets plus a default basket name for unmatched
expenses. A basket matches an expense if any of its trigger conditions are met (OR
logic within triggers). Priority is first-match: an expense is assigned to the first
basket whose triggers match it.

Every basket of a view, the default basket included, has a distinct name: the chart
shows one bar per name, and a breakdown must split exactly what that bar shows.

A basket's triggers are four lists, any of which may be empty or absent:
- events: matches any expense belonging to one of those events;
- tags: matches any expense carrying one of those tags;
- categories: matches any expense in one of those categories;
- category groups: matches any expense whose category is currently in one of those
  groups.

Categories and groups are referenced by identity, never by name, so a rename or a
switch of category template leaves a view intact. A group basket follows the category's
current group: moving a category to another group moves its whole history between group
baskets. A view that must not shift that way lists categories instead of groups.

Because priority is first-match, the order of baskets is the analyst's tool for
cross-cutting baskets: "Travel" by tag placed before "Food" by category takes the meals
eaten while travelling, and placed after it gets only the travel spend that is not food.

Under each basket chart the fixed breakdown by category group stays available. It costs
no model call and answers the common question; the analyst's drill-down is for when it
is not enough.

Each view is stored in `analytics.db` with its baskets, default basket, chart type and,
for a breakdown, its scope.

### Drill-down

From any basket of the draft the user can ask to break it down, either by typing or
through the basket's "Break down" control, which sends that request into the
conversation. The analyst examines that basket's own expenses with the same data-first
method and proposes a second-level set of 5–10 baskets, by category, tag or event, that
covers only that basket.

A breakdown is a view with a scope. The scope keeps exactly the expenses the parent view
put in the drilled basket: those that match it and none of the baskets ahead of it, so a
breakdown of "Food" never re-includes the travel meals "Travel" took. Breaking down the
default basket keeps the expenses none of the parent's baskets matched — usually the most
valuable case. Expenses outside the scope are left out of the breakdown entirely, not
sent to its default basket, so a breakdown's totals add up to the parent basket's total.
Scopes compose, so a breakdown can itself be broken down, but the analyst goes one level
at a time.

The page remembers the chain of parent drafts, and a "back" control (or the analyst, on
request) returns to the parent. Refining a breakdown keeps its scope; starting a new
top-level view drops it. Pinning a breakdown saves it with its scope, and its chart title
leads with the scope's name, e.g. "Food ▸ by category".

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
and the reply justifies each basket with concrete data: "Relocation — 45k over 3 months
(40% of quarter), moved to a separate block." Each reply offers 3–5 follow-up questions
as clickable buttons (the LLM emits them via a tool, not as prose); clicking one sends
it straight into the conversation. The user never has to name categories, events, or tags.

**Refining a view.** The user reacts to what they see in the draft chart ("travel looks off"). The LLM consults the data, proposes specific alternatives, and on
approval modifies the draft via in-session tools; the chart re-renders immediately.

**Pinning a view.** The live draft becomes permanent either when the user presses the
Pin control beneath the draft or when the LLM calls its save tool on request. Pinned
views appear as a gallery of live cards on the dashboard; each card re-executes against
current ledger data for the selected period and can be reopened as a draft or deleted.

Users never compose basket definitions by naming categories or writing rules. The
LLM does this from data.

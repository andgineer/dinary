# Development

## Prerequisites

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Node.js 22+, then:

```bash
uv sync
cp -r .deploy.example .deploy   # .deploy/ is gitignored — edit as needed
```

To run the **full** test suite (`inv test` / `uv run pytest`), install every
dependency group plus two CLI tools the backup/restore tests shell out to.
The idempotent helper does both:

```bash
bash scripts/setup-test-env.sh
```

Equivalent to `uv sync --all-groups` (adds the `analytics` group — duckdb,
marimo, polars, …) plus `apt-get install -y zstd sqlite3`. Without the
`analytics` group the `tests/analytics/` suite fails to collect; without
`zstd`/`sqlite3` the backup-restore tests fail.

Credentials are read from `~/.config/gspread/service_account.json`.
Don't have a service account key yet? See [Google Sheets Setup](google-sheets-setup.md).

## Running the server

```bash
inv dev   # starts at http://127.0.0.1:8000 with uvicorn --reload
```

Migrations run automatically on first start. The DB is created at `data/dinary.db`.

### Useful flags

| Flag | Effect |
|---|---|
| `--reset` | Wipe `data/dinary.db`, re-create schema, re-seed catalog. Use for a clean slate or after changing `seed_config.py` — not for testing migration correctness against existing data. |
| `--rebuild` | Rebuild the PWA from `webapp/` before starting. |
| `--sheet-logging` | Enable Google Sheets logging (off by default so test expenses don't leak into the prod spreadsheet). |
| `--port N` | Listen on a different port (default 8000). |

## PWA development

The PWA is a Vue 3 + Pinia app under `webapp/`, built with Vite into `_static/` (gitignored).
FastAPI serves the built assets from the same origin as the API.

After editing anything under `webapp/`:

```bash
inv dev --rebuild              # rebuild + restart in one step
# or separately:
inv build-static               # full rebuild (npm ci + vite build)
inv build-static --skip-install  # faster: skip npm ci after first run
```

For hot-module replacement while iterating on Vue, run the Vite dev server in a second terminal.
It proxies `/api` calls to FastAPI on port 8000:

```bash
npm --prefix webapp run dev   # http://127.0.0.1:5173
```

!!! note
    `vite dev` does not register a service worker. Test offline and PWA behavior against a real
    `_static/` build (run `inv build-static`, then open FastAPI on port 8000).

## Working with production data

To run locally against a copy of prod data:

```bash
inv restore-primary   # download a live snapshot from VM1 into data/dinary.db
inv dev               # do NOT use --reset; that would wipe the snapshot
```

## Tests and code quality

```bash
inv test   # Python + JavaScript tests
inv pre    # pre-commit checks (ruff, pyrefly, yaml, …)
```

## Project specs

Internal design documents live in `specs/` (not published to the docs site):

| Directory | Contents |
|---|---|
| `specs/architecture/` | System architecture, data model, design decisions, trade-off comparisons |
| `specs/reference/` | Domain reference: catalog API contract, currencies, exchange rates, LLM providers, receipt fetching, Google Sheets integration, SQL tool |
| `specs/ui/` | PWA visual language, component catalogue, screen specs, interaction patterns |
| `specs/plans/` | Implementation plans (active) and completed-plan records (`*-done.md`) |

When `specs/` and code disagree, **the code wins** — specs describe intent, not ground truth.

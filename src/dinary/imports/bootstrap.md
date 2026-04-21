# Bootstrap import workflow

> **Bootstrap import is optional operator tooling.** If you are not
> migrating historical year-by-year Google Sheets into DuckDB, you do
> not need anything in this directory. See [`README.md`](README.md) for
> the full context and the "when you need this" checklist.

This document is the end-to-end recipe for a one-shot bootstrap
import of historical data. It covers: registering per-year sources,
seeding the taxonomy, importing expenses, importing income,
verification, and the layout-parser reference.

## 0. Prerequisites

- Google service account JSON key configured per
  [`docs/src/en/google-sheets-setup.md`](../../../docs/src/en/google-sheets-setup.md).
- Every source spreadsheet shared with the service account's
  `client_email` (read access is sufficient for bootstrap import).
- `uv` + Python 3.13+ environment from the repo root; all commands
  below are `uv run inv ...` on the laptop, or `inv ...` on the
  server after `inv setup` has finished.

## 1. Register per-year sources

All source spreadsheets are registered in a single operator-local
file at `.deploy/import_sources.json`. Copy the template and edit it:

```bash
cp .deploy.example/import_sources.json .deploy/import_sources.json
$EDITOR .deploy/import_sources.json
```

The file is a JSON array, one object per year. See
[`README.md`](README.md) → Schema reference for the full field list.

Minimal expense-only example:

```json
[
  {"year": 2026, "spreadsheet_id": "1AbC...xyz", "worksheet_name": "Budget 2026"}
]
```

Full expense + income example:

```json
[
  {
    "year": 2022,
    "spreadsheet_id": "1AbC...xyz",
    "worksheet_name": "Budget 2022",
    "layout_key": "rub_fallback",
    "income_worksheet_name": "Balance",
    "income_layout_key": "balance_rub_rsd"
  }
]
```

The layout defaults by year, so you only need `layout_key` /
`income_layout_key` if the auto-guess is wrong. The loader is
mtime-keyed, so edits to the file are picked up without restarting
the server.

## 2. Sync to the deploy host (if deploying)

When running from the laptop against a remote VM, `inv setup` and
`inv deploy` rsync `.deploy/.env` and `.deploy/import_sources.json`
to `/home/ubuntu/dinary-server/.deploy/` on the host. Bootstrap
import tasks then read the file from the server. No separate sync
step is needed — just run `inv setup` once or `inv deploy` on every
config change.

## 3. Seed the taxonomy

Two paths exist, and you pick exactly one per deployment:

- `inv bootstrap-catalog` — seeds the hardcoded runtime taxonomy
  (category groups, categories, tags, events) without consulting any
  Google Sheet. This is the standard path for deployments that will
  not do bootstrap import.
- `inv import-catalog` — rebuilds the catalog **and** the legacy
  `(sheet_category, sheet_group) → 3D` mapping tables from
  `.deploy/import_sources.json`. Use this when you plan to run
  `inv import-budget[-all]`.

`inv import-catalog` is a superset, but it is slower (Google Sheets
round-trips) and requires every registered spreadsheet to be
reachable. `inv bootstrap-catalog` is the right default for everyone
who does not need the legacy mapping rebuild.

Catalog-version bumps are gated on actual change — a no-op reseed
leaves `app_metadata.catalog_version` untouched so the PWA's
ETag-validated `GET /api/catalog` keeps serving `304 Not Modified`.

## 4. Import expenses

```bash
inv import-budget --year=2022 --yes      # one year (destructive: DELETE year, re-insert)
inv import-budget-all --yes              # every year present in .deploy/import_sources.json
```

`--yes` is required because the task wipes the target year inside
`expenses` and re-inserts from scratch. Idempotency of bootstrap
rows is achieved by regenerating the `client_expense_id` UUIDs
deterministically from the legacy row contents.

The `-all` task reads the year list directly from
`.deploy/import_sources.json` via `dinary.config.read_import_sources`,
not from a DuckDB table. If the file is missing or empty the task
exits immediately with a clear error pointing back here.

## 5. Import income

```bash
inv import-income --year=2022 --yes
inv import-income-all --yes
```

Only years whose entry has `income_worksheet_name` are imported;
years without structured income data (e.g. 2012–2018) are skipped.
Income amounts are stored in the configured accounting currency
(`DINARY_ACCOUNTING_CURRENCY`, default `EUR`) using NBS middle rates
for the 1st of each month. See [`.plans/income.md`](../../../.plans/income.md)
for the full layout reference (`balance_rub`, `balance_rub_rsd`,
`balance_rsd`, `income_rsd`) and the mid-2022 RUB→RSD transition.

## 6. Verify

```bash
inv verify-bootstrap-import --year=2022
inv verify-bootstrap-import-all
inv verify-income-equivalence --year=2022
inv verify-income-equivalence-all
inv report-2d-3d
```

- `verify-bootstrap-import` re-reads the sheet, re-runs the 3D
  mapping, and asserts that DuckDB rows match the resolved
  `(category_id, event_id, tag set)` for every non-skipped legacy
  row.
- `verify-income-equivalence` re-aggregates the income worksheet in
  the accounting currency and compares month-by-month to DuckDB's
  `income` table with a ±0.02 tolerance.
- `report-2d-3d` renders a cross-year grid of every legacy
  `(sheet_category, sheet_group)` pair against its resolved 3D
  tuple — useful for spotting mapping gaps before a production
  import.

All verification tasks read the year list from
`.deploy/import_sources.json`; they treat a missing file as "no
years to verify" and exit 0, so they are safe to run on a
non-bootstrap deployment.

## 7. Coordinated full reset

For a clean rebuild across every registered year, the operator
runbook is:

```bash
inv backup                              # rsync data/ to laptop
inv stop                                # stop the FastAPI service
inv reset-db --yes                      # drop + re-run migrations
inv import-catalog --yes                # seed catalog + mappings
inv import-budget-all --yes
inv import-income-all --yes
inv verify-bootstrap-import-all
inv verify-income-equivalence-all
inv start                               # bring the service back
```

Keep the service stopped for the entire reset — DuckDB allows only
one writer per file between processes, and bootstrap import holds
the write slot for non-trivial durations.

## Layouts (expense import)

The `layout_key` selects a `SheetLayout` in
`src/dinary/imports/expense_import.py`. Known values and their
default year ranges:

| `layout_key`   | Default year range | Notes                                         |
|----------------|--------------------|-----------------------------------------------|
| `rub_2012`     | 2012               | Earliest legacy layout (RUB).                 |
| `rub_2014`     | 2013–2015          | Transitional (RUB).                           |
| `rub_2016`     | 2016–2018          | Expanded envelope columns (RUB).              |
| `rub_6col`     | 2019–2021          | Six-column split (RUB).                       |
| `rub_fallback` | 2022               | Mid-year RUB → RSD transition year.           |
| `default`      | 2023+              | Current RSD layout.                           |

Override only when the auto-guess is wrong for your sheet. Year-by-year
defaults live in `dinary.config._default_layout_for_year`.

## Layouts (income import)

The `income_layout_key` selects an `IncomeLayout` in
`src/dinary/imports/income_import.py`:

| `income_layout_key`   | Currency rule                              |
|-----------------------|--------------------------------------------|
| `balance_rub`         | All months in RUB.                         |
| `balance_rub_rsd`     | Months 1–7 in RUB, 8–12 in RSD (2022).     |
| `balance_rsd`         | All months in RSD.                         |
| `income_rsd`          | All months in RSD (current "Income" tab).  |

There is no auto-guess for income layouts — either set
`income_layout_key` explicitly or the year is skipped at import time.

## Non-goals

- **Two-way sync.** Bootstrap import is a one-shot data lift. Runtime
  sheet logging (Phase 2+) is append-only and intentionally coarser
  than bootstrap: a round-trip through logging is not guaranteed to
  recover the original 3D tuple.
- **Ingesting arbitrary third-party sheet schemas.** The layouts above
  are hand-maintained for the author's historical spreadsheets; there
  is no generic "column mapper" UI.
- **Running bootstrap import in production request paths.** Every
  `inv import-*` task assumes the FastAPI service is stopped and
  holds the DuckDB writer slot for its full duration.

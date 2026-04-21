# Income Import — Implementation Notes

> **Scope.** This document is the deep-dive reference for the historical
> income import only. Everything cross-cutting (the single-file DuckDB
> model, `settings.app_currency` storage semantics, the unified
> `0001_initial_schema.sql` migration stream, FK-safe catalog sync,
> etc.) is owned by [architecture.md](architecture.md) and must be
> consulted first. If this file and `architecture.md` ever disagree,
> `architecture.md` wins.

## Schema

### `data/dinary.duckdb` — table `income`

```sql
CREATE TABLE income (
    year   INTEGER NOT NULL,
    month  INTEGER NOT NULL,
    amount DECIMAL(12,2) NOT NULL,          -- settings.app_currency (default RSD)
    PRIMARY KEY (year, month),
    CHECK (month BETWEEN 1 AND 12)
);
```

Defined in `src/dinary/migrations/0001_initial_schema.sql` alongside every
other ledger and catalog table — the per-year `budget_YYYY.duckdb` /
`config.duckdb` split was removed in the single-file reset, so there is
exactly one migration stream. `income.amount` is stored in the configured
app currency (default `"RSD"`); the column is dimensionless at the schema
level because the app-currency choice is a deployment-wide setting, not a
per-row attribute.

### `.deploy/import_sources.json` — operator-local source registry

```json
[
  {
    "year": 2022,
    "spreadsheet_id": "…",
    "worksheet_name": "Budget 2022",
    "layout_key": "rub_fallback",
    "income_worksheet_name": "Balance",
    "income_layout_key": "balance_rub_rsd"
  }
]
```

The per-year source registry used to live as an `import_sources`
DuckDB table; it now lives as a gitignored JSON file at
`.deploy/import_sources.json`, loaded by
`dinary.config.read_import_sources`. The file is OPTIONAL and only
consumed by `inv import-*` tasks. One record per year carries both
expense and income source metadata; the ``income_*`` fields are
absent or empty for years without a structured income worksheet
(e.g. 2012–2018). See the repo-root `imports/` directory for the
full schema and workflow documentation.

## Layouts

`IncomeLayout` dataclass in `src/dinary/imports/income_import.py` maps sheet columns:

| Layout key | col_date | col_amount | currency | transition |
|------------|----------|------------|----------|------------|
| `balance_rub` | 1 | 2 | RUB | — |
| `balance_rub_rsd` | 1 | 2 | RUB | month ≥ 8 → RSD |
| `balance_rsd` | 1 | 2 | RSD | — |
| `income_rsd` | 1 | 2 | RSD | — |

### Mid-year currency transition (2022)

2022 income was received in RUB through July, then in RSD from August.
`balance_rub_rsd` layout handles this: `transition_month=8`, `transition_currency="RSD"`.
The same column contains amounts in both currencies; the month determines which rate to apply.

## Year → source mapping

Registered in `.deploy/import_sources.json` (one entry per year with
an `income_worksheet_name` populated):

| Year | Worksheet | Layout |
|------|-----------|--------|
| 2019 | Balance | balance_rub |
| 2020 | Balance | balance_rub |
| 2021 | Balance | balance_rub |
| 2022 | Balance | balance_rub_rsd |
| 2023 | Balance | balance_rsd |
| 2024 | Income | income_rsd |
| 2025 | Income | income_rsd |
| 2026 | Income | income_rsd |

Years 2012–2018 have no income source — no structured income data in those sheets.

## Import flow (`import_year_income`)

1. Read `.deploy/import_sources.json` via `dinary.config.get_import_source(year)` → spreadsheet ID, worksheet, layout key.
2. Pre-fetch NBS middle rates for the 1st of each month for every currency
   the layout will need (source currency, EUR for legacy reports, and the
   app currency). Rates are held as `RSD per 1 unit of X` so an identity
   entry (RSD itself, or the app currency when it is RSD) is just
   `Decimal(1)`. This runs inside a short-lived writer cursor so the
   subsequent sheet loop does not hold the DuckDB write slot across
   HTTP round-trips.
3. Open the Google Sheet via `gspread`, read all rows from the income
   worksheet.
4. For each row after `header_rows`:
   - Parse date → extract `(year, month)`. Skip rows where year ≠ target.
   - Parse amount (handles `$`, spaces, commas).
   - Determine currency (base or transition based on month).
   - Convert to the app currency via the pre-fetched rates
     (`amount_original * rate_src / rate_app`). Missing rate → skip row
     with a warning.
5. Aggregate by month.
6. In a single DuckDB transaction: `DELETE FROM income WHERE year = ?`
   then insert one row per `(year, month)`.

## Verification (`verify_income_equivalence`)

Re-reads the sheet, re-aggregates in the app currency, and compares
month-by-month against DB with a ±0.02 tolerance. The result dict uses
`total_sheet_app`, `total_db_app`, and `app_currency` (not the pre-reset
`*_eur` keys).

## Invoke tasks

All destructive income-import tasks require explicit `--yes` confirmation:

- `inv import-income --year=YYYY --yes` — destructive re-import of one year.
- `inv import-income-all --yes` — destructive re-import of every registered year (run as part of the coordinated reset flow).
- `inv verify-income-equivalence --year=YYYY` — verify one year against the source sheet (no `--yes` needed; read-only).

## Historical results (2026-04, EUR snapshot)

The table below is the original import log taken before the single-file
reset switched storage to the app currency; it is kept for reference
since the sheet source values themselves have not changed. The current
DB values are the same totals re-expressed in RSD (via the same NBS
middle rates) and should be re-checked with
`inv verify-income-equivalence-all` after any re-import.

| Year | Months | Total EUR |
|------|--------|-----------|
| 2019 | 12 | 25,520.45 |
| 2020 | 12 | 30,616.14 |
| 2021 | 12 | 30,468.42 |
| 2022 | 12 | 60,722.47 |
| 2023 | 9 | 36,402.40 |
| 2024 | 10 | 43,237.91 |
| 2025 | 10 | 50,334.96 |
| 2026 | 3 | 15,158.71 |

All years passed zero-diff verification at the time of that snapshot.

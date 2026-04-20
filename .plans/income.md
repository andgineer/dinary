# Income Import — Implementation Notes

## Schema

### `budget_YYYY.duckdb` — table `income`

```sql
CREATE TABLE income (
    year   INTEGER NOT NULL,
    month  INTEGER NOT NULL,
    amount DECIMAL(12,2) NOT NULL,          -- EUR
    PRIMARY KEY (year, month),
    CHECK (month BETWEEN 1 AND 12)
);
```

Defined inline in `src/dinary/migrations/budget/0001_initial_schema.sql` (the legacy `0002_income.sql` migration was deleted in the 3D reset). The `origin` column was dropped because `income` is single-source per year (always sheet-imported via `inv import-income-all`).

### `config.duckdb` — `import_sources`

```sql
CREATE TABLE import_sources (
    year                  INTEGER PRIMARY KEY,
    spreadsheet_id        TEXT NOT NULL,
    worksheet_name        TEXT NOT NULL DEFAULT '',
    layout_key            TEXT NOT NULL DEFAULT 'default',
    notes                 TEXT,
    income_worksheet_name TEXT DEFAULT '',
    income_layout_key     TEXT DEFAULT ''
);
```

Created as `sheet_import_sources` in `src/dinary/migrations/config/0001_initial_schema.sql` and renamed to `import_sources` in `src/dinary/migrations/config/0002_logging_and_import_rename.sql` (the legacy `0002_income_sources.sql` ALTER migration was folded into the initial schema in the 3D reset).

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

Registered in `import_sources`:

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

1. Read `import_sources` for the year → get spreadsheet ID, worksheet, layout key.
2. Open the Google Sheet via `gspread`, read all rows from the income worksheet.
3. For each row after `header_rows`:
   - Parse date → extract `(year, month)`. Skip rows where year ≠ target.
   - Parse amount (handles `$`, spaces, commas).
   - Determine currency (base or transition based on month).
   - Convert to EUR via NBS (RSD) or Frankfurter (RUB).
4. Aggregate by month.
5. Delete existing `income` rows for the year, insert new ones.

## Verification (`verify_income_equivalence`)

Re-reads the sheet, re-aggregates, and compares month-by-month against DB with ±0.02 EUR tolerance.

## Invoke tasks

All destructive income-import tasks require explicit `--yes` confirmation:

- `inv import-income --year=YYYY --yes` — destructive re-import of one year.
- `inv import-income-all --yes` — destructive re-import of every registered year (run as part of the coordinated reset flow).
- `inv verify-income-equivalence --year=YYYY` — verify one year against the source sheet (no `--yes` needed; read-only).

## Import results (2026-04)

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

All years passed zero-diff verification.

# Income import reference

> **Internal operator documentation.** This file documents the
> historical Google-Sheets-to-SQLite income import path. It is not
> part of the user/admin docs site under `docs/`.

## Purpose

Dinary stores monthly income in the deployment's accounting currency
(`settings.accounting_currency`, default `EUR`) so savings and
income-vs-expense reports can be computed in the same currency as the
expense ledger.

## Source worksheets

| Period | Source worksheet | Currency |
|--------|------------------|----------|
| 2019-2021 | `Balance` | RUB -> accounting currency |
| 2022 (Jan-Jul) | `Balance` | RUB -> accounting currency |
| 2022 (Aug-Dec) | `Balance` | RSD -> accounting currency |
| 2023 | `Balance` | RSD -> accounting currency |
| 2024-2026 | `Income` | RSD -> accounting currency |

Conversion uses the 1st-of-month rate:

- NBS for RSD-anchored conversions.
- Frankfurter for historical RUB, bridged through `EUR -> RSD` where
  needed.

## Commands

### Import one year

```bash
inv import-income --year=2024 --yes
```

Destructive re-import: deletes the target year's rows from the `income`
table in `data/dinary.db` and inserts fresh monthly totals from the
source worksheet.

### Import all registered years

```bash
inv import-income-all --yes
```

Runs income import for every year that has an income source registered in
`.deploy/import_sources.json`. This is typically used inside the
coordinated reset flow, with the FastAPI service stopped.

### Verify one year

```bash
inv verify-income-equivalence --year=2024
```

Re-reads the source worksheet and compares month-by-month totals against
the SQLite `income` table with a tolerance of `+-0.02` in the
accounting currency.

## Stored shape

The `income` table stores one row per month:

- `year`
- `month`
- `amount` in `settings.accounting_currency`

There is no per-row source marker; the import path assumes at most one
configured income worksheet per year.

## Layout keys

`income_layout_key` selects an `IncomeLayout` from
`src/dinary/imports/income_import.py`:

| Key | Meaning |
|-----|---------|
| `balance_rub` | All months in RUB |
| `balance_rub_rsd` | 2022 transition: months 1-7 in RUB, 8-12 in RSD |
| `balance_rsd` | All months in RSD |
| `income_rsd` | Current `Income` tab, all months in RSD |

## Current operational note

Current income still originates in Google Sheets and is imported
into SQLite through `inv import-income`. The PWA does not yet
provide direct income entry.

# Historical data import (bootstrap)

> **Most operators don't need anything in this directory.**
>
> Runtime dinary-server — the FastAPI service, the PWA, Sheet logging,
> the DuckDB ledger — works perfectly without ever reading any file
> documented here. This directory covers the one-shot **bootstrap
> import** that migrates the author's legacy Google Sheets budgets
> (2012–present) into DuckDB. A fresh deployment that starts from an
> empty DuckDB and enters expenses through the PWA should skip it
> entirely and seed the taxonomy with `inv bootstrap-catalog`.

## When you need this

You need the bootstrap import path only if **all** of these are true:

1. You have historical year-by-year Google Sheets budgets with a
   column layout that matches one of the supported parsers (see
   [`bootstrap.md`](bootstrap.md) → Layouts), **and**
2. You want those historical rows inside DuckDB so cross-year
   analytics works, **and**
3. You are willing to register each year's spreadsheet ID in a
   local, non-committed config file.

Everyone else should stop reading and go to the main README +
[`.plans/architecture.md`](../../../.plans/architecture.md).

## Where things live

| What                                    | Path                                                    | Notes                                            |
|-----------------------------------------|---------------------------------------------------------|--------------------------------------------------|
| Per-year source registry (operator-owned) | `.deploy/import_sources.json`                           | Gitignored. Optional. Consumed only by `inv import-*` / `inv verify-*`. |
| Placeholder template                    | `.deploy.example/import_sources.json`                   | Committed. Contains `REPLACE_WITH_YOUR_SPREADSHEET_ID` placeholders. |
| Loader + schema                         | `src/dinary/config.py` → `read_import_sources`          | mtime-keyed, thread-safe cache.                   |
| Import logic                            | `src/dinary/imports/`                                   | `expense_import.py`, `income_import.py`, `seed.py`, `verify_*.py`, `report_2d_3d.py`. |
| Operator tasks                          | `tasks.py`                                              | `inv import-config`, `inv import-catalog`, `inv import-budget[-all]`, `inv import-income[-all]`, `inv verify-bootstrap-import[-all]`, `inv verify-income-equivalence-all`, `inv report-2d-3d`. |

## Files in this directory

- [`bootstrap.md`](bootstrap.md) — full workflow: copying the template,
  editing it, running the `inv import-*` tasks, layout reference,
  verification.
- [`income.md`](income.md) — income-specific details: source tabs,
  currency transitions, verification semantics, and the current
  year-by-year mapping.

## Common errors

- **`ValueError: year YYYY not in import sources and .deploy/import_sources.json is empty or missing.`**
  You invoked `inv import-budget --year=YYYY` without creating
  `.deploy/import_sources.json`, or the file exists but contains an
  empty JSON array. Copy the template (see
  [`bootstrap.md`](bootstrap.md)) and add an entry for the target year.
- **`ValueError: year YYYY not in import sources. Available years: [...]`**
  `.deploy/import_sources.json` exists and parses, but has no entry
  for the year you asked to import. The bracketed list shows the
  years currently registered; add a new object for `YYYY` (or fix a
  typo in the `year` field) and rerun.
- **`UserWarning: DINARY_IMPORT_SOURCES_JSON is no longer supported and is ignored`**
  The old env var was removed in the 2026-04 reset. Move the payload
  into `.deploy/import_sources.json` as a JSON array (not an env-var
  string) and unset the variable.
- **`RuntimeError: ... JSON array ...`**
  The file parsed but had the wrong shape (e.g. a single object, or
  an object wrapping the rows). The file **must** be a top-level JSON
  array of objects, one per year.

## Schema reference

Per-object fields in `.deploy/import_sources.json`:

| Field                   | Type    | Required | Description |
|-------------------------|---------|----------|-------------|
| `year`                  | int     | yes      | Budget year (positive; e.g. `2026`). |
| `spreadsheet_id`        | string  | yes      | Google Sheets spreadsheet ID (or a URL — the loader accepts both). |
| `worksheet_name`        | string  | no       | Worksheet tab for expense import. Empty → the first visible tab. |
| `layout_key`            | string  | no       | Column-layout parser id. Defaults by year (see `bootstrap.md` → Layouts). Known values: `default`, `rub_6col`, `rub_2016`, `rub_2014`, `rub_2012`, `rub_fallback`. |
| `income_worksheet_name` | string  | no       | Worksheet tab for income import; absence skips income for that year. |
| `income_layout_key`     | string  | no       | Income layout parser id: `balance_rub`, `balance_rub_rsd`, `balance_rsd`, `income_rsd`. |
| `notes`                 | string  | no       | Free-form operator comment; loader ignores it. |

The loader **does not** validate that `spreadsheet_id` resolves to a
real, accessible sheet — that check happens naturally the first time
an `inv import-*` or `inv verify-*` task opens it via gspread, where
a 404 / permission error is the signal that the entry is wrong.

## Why not a DuckDB table?

Up to 2026-04 the per-year registry lived as an `import_sources`
DuckDB table, seeded from a `DINARY_IMPORT_SOURCES_JSON` env var.
Two problems with that:

1. Operator config was stored inside derived DB state, so
   `inv import-catalog` had to snapshot + restore the table across
   the catalog-rebuild transaction. The snapshot dance was complex,
   error-prone, and made the "atomic catalog rebuild" boundary
   fuzzier than it needed to be.
2. A genuinely optional feature (bootstrap import) was wired into
   the shape of the catalog DB. Operators who never imported still
   had an empty table sitting there, and the deployment surface
   pretended bootstrap import was first-class when in reality it is
   a niche operator path.

The file-backed loader collapses both away: config is config,
derived state is derived state, and the absence of the file is the
operational signal "this deployment doesn't do bootstrap import".

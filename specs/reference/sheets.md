# Google Sheets Integration

## Two distinct roles

The sheets layer serves two separate use cases with different requirements:

- **Historical import** — one-time destructive bootstrap. Reads a year's worth of
  expenses from a source spreadsheet and overwrites the corresponding DB rows.
  Does not enqueue sheet-logging jobs because the rows are already in the sheet.
- **Runtime sheet logging** — ongoing append-only drain. Writes new expenses to the
  spreadsheet as they are classified. Enabled when the logging spreadsheet is
  configured.

These paths share sheet-reading utilities but have separate concerns around
idempotency and destructiveness.

## Year-aware row matching

The logging spreadsheet accumulates expenses across multiple years but column G
stores only the month number. When reading back display values over the Sheets
API, the year is dropped from formatted date strings. Column A stores the full
date as a serial value, which must be fetched with the raw-value format to
recover the year. All row-matching helpers operate on year+month pairs, with a
month-only fallback for unit tests and single-year diagnostics.

## Map tab as 3D→2D resolver

Each expense has three classifying dimensions: category, event, and tags.
The destination spreadsheet is organised in 2D (category rows, envelope columns).
The `map` worksheet resolves this: each row specifies a (category, event, tags)
match pattern and the target sheet category and envelope. Rows are evaluated
top-to-bottom; the first match wins for each output dimension.

Fallback when no row matches: use the raw category name as the sheet category
and an empty envelope.

## Atomic reload with error protection

When the map tab is reloaded (e.g. after the operator edits it), the derived DB
tables are swapped atomically. A parse error in the new map tab leaves the
previous tables in place — the system continues operating with the last known
good mapping rather than failing open with an empty or corrupt one.

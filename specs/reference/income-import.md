# Income Import

## Operator-local source registry

Import sources (per-year Google Sheets spreadsheets) are stored in a gitignored
JSON file at `.deploy/import_sources.json`, not in the database. This is
operator-local configuration that varies per deployment and has no business in
the shared schema or repository.

## Mid-year currency transitions

Some years have income received in different currencies across the year (e.g.
RUB through July, RSD from August). The layout system handles this by keying on
the month number within a single worksheet column rather than splitting into
separate columns or separate import passes. The month boundary that triggers the
currency switch is part of the layout configuration for that year.

## Destructive import requires explicit confirmation

All income import tasks that overwrite existing DB rows require an explicit
`--yes` flag. A missing flag is a no-op. This prevents accidental data loss from
a mistyped command or a script that runs without human review.

## Verification against source sheets

After import, a verify task re-reads the source spreadsheet and compares
month-by-month totals against the DB with a small tolerance for floating-point
rounding. This confirms that the DB faithfully represents the sheets, which are
the authoritative historical source.

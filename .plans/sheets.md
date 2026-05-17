# Google Sheets Integration

## Roles

`services.sheets` is shared between two paths:
- **Historical import** (`imports.expense_import`) — read-only, one-time, destructive for the target year in SQLite.
- **Runtime sheet logging** (`services.sheet_logging`) — append-only drain loop; enabled when `DINARY_SHEET_LOGGING_SPREADSHEET` is set.

## Year-aware row matching

The sheet-logging spreadsheet accumulates expenses across multiple calendar
years. Column G stores only the month number (1–12), which is insufficient to
distinguish e.g. January 2026 from January 2027.

Column A stores the first day of the expense's month as `YYYY-MM-DD`
(`USER_ENTERED` format so Sheets parses it as a date serial).
`ws.get_all_values()` returns the *formatted* display string (`"Apr-1"` etc.)
which drops the year, so `fetch_row_years()` fetches column A separately with
`UNFORMATTED_VALUE` to read the underlying serial and decode the year.

All matching helpers (`find_category_row`, `find_month_range`,
`_find_insertion_row`) accept `(target_year, years_by_row)` as a paired
optional argument. When both are absent they fall back to month-only matching
for unit tests and single-year diagnostics.

## Map tab protocol (`sheet_mapping`)

The `map` worksheet tab (name in `settings.sheet_mapping_tab_name`) is the
source of truth for 3D→2D resolution. Its schema:

| Col | Name      | Semantics                                                            |
|-----|-----------|----------------------------------------------------------------------|
| A   | category  | Canonical category name, or `*` for any                             |
| B   | event     | Event name, or `*` for any (including no event)                     |
| C   | tags      | Comma/whitespace-separated names that must ALL be present; `*` = none required |
| D   | Расходы   | Target `sheet_category`; `*`/empty = don't decide here              |
| E   | Конверт   | Target `sheet_group`; same three semantics                           |

Evaluation: rows are scanned top-to-bottom. For each output column (D, E) the
first non-`*` value from a matching row wins; scanning continues for the other
column until both are resolved. Fallback when no row decides: `sheet_category`
→ `categories.name`; `sheet_group` → `""`.

The DB tables `sheet_mapping` / `sheet_mapping_tags` are derived state.
`reload_now()` validates the tab against the live catalog and swaps them
atomically; a parse error leaves the existing tables in place.

## Historical import (`expense_import`)

One-time destructive bootstrap. For a given year it:
1. Wipes existing rows for that year in SQLite (`sheet_logging_jobs` →
   `expense_tags` → `expenses`).
2. Reads every sheet row through `iter_parsed_sheet_rows` (FX conversion,
   layout detection, month filtering).
3. Resolves `(sheet_category, sheet_group, year)` via `import_mapping`
   (year-scoped first, `year=0` fallback), then applies housing/DIY keyword
   heuristics and unions `events.auto_tags`.
4. Inserts with `enqueue_logging=False` (rows are already in the sheet) and
   `client_expense_id=None` (legacy rows have no PWA-generated key; `UNIQUE`
   allows multiple NULLs).
5. Runs post-import fixes (surgical category/event rewrites for known edge cases).

Does not touch `sheet_logging_jobs` and does not bump `catalog_version`.

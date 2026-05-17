"""Google Sheets row utilities: cell parsing, year decoding, row finding.

Year-aware matching: column A stores a date serial; ``get_all_values()``
returns the formatted display string without the year, so ``fetch_row_years()``
reads column A unformatted separately. Pass ``(target_year, years_by_row)``
together or omit both (falls back to month-only matching for tests/single-year callers).
See ``.plans/sheets.md``.
"""

import logging
from datetime import date, timedelta

import gspread
from gspread.utils import ValueRenderOption

logger = logging.getLogger(__name__)

# 1-indexed column numbers matching the Google Sheet layout:
#   A=Date  B=AppCurrency(formula)  C=EUR(formula)  D=Category  E=Group
#   F=Comment  G=Month  H=Rate  J=LastClientExpenseId
# Column I is intentionally skipped (keeps the human-visible A..H block intact).
COL_CATEGORY = 4
COL_GROUP = 5
COL_MONTH = 7

HEADER_ROWS = 1


def _cell(row: list[str], col_1indexed: int) -> str:
    """Safely get a stripped cell value using a 1-indexed column number."""
    idx = col_1indexed - 1
    return row[idx].strip() if len(row) > idx else ""


def _is_numeric(value: str) -> bool:
    if not value:
        return False
    cleaned = value.replace(",", ".").replace(" ", "")
    try:
        float(cleaned)
    except ValueError:
        return False
    return True


def fmt_amount(amount: float) -> str:
    """Format as integer when possible, e.g. 1500.0 → '1500'."""
    return str(int(amount)) if amount == int(amount) else f"{amount:.2f}"


# Google Sheets stores dates as days since 1899-12-30 (the "1900 date system"
# inherited from Excel with the Lotus 1-2-3 leap-year quirk).
_SHEETS_EPOCH = date(1899, 12, 30)


def _year_from_serial(value: int | float) -> int | None:
    try:
        return (_SHEETS_EPOCH + timedelta(days=int(value))).year
    except (OverflowError, ValueError, TypeError):
        return None


def _year_from_iso_string(value: str) -> int | None:
    try:
        return date.fromisoformat(value[:10]).year
    except ValueError:
        return None


def _year_from_a_value(value: object) -> int | None:
    """Extract the year from a column-A cell read with ``UNFORMATTED_VALUE``.

    Date serial → decoded year. Plain ``YYYY-MM-DD`` text → parsed year.
    Anything else (formatted display strings, empty cells) → ``None``
    (treated as "year unknown" by matching helpers; wildcard-matches month).
    """
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return _year_from_serial(value)
    if isinstance(value, str):
        return _year_from_iso_string(value)
    return None


def fetch_row_years(ws: gspread.Worksheet, n_rows: int) -> list[int | None]:
    """Read column A unformatted; return a list aligned 1:1 with ``get_all_values()``.

    Sheets API trims trailing empty rows from ``batch_get``, so the result is
    padded with ``None`` to exactly ``n_rows`` entries. ``None`` = year unknown
    → wildcard-matches any target year.
    """
    if n_rows < 1:
        return []
    fetched = ws.batch_get(
        [f"A1:A{n_rows}"],
        value_render_option=ValueRenderOption.unformatted,
    )
    cells = fetched[0] if fetched else []
    years: list[int | None] = []
    for cell_row in cells:
        val = cell_row[0] if cell_row else None
        years.append(_year_from_a_value(val))
    while len(years) < n_rows:
        years.append(None)
    return years


def _check_year_args_paired(
    target_year: int | None,
    years_by_row: list[int | None] | None,
) -> None:
    """Raise if exactly one of (target_year, years_by_row) is provided.

    Partial year-aware mode silently wildcards — the failure mode behind a
    previous rate-corruption bug. Failing loudly here surfaces the caller bug.
    """
    if (target_year is None) != (years_by_row is None):
        raise ValueError(
            "target_year and years_by_row must be provided together "
            "(or both omitted); got "
            f"target_year={target_year!r}, "
            f"years_by_row={'list' if years_by_row is not None else None}",
        )


def _row_year_matches(
    row_index_1based: int,
    target_year: int | None,
    years_by_row: list[int | None] | None,
) -> bool:
    if target_year is None:
        return True
    assert years_by_row is not None  # noqa: S101 — enforced by _check_year_args_paired
    idx = row_index_1based - 1
    if idx < 0 or idx >= len(years_by_row):
        return True
    row_year = years_by_row[idx]
    return row_year is None or row_year == target_year


def find_category_row(
    all_values: list[list[str]],
    target_month: int,
    category: str,
    group: str,
    *,
    target_year: int | None = None,
    years_by_row: list[int | None] | None = None,
) -> int | None:
    """Return the 1-indexed row for ``(year?, month, category, group)``, or None."""
    _check_year_args_paired(target_year, years_by_row)
    for i, row in enumerate(all_values[HEADER_ROWS:], start=HEADER_ROWS + 1):
        if (
            _cell(row, COL_MONTH) == str(target_month)
            and _cell(row, COL_CATEGORY) == category
            and _cell(row, COL_GROUP) == group
            and _row_year_matches(i, target_year, years_by_row)
        ):
            return i
    return None


def find_month_range(
    all_values: list[list[str]],
    month: int,
    *,
    target_year: int | None = None,
    years_by_row: list[int | None] | None = None,
) -> tuple[int, int] | None:
    """Return ``(first_row, last_row)`` 1-indexed for a contiguous month block."""
    _check_year_args_paired(target_year, years_by_row)
    first: int | None = None
    last: int | None = None
    for i, row in enumerate(all_values[HEADER_ROWS:], start=HEADER_ROWS + 1):
        if _cell(row, COL_MONTH) == str(month) and _row_year_matches(i, target_year, years_by_row):
            if first is None:
                first = i
            last = i
    if first is None or last is None:
        return None
    return (first, last)


def _find_insertion_row(
    all_values: list[list[str]],
    target_month: int,
    category: str,
    group: str,
    *,
    target_year: int | None = None,
    years_by_row: list[int | None] | None = None,
) -> int:
    """Return the 1-indexed position for a new ``(year?, month, cat, grp)`` row.

    Inserts within an existing month block to maintain ascending (category, group)
    order. When no block exists yet and year-aware mode is on, walks top-to-bottom
    and stops at the first row strictly older than the target (newer months on top).
    """
    _check_year_args_paired(target_year, years_by_row)
    month_range = find_month_range(
        all_values,
        target_month,
        target_year=target_year,
        years_by_row=years_by_row,
    )
    if month_range is not None:
        first, last = month_range
        for i in range(first, last + 1):
            row = all_values[i - 1]
            if (_cell(row, COL_CATEGORY), _cell(row, COL_GROUP)) > (category, group):
                return i
        return last + 1

    if target_year is not None and years_by_row is not None:
        for i, row in enumerate(all_values[HEADER_ROWS:], start=HEADER_ROWS + 1):
            row_year = years_by_row[i - 1]
            month_str = _cell(row, COL_MONTH)
            if row_year is None or not month_str.isdigit():
                continue
            if (row_year, int(month_str)) < (target_year, target_month):
                return i
        return len(all_values) + 1

    return HEADER_ROWS + 1

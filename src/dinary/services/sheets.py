"""Google Sheets read/write via gspread.

Shared between historical import (read-only) and optional runtime sheet
logging (append-only). The sheet logging path uses ``ensure_category_row``
to insert rows on demand — no month-copying. Import uses layout-aware
readers in ``dinary.imports``.

Year-aware matching
-------------------
The optional sheet-logging spreadsheet holds expenses for *all* years.
Column G stores the month number 1..12 only, so any helper that filters
rows by month would otherwise collapse e.g. January 2026 and January
2027 into one match — appending a 2027 expense onto a 2026 row.

The fix is keyed on column A: ``insert_logging_row`` writes
``YYYY-MM-DD`` there with ``USER_ENTERED``, so Google Sheets stores it
as a date serial. ``ws.get_all_values()`` returns the *formatted*
display string (``"Apr-1"`` etc.), which doesn't carry the year, so we
fetch column A separately with ``UNFORMATTED_VALUE`` via
``fetch_row_years`` and pass the resulting per-row year list as
``years_by_row`` into the matching helpers.

When ``years_by_row`` (and ``target_year``) is omitted, the helpers
fall back to legacy month-only matching — that path stays alive for
unit tests that mock ``ws.get_all_values()`` only and never need a
multi-year sheet, and for any caller that genuinely doesn't care about
year (e.g. a single-year diagnostic).
"""

import logging
from datetime import date, timedelta

import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import ValueInputOption, ValueRenderOption

from dinary.config import settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# 1-indexed column numbers matching the actual Google Sheet layout:
#   A=Date  B=AppCurrency(formula)  C=EUR(formula)  D=Category  E=Group
#   F=Comment  G=Month  H=Rate  J=LastClientExpenseId
# Column I is intentionally skipped to leave the human-visible block
# (A..H) untouched. J stores only the *most recent* ``client_expense_id``
# appended to the row ("last-key-only" marker). If a retry arrives for
# the same ``client_expense_id``, the drain worker sees J == key and
# skips the second write; otherwise the marker is overwritten so the
# cell size stays bounded to a single UUID regardless of how many
# expenses a row aggregates over a month.
COL_DATE = 1
COL_AMOUNT_RSD = 2
COL_CATEGORY = 4
COL_GROUP = 5
COL_COMMENT = 6
COL_MONTH = 7
COL_RATE_EUR = 8
COL_EXPENSE_IDS = 10

HEADER_ROWS = 1


_gc: gspread.Client | None = None


def _get_client() -> gspread.Client:
    global _gc  # noqa: PLW0603
    if _gc is None:
        creds = Credentials.from_service_account_file(
            str(settings.google_sheets_credentials_path),
            scopes=SCOPES,
        )
        _gc = gspread.authorize(creds)
    return _gc


def get_sheet(spreadsheet_id: str) -> gspread.Spreadsheet:
    return _get_client().open_by_key(spreadsheet_id)


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


# Google Sheets stores dates as the number of days since 1899-12-30
# (the "1900 date system" inherited from Excel, with the Lotus 1-2-3
# leap-year quirk built into the epoch shift).
_SHEETS_EPOCH = date(1899, 12, 30)


def _year_from_a_value(value: object) -> int | None:  # noqa: PLR0911
    """Extract the year from a column-A cell read with ``UNFORMATTED_VALUE``.

    ``insert_logging_row`` writes the first day of the month with
    ``USER_ENTERED``, so Google parses it into a date serial. Older /
    legacy rows entered as plain text may show up as ``YYYY-MM-DD``
    strings instead. Anything else (the displayed ``"Apr-1"`` form, an
    empty cell, an unrecognised string) returns ``None`` and is treated
    as "year unknown" by the matching helpers — those rows still match
    on (month, cat, grp) only, preserving backwards compatibility.

    The PLR0911 suppression is intentional: the function is a flat
    type-dispatch table — one ``return`` per observable input class
    (``None``/empty, bool, valid date serial, NaN/inf serial, ISO
    string, malformed string, unknown type). Folding the returns into
    a single chained expression would obscure the decision table
    without removing any logic.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        # ``bool`` is a subclass of ``int`` — guard explicitly so a
        # stray TRUE/FALSE doesn't get interpreted as a date serial.
        return None
    if isinstance(value, int | float):
        # ``int(value)`` can raise ``ValueError`` on NaN, ``OverflowError``
        # on +/-inf, and ``TypeError`` on rare numeric subclasses with
        # broken ``__int__``. None of those are valid date serials.
        try:
            return (_SHEETS_EPOCH + timedelta(days=int(value))).year
        except (OverflowError, ValueError, TypeError):
            return None
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10]).year
        except ValueError:
            return None
    return None


def fetch_row_years(ws: gspread.Worksheet, n_rows: int) -> list[int | None]:
    """Read column A unformatted and return ``years_by_row[i-1]`` for row ``i``.

    A separate ``batch_get`` is required because ``ws.get_all_values()``
    returns the *displayed* (formatted) text — Google shows column A as
    ``"Apr-1"`` etc., dropping the year. The unformatted read returns
    the underlying date serial (or the original string for text-typed
    cells), which ``_year_from_a_value`` decodes.

    Header rows are read too so the returned list aligns 1:1 with
    ``ws.get_all_values()``.

    Caller contract: pass ``n_rows = len(ws.get_all_values())`` from the
    same logical fetch. The result is always exactly ``n_rows`` long —
    Sheets API trims trailing empty rows out of ``batch_get`` so we pad
    with ``None`` to restore the 1:1 alignment that ``_row_year_matches``
    and ``_find_insertion_row`` index into. ``None`` entries are treated
    as "year unknown" and wildcard-match — the right behavior for
    pre-year-aware sheets and blank/text column-A cells.
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
    """Reject callers that opt into year-aware mode only halfway.

    ``target_year`` and ``years_by_row`` are a single contract: either
    both present (year-aware matching is on) or both absent (legacy
    month-only matching). Passing one without the other previously
    yielded silent wildcard matches that *looked* year-aware to the
    caller — exactly the failure mode behind the rate-corruption bug
    we just fixed in ``_append_row_to_sheet``. Failing loudly here
    surfaces the caller bug instead of laundering it.
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
    """Year-aware acceptance check shared by the matching helpers.

    Returns ``True`` when:

    * year-aware mode is off (no ``target_year`` and no
      ``years_by_row`` — already validated by
      ``_check_year_args_paired``);
    * the row's parsed year matches ``target_year``;
    * the row's year is ``None`` — happens for header rows, blank
      column-A cells, and legacy/test rows whose A is formatted text
      like ``"Apr-1"``. Treating these as wildcards keeps the existing
      single-year and unit-test paths working unchanged.

    Out-of-bounds ``row_index_1based`` (years_by_row shorter than
    all_values) also wildcards — see ``fetch_row_years`` padding logic.
    Callers in production should keep both lists aligned; the
    out-of-bounds path is a safety net, not a contract.
    """
    # ``_check_year_args_paired`` (called by every public matcher above
    # before reaching here) guarantees ``target_year is None ↔ years_by_row
    # is None``, so testing one suffices for the legacy-mode short-circuit.
    if target_year is None:
        return True
    assert years_by_row is not None  # noqa: S101  -- enforced by caller pairing check
    idx = row_index_1based - 1
    if idx < 0 or idx >= len(years_by_row):
        return True
    row_year = years_by_row[idx]
    return row_year is None or row_year == target_year


def find_category_row(  # noqa: PLR0913
    all_values: list[list[str]],
    target_month: int,
    category: str,
    group: str,
    *,
    target_year: int | None = None,
    years_by_row: list[int | None] | None = None,
) -> int | None:
    """Find the 1-indexed row for a (year?, month, category, group) tuple.

    When ``target_year`` and ``years_by_row`` are both provided, only a
    row whose column-A year matches ``target_year`` (or is unknown) can
    be selected. This is what lets the sheet-logging path target the
    correct yearly block in a multi-year spreadsheet.
    """
    _check_year_args_paired(target_year, years_by_row)
    for i, row in enumerate(all_values[HEADER_ROWS:], start=HEADER_ROWS + 1):
        month_val = _cell(row, COL_MONTH)
        cat_val = _cell(row, COL_CATEGORY)
        grp_val = _cell(row, COL_GROUP)
        if (
            month_val == str(target_month)
            and cat_val == category
            and grp_val == group
            and _row_year_matches(i, target_year, years_by_row)
        ):
            return i
    return None


def get_month_rate(
    all_values: list[list[str]],
    target_month: int,
    *,
    target_year: int | None = None,
    years_by_row: list[int | None] | None = None,
) -> str | None:
    """Return the first non-empty EUR rate found for *target_month*.

    With year-aware arguments, only rates from rows of the correct year
    (or year-unknown rows) are considered.
    """
    _check_year_args_paired(target_year, years_by_row)
    for i, row in enumerate(all_values[HEADER_ROWS:], start=HEADER_ROWS + 1):
        if _cell(row, COL_MONTH) == str(target_month) and _row_year_matches(
            i,
            target_year,
            years_by_row,
        ):
            rate_val = _cell(row, COL_RATE_EUR)
            if rate_val and _is_numeric(rate_val):
                return rate_val
    return None


def find_month_range(
    all_values: list[list[str]],
    month: int,
    *,
    target_year: int | None = None,
    years_by_row: list[int | None] | None = None,
) -> tuple[int, int] | None:
    """Return (first_row, last_row) 1-indexed for a contiguous month block.

    With year-aware arguments, the block is constrained to rows whose
    column-A year matches ``target_year`` (or is unknown).
    """
    _check_year_args_paired(target_year, years_by_row)
    first: int | None = None
    last: int | None = None
    for i, row in enumerate(all_values[HEADER_ROWS:], start=HEADER_ROWS + 1):
        if _cell(row, COL_MONTH) == str(month) and _row_year_matches(
            i,
            target_year,
            years_by_row,
        ):
            if first is None:
                first = i
            last = i
    if first is None or last is None:
        return None
    return (first, last)


def _find_insertion_row(  # noqa: PLR0913
    all_values: list[list[str]],
    target_month: int,
    category: str,
    group: str,
    *,
    target_year: int | None = None,
    years_by_row: list[int | None] | None = None,
) -> int:
    """Return the 1-indexed row where a new ``(year?, month, cat, grp)`` row should go.

    Within an existing matching block (same year + month, or just same
    month in legacy mode), the new row is placed to maintain ascending
    ``(category, group)`` order.

    Year-aware mode (``target_year`` + ``years_by_row``):
        when no block exists yet for ``(target_year, target_month)`` we
        walk top-to-bottom and stop at the first existing row whose
        ``(year, month)`` is **strictly older** than the target. This
        keeps the file's "newer years/months on top" convention. If
        nothing older is found, the row goes at the bottom — which is
        what we want when the new entry is the oldest.

    Legacy mode (no year info):
        unchanged — new month blocks land right after the header.
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
            row_cat = _cell(row, COL_CATEGORY)
            row_grp = _cell(row, COL_GROUP)
            if (row_cat, row_grp) > (category, group):
                return i
        return last + 1

    if target_year is not None and years_by_row is not None:
        # ``years_by_row`` is sized off ``len(all_values)`` by
        # ``fetch_row_years``, so indices ``HEADER_ROWS..len(all_values)``
        # are always in bounds.
        for i, row in enumerate(all_values[HEADER_ROWS:], start=HEADER_ROWS + 1):
            row_year = years_by_row[i - 1]
            month_str = _cell(row, COL_MONTH)
            if row_year is None or not month_str.isdigit():
                continue
            row_month = int(month_str)
            if (row_year, row_month) < (target_year, target_month):
                return i
        return len(all_values) + 1

    return HEADER_ROWS + 1


def insert_logging_row(  # noqa: PLR0913
    ws: gspread.Worksheet,
    insert_at: int,
    expense_date: date,
    month: int,
    category: str,
    group: str,
    *,
    rate: str | None = None,
) -> None:
    """Insert a single blank row at *insert_at* and populate its cells.

    Cell values written:
      * A — first day of month (``YYYY-MM-DD``)
      * B — empty (filled later by ``append_expense_atomic``)
      * C — conversion formula ``=IF(H{r}="","",B{r}/H{r})`` (relative per row)
      * D — *category*
      * E — *group*
      * F — empty (comment, filled by ``append_expense_atomic``)
      * G — *month* number
      * H — *rate* (if provided) — written on every row, not only the
        first row of a month, so the column-C formula stays stable. If
        *rate* is ``None`` we write an explicit empty string rather
        than skipping the cell; the first subsequent
        ``append_expense_atomic`` on this row will then see an empty H
        and backfill the rate via its set-if-missing guard. Skipping
        H entirely would break that guard because ``batch_get`` on an
        unset cell and on an empty-string cell both come back as ``""``
        — the placeholder is what lets a caller tell "H was never
        touched" apart from "H was touched and intentionally blank".
      * J — empty (last-key-only ``client_expense_id`` marker, filled by
        ``append_expense_atomic``)
    """
    ws.insert_rows([[]], row=insert_at)

    date_str = expense_date.replace(day=1).strftime("%Y-%m-%d")
    r = insert_at
    ws.batch_update(
        [
            {"range": f"A{r}", "values": [[date_str]]},
            {"range": f"B{r}", "values": [[""]]},
            {"range": f"C{r}", "values": [[f'=IF(H{r}="","",B{r}/H{r})']]},
            {"range": f"D{r}", "values": [[category]]},
            {"range": f"E{r}", "values": [[group]]},
            {"range": f"F{r}", "values": [[""]]},
            {"range": f"G{r}", "values": [[str(month)]]},
            {"range": f"H{r}", "values": [[rate or ""]]},
            {"range": f"J{r}", "values": [[""]]},
        ],
        value_input_option=ValueInputOption.user_entered,
    )
    logger.info(
        "Inserted logging row at %d for month %d (%s/%s)",
        r,
        month,
        category,
        group,
    )


def ensure_category_row(  # noqa: PLR0913
    ws: gspread.Worksheet,
    all_values: list[list[str]],
    target_month: int,
    category: str,
    group: str,
    expense_date: date,
    *,
    years_by_row: list[int | None] | None = None,
    rate: str | None = None,
) -> tuple[int, list[list[str]]]:
    """Return ``(row_index, refreshed_values)`` for ``(year?, month, cat, grp)``.

    If the row already exists, return it and the unchanged grid.
    Otherwise insert a single new row at the position that maintains
    ``(year, month, category, group)`` sort order, and return the
    refreshed grid.

    When ``years_by_row`` is provided, ``expense_date.year`` is the
    target year; otherwise the helper falls back to month-only matching
    so existing single-year tests and callers keep working.
    """
    target_year = expense_date.year if years_by_row is not None else None

    target_row = find_category_row(
        all_values,
        target_month,
        category,
        group,
        target_year=target_year,
        years_by_row=years_by_row,
    )
    if target_row is not None:
        return target_row, all_values

    insert_at = _find_insertion_row(
        all_values,
        target_month,
        category,
        group,
        target_year=target_year,
        years_by_row=years_by_row,
    )
    insert_logging_row(
        ws,
        insert_at,
        expense_date,
        target_month,
        category,
        group,
        rate=rate,
    )
    refreshed = ws.get_all_values()
    return insert_at, refreshed


def extend_rsd_formula(existing: str, amount_rsd: float) -> str:
    """Pure function — return the new B-cell formula after appending *amount_rsd*.

    Inputs the live string in column B as rendered with
    ``ValueRenderOption.formula`` (so a formula cell looks like
    ``"=460+373"`` and a literal-number cell looks like ``"460"`` or
    ``"460.5"``). Empty / non-numeric / unrecognised values restart the
    formula from scratch.
    """
    amount_str = fmt_amount(amount_rsd)
    if existing.startswith("="):
        return f"{existing}+{amount_str}"
    if existing and _is_numeric(existing):
        return f"={existing}+{amount_str}"
    return f"={amount_str}"


def extend_comment(existing: str, new_comment: str) -> str:
    """Pure function — return the new F-cell value after appending *new_comment*.

    ``new_comment`` is appended verbatim with a ``"; "`` separator if
    *existing* is non-empty, or stored alone otherwise. An empty
    *new_comment* leaves *existing* unchanged.
    """
    if not new_comment:
        return existing
    separator = "; " if existing else ""
    return f"{existing}{separator}{new_comment}"


def append_to_rsd_formula(ws: gspread.Worksheet, row: int, amount_rsd: float) -> None:
    """Append +amount to the formula in column B (e.g. =460+373 → =460+373+1500).

    Non-atomic, single-cell helper. Sheet logging uses
    ``append_expense_atomic`` instead so the formula and the
    idempotency marker are written together.
    """
    rsd_addr = gspread.utils.rowcol_to_a1(row, COL_AMOUNT_RSD)
    raw = ws.acell(rsd_addr, value_render_option=ValueRenderOption.formula).value
    existing = str(raw) if raw is not None else ""
    formula = extend_rsd_formula(existing, amount_rsd)
    ws.update(
        range_name=rsd_addr,
        values=[[formula]],
        value_input_option=ValueInputOption.user_entered,
    )


def append_comment(ws: gspread.Worksheet, row: int, row_data: list[str], comment: str) -> None:
    """Append a comment to column F, semicolon-separated.

    Non-atomic, single-cell helper. Sheet logging uses
    ``append_expense_atomic`` instead so the comment, the formula, and
    the idempotency marker are written together.
    """
    existing = _cell(row_data, COL_COMMENT)
    new_value = extend_comment(existing, comment)
    if new_value != existing:
        ws.update_cell(row, COL_COMMENT, new_value)


def _first_cell(batch_get_result: list[list[str]]) -> str:
    """Pull a single cell value out of a ``ws.batch_get`` per-range result.

    ``ws.batch_get`` returns ``[]`` for an empty cell, ``[[value]]`` for
    a single non-empty cell, and similarly nested lists for larger
    ranges. We only ever ask for single cells here, so flatten and
    default to ``""``.
    """
    if not batch_get_result:
        return ""
    first_row = batch_get_result[0]
    if not first_row:
        return ""
    return str(first_row[0]) if first_row[0] is not None else ""


def append_expense_atomic(  # noqa: PLR0913
    ws: gspread.Worksheet,
    row: int,
    *,
    marker_key: str,
    amount_rsd: float,
    comment: str,
    rate: str | None = None,
) -> bool:
    """Idempotently record one expense at *row*.

    Reads the live B (formula) / F (comment) / J (marker) cells in a
    single ``batch_get`` call, then either:

    * returns ``False`` immediately when J already equals *marker_key* —
      the previous attempt for this expense reached the server even if
      we never saw the response;
    * issues a single ``batch_update`` writing the new B, F (only when
      *comment* is non-empty), J (overwriting any previous marker with
      *marker_key* — "last-key-only" semantics), and H (when *rate* is
      provided) in one HTTP request, and returns ``True``.

    Combining the writes into one request is what closes the
    timeout-after-success duplicate hole: the marker is written together
    with the formula it accounts for, so the only two observable states
    are "all updated" and "none updated", both of which the next attempt
    handles correctly.

    The J column stores only the most recent ``client_expense_id`` that
    was appended to this row; older markers are overwritten. This bounds
    the cell size to a single UUID regardless of how many expenses a
    row aggregates.
    """
    formula_addr = gspread.utils.rowcol_to_a1(row, COL_AMOUNT_RSD)
    comment_addr = gspread.utils.rowcol_to_a1(row, COL_COMMENT)
    marker_addr = gspread.utils.rowcol_to_a1(row, COL_EXPENSE_IDS)
    rate_addr = gspread.utils.rowcol_to_a1(row, COL_RATE_EUR)

    fetched = ws.batch_get(
        [formula_addr, comment_addr, marker_addr, rate_addr],
        value_render_option=ValueRenderOption.formula,
    )
    existing_formula = _first_cell(fetched[0])
    existing_comment = _first_cell(fetched[1])
    existing_marker = _first_cell(fetched[2])
    existing_rate = _first_cell(fetched[3])

    if existing_marker == marker_key:
        logger.info(
            "Expense %s already recorded at row %d (J marker equal); skipping",
            marker_key,
            row,
        )
        return False

    new_formula = extend_rsd_formula(existing_formula, amount_rsd)

    updates = [
        {"range": formula_addr, "values": [[new_formula]]},
        {"range": marker_addr, "values": [[marker_key]]},
    ]
    if comment:
        new_comment = extend_comment(existing_comment, comment)
        updates.append({"range": comment_addr, "values": [[new_comment]]})
    # Set-if-missing: only backfill H when the cell is empty, so a user's
    # manual rate edit (or an earlier importer-written rate) survives the
    # next append. This matches architecture.md's column-H contract; a
    # stale rate in an already-populated cell is the operator's problem,
    # not the drain's.
    if rate is not None and not existing_rate:
        updates.append({"range": rate_addr, "values": [[rate]]})

    ws.batch_update(updates, value_input_option=ValueInputOption.user_entered)
    return True

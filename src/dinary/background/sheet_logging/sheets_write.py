"""Sheet logging row operations: insert and atomically append expense rows."""

import logging
from datetime import date

import gspread
from gspread.utils import ValueInputOption, ValueRenderOption

from dinary.sheets.sheets import (
    _find_insertion_row,
    _is_numeric,
    find_category_row,
    fmt_amount,
)

logger = logging.getLogger(__name__)

# 1-indexed columns, see specs/reference/sheets.md "Logging column layout".
COL_DATE = 1
COL_AMOUNT_APP = 2
COL_COMMENT = 6
COL_RATE = 8
COL_EXPENSE_IDS = 10


def insert_logging_row(
    ws: gspread.Worksheet,
    insert_at: int,
    expense_date: date,
    month: int,
    category: str,
    group: str,
    *,
    rate: str | None = None,
) -> None:
    """H is written as an explicit empty string when *rate* is None, enabling
    ``append_expense_atomic``'s set-if-missing backfill path."""
    ws.insert_rows([[]], row=insert_at)
    r = insert_at
    date_str = expense_date.replace(day=1).strftime("%Y-%m-%d")
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
    logger.info("Inserted logging row at %d for month %d (%s/%s)", r, month, category, group)


def ensure_category_row(
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

    If the row exists return it unchanged. Otherwise insert at the position that
    maintains ``(year, month, category, group)`` sort order and return the
    refreshed grid.
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
    insert_logging_row(ws, insert_at, expense_date, target_month, category, group, rate=rate)
    refreshed = ws.get_all_values()
    return insert_at, refreshed


def extend_amount_formula(existing: str, amount_app: float) -> str:
    """Return the new B-cell formula after appending *amount_app*.

    Input is the live cell value rendered with ``ValueRenderOption.formula``.
    Empty / non-numeric / unrecognised values restart the formula from scratch.
    """
    amount_str = fmt_amount(amount_app)
    if existing.startswith("="):
        return f"{existing}+{amount_str}"
    if existing and _is_numeric(existing):
        return f"={existing}+{amount_str}"
    return f"={amount_str}"


def extend_comment(existing: str, new_comment: str) -> str:
    """Return the new F-cell value after appending *new_comment* with ``"; "`` separator."""
    if not new_comment:
        return existing
    separator = "; " if existing else ""
    return f"{existing}{separator}{new_comment}"


def _first_cell(batch_get_result: list[list[str]]) -> str:
    if not batch_get_result:
        return ""
    first_row = batch_get_result[0]
    if not first_row:
        return ""
    return str(first_row[0]) if first_row[0] is not None else ""


def append_expense_atomic(
    ws: gspread.Worksheet,
    row: int,
    *,
    marker_key: str,
    amount_app: float,
    comment: str,
    rate: str | None = None,
) -> bool:
    """Idempotently append one expense to *row*. Returns False if already recorded.
    One atomic batch_get + batch_update closes the timeout-after-success duplicate
    hole — see ``specs/reference/sheets.md`` for the column-J idempotency contract."""
    formula_addr = gspread.utils.rowcol_to_a1(row, COL_AMOUNT_APP)
    comment_addr = gspread.utils.rowcol_to_a1(row, COL_COMMENT)
    marker_addr = gspread.utils.rowcol_to_a1(row, COL_EXPENSE_IDS)
    rate_addr = gspread.utils.rowcol_to_a1(row, COL_RATE)

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

    updates = [
        {"range": formula_addr, "values": [[extend_amount_formula(existing_formula, amount_app)]]},
        {"range": marker_addr, "values": [[marker_key]]},
    ]
    if comment:
        updates.append(
            {"range": comment_addr, "values": [[extend_comment(existing_comment, comment)]]},
        )
    if rate is not None and not existing_rate:
        updates.append({"range": rate_addr, "values": [[rate]]})

    ws.batch_update(updates, value_input_option=ValueInputOption.user_entered)
    return True

"""Verify that the rebuilt DB produces the same Google Sheet representation.

Compares DuckDB aggregates (via reverse lookup) against the actual sheet data
row-by-row for each month. Any diff means the rebuild changed observable behavior.

Equivalence is based on amount_original (the value as it appears in the sheet).
"""

import logging
from collections import defaultdict
from decimal import Decimal

from dinary.services import duckdb_repo
from dinary.services.import_sheet import LAYOUTS
from dinary.services.sheets import (
    HEADER_ROWS,
    _cell,
    get_sheet,
)
from dinary.services.sync import _build_aggregates

logger = logging.getLogger(__name__)

_MONTHS_IN_YEAR = 12


def _parse_display_amount(display: str) -> float | None:
    if not display:
        return None
    cleaned = display.replace(" ", "").replace(",", "")
    try:
        val = float(cleaned)
        return val if val != 0 else None
    except ValueError:
        return None


def _read_sheet_aggregates(
    year: int,
) -> dict[int, dict[tuple[str, str], dict]]:
    """Read the Google Sheet and build per-month aggregates.

    Returns {month: {(type, envelope): {amount, comment}}}.
    """
    source = duckdb_repo.get_import_source(year)
    if source is None:
        msg = f"sheet_import_sources is missing a row for year {year}"
        raise ValueError(msg)
    spreadsheet_id = source.spreadsheet_id
    worksheet_name = source.worksheet_name
    layout_key = source.layout_key
    layout = LAYOUTS[layout_key]

    ss = get_sheet(spreadsheet_id)
    ws = ss.worksheet(worksheet_name) if worksheet_name else ss.sheet1
    all_values = ws.get_all_values()

    result: dict[int, dict[tuple[str, str], dict]] = defaultdict(dict)

    for row_idx in range(HEADER_ROWS, len(all_values)):
        row_display = all_values[row_idx]

        month_str = _cell(row_display, layout.col_month)
        if not month_str or not month_str.isdigit():
            continue
        month = int(month_str)
        if not 1 <= month <= _MONTHS_IN_YEAR:
            continue

        category = _cell(row_display, layout.col_category)
        group = _cell(row_display, layout.col_group)
        if not category:
            continue

        display_raw = _cell(row_display, layout.col_amount)
        amount_val = _parse_display_amount(display_raw)

        total = Decimal(str(amount_val)) if amount_val else Decimal(0)
        comment = _cell(row_display, layout.col_comment)

        key = (category, group)
        if key in result[month]:
            result[month][key]["amount"] += total
            if comment:
                existing = result[month][key]["comment"]
                result[month][key]["comment"] = f"{existing}; {comment}" if existing else comment
        else:
            result[month][key] = {"amount": total, "comment": comment}

    return dict(result)


def _read_db_aggregates(year: int) -> dict[int, dict[tuple[str, str], dict]]:
    """Build per-month aggregates from DuckDB using the sync reverse-lookup path."""
    result: dict[int, dict[tuple[str, str], dict]] = {}

    con = duckdb_repo.get_budget_connection(year)
    try:
        for month in range(1, _MONTHS_IN_YEAR + 1):
            agg = _build_aggregates(con, year, month)
            if agg is None:
                continue
            month_data: dict[tuple[str, str], dict] = {}
            for (cat, grp), data in agg.items():
                comments = data.get("comments", [])
                month_data[(cat, grp)] = {
                    "amount": data["total_rsd"],
                    "comment": "; ".join(comments) if comments else "",
                }
            result[month] = month_data
    finally:
        con.close()

    return result


def verify_sheet_equivalence(year: int) -> dict:
    """Compare sheet data against DB aggregates and return a diff report.

    Returns a dict with:
      - months_checked: int
      - missing_rows: list of rows in sheet but not in DB
      - extra_rows: list of rows in DB but not in sheet
      - amount_diffs: list of rows with different amounts
      - comment_diffs: list of rows with different comments
      - ok: bool (True when zero diffs)
    """
    duckdb_repo.init_config_db()
    sheet_data = _read_sheet_aggregates(year)
    db_data = _read_db_aggregates(year)

    all_months = sorted(set(sheet_data.keys()) | set(db_data.keys()))

    missing_rows: list[dict] = []
    extra_rows: list[dict] = []
    amount_diffs: list[dict] = []
    comment_diffs: list[dict] = []

    for month in all_months:
        s_month = sheet_data.get(month, {})
        d_month = db_data.get(month, {})

        all_keys = set(s_month.keys()) | set(d_month.keys())
        for key in sorted(all_keys):
            cat, grp = key
            in_sheet = key in s_month
            in_db = key in d_month

            if in_sheet and not in_db:
                sheet_amt = s_month[key]["amount"]
                if sheet_amt > 0:
                    missing_rows.append(
                        {
                            "month": month,
                            "type": cat,
                            "envelope": grp,
                            "sheet_amount": float(sheet_amt),
                        },
                    )
                continue

            if in_db and not in_sheet:
                extra_rows.append(
                    {
                        "month": month,
                        "type": cat,
                        "envelope": grp,
                        "db_amount": float(d_month[key]["amount"]),
                    },
                )
                continue

            s_amt = s_month[key]["amount"]
            d_amt = d_month[key]["amount"]
            if abs(s_amt - d_amt) > Decimal("0.01"):
                amount_diffs.append(
                    {
                        "month": month,
                        "type": cat,
                        "envelope": grp,
                        "sheet_amount": float(s_amt),
                        "db_amount": float(d_amt),
                    },
                )

            s_comment = s_month[key]["comment"]
            d_comment = d_month[key]["comment"]
            if s_comment != d_comment:
                comment_diffs.append(
                    {
                        "month": month,
                        "type": cat,
                        "envelope": grp,
                        "sheet_comment": s_comment,
                        "db_comment": d_comment,
                    },
                )

    ok = not missing_rows and not extra_rows and not amount_diffs
    return {
        "year": year,
        "months_checked": len(all_months),
        "missing_rows": missing_rows,
        "extra_rows": extra_rows,
        "amount_diffs": amount_diffs,
        "comment_diffs": comment_diffs,
        "ok": ok,
    }

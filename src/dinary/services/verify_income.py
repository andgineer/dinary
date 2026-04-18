"""Verify imported income against Google Sheets source data.

Re-reads the Income/Balance worksheet, aggregates EUR per month using the
same layout logic as import_income, and compares against the DB.
"""

import logging
from decimal import Decimal

from dinary.services import duckdb_repo
from dinary.services.import_income import (
    INCOME_LAYOUTS,
    _aggregate_from_sheet,
)

logger = logging.getLogger(__name__)

_TOLERANCE = Decimal("0.02")


def verify_income_equivalence(year: int) -> dict:
    """Compare DB income rows against the source Google Sheet."""
    source = duckdb_repo.get_import_source(year)
    if source is None:
        return {"year": year, "ok": False, "error": "no import source registered"}
    if not source.income_worksheet_name:
        return {"year": year, "ok": False, "error": "no income worksheet registered"}

    layout_key = source.income_layout_key
    if layout_key not in INCOME_LAYOUTS:
        return {"year": year, "ok": False, "error": f"unknown layout key: {layout_key!r}"}

    layout = INCOME_LAYOUTS[layout_key]
    sheet_monthly, _rows = _aggregate_from_sheet(year, source, layout)

    con = duckdb_repo.get_budget_connection(year)
    try:
        db_rows = con.execute(
            "SELECT month, amount FROM income WHERE year = ? ORDER BY month",
            [year],
        ).fetchall()
    finally:
        con.close()

    db_monthly: dict[int, Decimal] = {r[0]: Decimal(str(r[1])) for r in db_rows}
    month_diffs = _compare_months(sheet_monthly, db_monthly)

    total_sheet = sum(Decimal(str(v)) for v in sheet_monthly.values())
    total_db = sum(db_monthly.values())

    return {
        "year": year,
        "ok": len(month_diffs) == 0,
        "total_sheet_eur": float(total_sheet),
        "total_db_eur": float(total_db),
        "months_in_sheet": len(sheet_monthly),
        "months_in_db": len(db_monthly),
        "month_diffs": month_diffs,
    }


def _compare_months(
    sheet: dict[int, Decimal],
    db: dict[int, Decimal],
) -> list[dict]:
    all_months = sorted(set(sheet) | set(db))
    diffs = []
    for month in all_months:
        s = Decimal(str(sheet.get(month, 0)))
        d = db.get(month, Decimal(0))
        diff = abs(s - d)
        if diff > _TOLERANCE:
            diffs.append(
                {
                    "month": month,
                    "sheet_eur": float(s),
                    "db_eur": float(d),
                    "diff": float(diff),
                },
            )
    return diffs

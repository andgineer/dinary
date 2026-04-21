"""Verify imported income against Google Sheets source data.

Re-reads the Income/Balance worksheet, aggregates accounting-currency
(``settings.accounting_currency``, EUR by default) totals per month
using the same layout logic as ``import_income``, and compares against
the DB.
"""

import logging
from decimal import Decimal

from dinary.config import IMPORT_SOURCES_DOC_HINT, get_import_source, settings
from dinary.imports.income_import import (
    INCOME_LAYOUTS,
    aggregate_from_sheet,
)
from dinary.services import duckdb_repo

logger = logging.getLogger(__name__)

_TOLERANCE = Decimal("0.02")


def verify_income_equivalence(year: int) -> dict:
    """Compare DB income rows against the source Google Sheet.

    Error messages for the "missing source" and "no income
    worksheet" branches include a pointer to
    ``.deploy/import_sources.json`` + the repo-root ``imports/``
    directory so the operator knows where to register the year
    without having to grep the code.
    """
    source = get_import_source(year)
    if source is None:
        return {
            "year": year,
            "ok": False,
            "error": (
                f"no entry for year {year} in .deploy/import_sources.json. "
                f"{IMPORT_SOURCES_DOC_HINT}"
            ),
        }
    if not source.income_worksheet_name:
        return {
            "year": year,
            "ok": False,
            "error": (
                f"year {year} in .deploy/import_sources.json has no "
                "income_worksheet_name; add one to enable income import. "
                f"{IMPORT_SOURCES_DOC_HINT}"
            ),
        }

    layout_key = source.income_layout_key
    if layout_key not in INCOME_LAYOUTS:
        return {"year": year, "ok": False, "error": f"unknown layout key: {layout_key!r}"}

    layout = INCOME_LAYOUTS[layout_key]
    sheet_monthly, _rows = aggregate_from_sheet(year, source, layout)

    con = duckdb_repo.get_connection()
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
        "accounting_currency": settings.accounting_currency,
        "total_sheet_acc": float(total_sheet),
        "total_db_acc": float(total_db),
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
                    "sheet_acc": float(s),
                    "db_acc": float(d),
                    "diff": float(diff),
                },
            )
    return diffs

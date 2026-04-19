"""Verify a bootstrap-imported `budget_YYYY.duckdb` matches the source sheet.

Run after `inv rebuild-budget --year=YYYY`. Aggregates by
`(sheet_category, sheet_group)` per month on both sides:

  * sheet side — re-reads the historical worksheet exactly as
    `import_sheet` does (same layout, same RUB/RSD currency rules);
  * DB side — sums `amount_original` on every row of `budget_YYYY.duckdb`
    grouped by the `(sheet_category, sheet_group)` provenance pair that
    `import_sheet` baked in.

Equivalence is on `amount_original` (the raw value as it appears in the
sheet). Any diff means the rebuild changed observable behavior. Runtime
expenses (`sheet_category IS NULL`) are intentionally not part of this
check — they are produced by the runtime export path, not by import.
"""

import logging
from collections import defaultdict
from decimal import Decimal

from dinary.services import duckdb_repo
from dinary.services.import_sheet import (
    LAYOUTS,
    MONTHS_IN_YEAR,
    parse_display_amount,
    resolve_currency,
)
from dinary.services.sheets import (
    HEADER_ROWS,
    _cell,
    get_sheet,
)

logger = logging.getLogger(__name__)

_DIFF_TOLERANCE = Decimal("0.01")


def _read_sheet_aggregates(
    year: int,
) -> dict[int, dict[tuple[str, str], dict]]:
    source = duckdb_repo.get_import_source(year)
    if source is None:
        msg = f"sheet_import_sources is missing a row for year {year}"
        raise ValueError(msg)
    layout = LAYOUTS[source.layout_key]

    ss = get_sheet(source.spreadsheet_id)
    ws = ss.worksheet(source.worksheet_name) if source.worksheet_name else ss.sheet1
    all_values = ws.get_all_values()

    result: dict[int, dict[tuple[str, str], dict]] = defaultdict(dict)

    for row_idx in range(HEADER_ROWS, len(all_values)):
        row_display = all_values[row_idx]

        month_str = _cell(row_display, layout.col_month)
        if not month_str or not month_str.isdigit():
            continue
        month = int(month_str)
        if not 1 <= month <= MONTHS_IN_YEAR:
            continue

        category = _cell(row_display, layout.col_category)
        group = _cell(row_display, layout.col_group)
        if not category:
            continue

        amount_val = parse_display_amount(_cell(row_display, layout.col_amount))
        if (
            amount_val is None
            and layout.col_amount_fallback is not None
            and resolve_currency(year, month, layout) == "RUB"
        ):
            amount_val = parse_display_amount(
                _cell(row_display, layout.col_amount_fallback),
            )

        # Treating "no parseable amount" the same as "explicit zero" is
        # intentional here: equivalence is computed by summing per-month,
        # per-(category, group), and a missing cell can't legitimately
        # appear next to a real expense in the same group anyway. A
        # falsely-zero row still makes both sides agree and a bug that
        # produces extra/missing rows shows up in row counts elsewhere.
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
    """Aggregate every bootstrap-imported expense row by sheet provenance."""
    result: dict[int, dict[tuple[str, str], dict]] = defaultdict(dict)

    con = duckdb_repo.get_budget_connection(year)
    try:
        rows = con.execute(
            "SELECT MONTH(datetime), sheet_category, sheet_group, amount_original, comment"
            " FROM expenses"
            " WHERE sheet_category IS NOT NULL AND sheet_group IS NOT NULL",
        ).fetchall()
    finally:
        con.close()

    for month, sheet_cat, sheet_grp, amt, comment in rows:
        key = (sheet_cat, sheet_grp)
        bucket = result[month].setdefault(key, {"amount": Decimal(0), "comment": ""})
        bucket["amount"] += Decimal(str(amt))
        if comment:
            bucket["comment"] = f"{bucket['comment']}; {comment}" if bucket["comment"] else comment

    return dict(result)


def verify_bootstrap_import(year: int) -> dict:
    """Compare sheet aggregates against bootstrap-imported DB rows.

    Returns a dict with `ok=True` when amount totals match per
    `(month, sheet_category, sheet_group)`. Comment differences are reported
    but do not flip `ok` (sheet-side comments are concatenated with `;` and
    the import path may have applied normalization).
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
                            "sheet_category": cat,
                            "sheet_group": grp,
                            "sheet_amount": float(sheet_amt),
                        },
                    )
                continue

            if in_db and not in_sheet:
                extra_rows.append(
                    {
                        "month": month,
                        "sheet_category": cat,
                        "sheet_group": grp,
                        "db_amount": float(d_month[key]["amount"]),
                    },
                )
                continue

            s_amt = s_month[key]["amount"]
            d_amt = d_month[key]["amount"]
            if abs(s_amt - d_amt) > _DIFF_TOLERANCE:
                amount_diffs.append(
                    {
                        "month": month,
                        "sheet_category": cat,
                        "sheet_group": grp,
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
                        "sheet_category": cat,
                        "sheet_group": grp,
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

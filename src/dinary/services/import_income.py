"""Import monthly income from Google Sheets into DuckDB.

Reads the Income/Balance worksheet for a given year, aggregates EUR totals
per month, and stores one row per (year, month) in budget_YYYY.duckdb.income.

Destructive re-import: wipes existing sheet_import rows before inserting.
"""

import dataclasses
import logging
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

import duckdb

from dinary.services import duckdb_repo
from dinary.services.nbs import get_rate
from dinary.services.sheets import get_sheet

logger = logging.getLogger(__name__)

_MONTHS_IN_YEAR = 12


# ---------------------------------------------------------------------------
# Income sheet layout
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class IncomeLayout:
    """Column positions (1-indexed) for an Income worksheet."""

    col_date: int
    col_amount: int
    currency: str
    header_rows: int = 1
    # Mid-year currency transition: if set, months >= transition_month
    # use transition_currency instead of currency.
    transition_month: int | None = None
    transition_currency: str | None = None


_INCOME_RUB_RSD_TRANSITION_MONTH = 8  # August 2022: RUB before, RSD from

INCOME_LAYOUTS: dict[str, IncomeLayout] = {
    # 2019-2021: Balance tab, col A = date, col B = salary in RUB
    "balance_rub": IncomeLayout(col_date=1, col_amount=2, currency="RUB"),
    # 2022: Balance tab, RUB until July, RSD from August
    "balance_rub_rsd": IncomeLayout(
        col_date=1,
        col_amount=2,
        currency="RUB",
        transition_month=_INCOME_RUB_RSD_TRANSITION_MONTH,
        transition_currency="RSD",
    ),
    # 2023: Balance tab, col A = date, col B = salary in RSD
    "balance_rsd": IncomeLayout(col_date=1, col_amount=2, currency="RSD"),
    # 2024-2026: Income tab, col A = date, col B = salary in RSD
    "income_rsd": IncomeLayout(col_date=1, col_amount=2, currency="RSD"),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cell(row: list[str], col_1indexed: int) -> str:
    idx = col_1indexed - 1
    return row[idx].strip() if len(row) > idx else ""


def _parse_amount(raw: str) -> Decimal | None:
    """Parse a numeric cell, handling locale separators and $ prefix."""
    if not raw:
        return None
    cleaned = raw.replace("\xa0", "").replace(" ", "").replace(",", ".").lstrip("$")
    try:
        val = Decimal(cleaned)
    except Exception:  # noqa: BLE001
        return None
    return val if val != 0 else None


_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y")
_LOOSE_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")


def _parse_date(raw: str) -> tuple[int, int] | None:
    """Extract (year, month) from a date string. Returns None if unparseable."""
    if not raw:
        return None
    m = _LOOSE_DATE_RE.match(raw)
    if m:
        return int(m.group(1)), int(m.group(2))
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)  # noqa: DTZ007
            return dt.year, dt.month
        except ValueError:
            continue
    return None


def _convert_to_eur(
    amount: Decimal,
    currency: str,
    year: int,
    month: int,
    config_con: duckdb.DuckDBPyConnection,
) -> Decimal:
    if currency == "EUR":
        return amount
    rate_date = date(year, month, 1)
    rate_cur = get_rate(config_con, rate_date, currency)
    rate_eur = get_rate(config_con, rate_date, "EUR")
    return (amount * rate_cur / rate_eur).quantize(Decimal("0.01"))


def _aggregate_from_sheet(
    year: int,
    source: duckdb_repo.ImportSourceRow,
    layout: IncomeLayout,
) -> tuple[dict[int, Decimal], int]:
    """Read Income/Balance worksheet and return {month: eur_total} and row count.

    Only rows whose date falls within the target year are included — Balance
    tabs accumulate rows across multiple years.
    """
    ss = get_sheet(source.spreadsheet_id)
    ws = ss.worksheet(source.income_worksheet_name)
    all_values = ws.get_all_values()

    monthly_eur: dict[int, Decimal] = defaultdict(Decimal)
    rows_read = 0

    config_con = duckdb_repo.get_config_connection(read_only=False)
    try:
        for row_idx in range(layout.header_rows, len(all_values)):
            row = all_values[row_idx]
            parsed_date = _parse_date(_cell(row, layout.col_date))
            if parsed_date is None:
                continue
            row_year, month = parsed_date
            if row_year != year:
                continue
            if not 1 <= month <= _MONTHS_IN_YEAR:
                continue

            amount = _parse_amount(_cell(row, layout.col_amount))
            if amount is None:
                continue

            currency = layout.currency
            if (
                layout.transition_month is not None
                and layout.transition_currency is not None
                and month >= layout.transition_month
            ):
                currency = layout.transition_currency

            amount_eur = _convert_to_eur(
                amount,
                currency,
                year,
                month,
                config_con,
            )
            monthly_eur[month] += amount_eur
            rows_read += 1
    finally:
        config_con.close()

    return dict(monthly_eur), rows_read


def _resolve_layout(year: int, source: duckdb_repo.ImportSourceRow) -> IncomeLayout:
    layout_key = source.income_layout_key
    if layout_key not in INCOME_LAYOUTS:
        msg = f"Unknown income layout key: {layout_key!r} for year {year}"
        raise ValueError(msg)
    return INCOME_LAYOUTS[layout_key]


# ---------------------------------------------------------------------------
# Core import
# ---------------------------------------------------------------------------


def import_year_income(year: int) -> dict:
    """Destructive re-import of monthly income for a single year."""
    source = duckdb_repo.get_import_source(year)
    if source is None:
        return {"year": year, "skipped": "no import source registered"}
    if not source.income_worksheet_name:
        return {"year": year, "skipped": "no income worksheet registered"}

    layout = _resolve_layout(year, source)
    monthly_eur, rows_read = _aggregate_from_sheet(year, source, layout)

    con = duckdb_repo.get_budget_connection(year)
    try:
        con.execute("DELETE FROM income WHERE origin = 'sheet_import'")
        for month in sorted(monthly_eur):
            con.execute(
                "INSERT INTO income (year, month, amount, origin) VALUES (?, ?, ?, 'sheet_import')",
                [year, month, float(monthly_eur[month])],
            )
    finally:
        con.close()

    months_written = len(monthly_eur)
    logger.info(
        "Imported income for %d: %d months from %d rows",
        year,
        months_written,
        rows_read,
    )
    return {
        "year": year,
        "rows_read": rows_read,
        "months_written": months_written,
        "total_eur": float(sum(monthly_eur.values())),
    }

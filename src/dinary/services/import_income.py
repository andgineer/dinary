"""Import monthly income from Google Sheets into DuckDB.

Reads the Income worksheet for a given year, aggregates EUR totals per month,
and stores one row per (year, month) in budget_YYYY.duckdb.income.

Destructive re-import: wipes existing sheet_import rows before inserting.
"""

from __future__ import annotations

import dataclasses
import logging
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from dinary.services import duckdb_repo
from dinary.services.sheets import get_sheet

if TYPE_CHECKING:
    import duckdb

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
    currency: str = "EUR"
    col_amount_fallback: int | None = None
    fallback_currency: str = "RSD"
    header_rows: int = 1
    month_is_date: bool = True


INCOME_LAYOUTS: dict[str, IncomeLayout] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cell(row: list[str], col_1indexed: int) -> str:
    idx = col_1indexed - 1
    return row[idx].strip() if len(row) > idx else ""


def _parse_amount(raw: str) -> Decimal | None:
    """Parse a numeric cell, handling locale separators."""
    if not raw:
        return None
    cleaned = raw.replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        val = Decimal(cleaned)
    except Exception:  # noqa: BLE001
        return None
    return val if val != 0 else None


_DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y")


def _parse_month_from_date(raw: str) -> int | None:
    """Extract month number from a date string (DD.MM.YYYY or YYYY-MM-DD)."""
    if not raw:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).month  # noqa: DTZ007
        except ValueError:
            continue
    return None


def _parse_month_bare(raw: str) -> int | None:
    """Parse a bare month number 1..12."""
    if not raw or not raw.strip().isdigit():
        return None
    m = int(raw.strip())
    return m if 1 <= m <= _MONTHS_IN_YEAR else None


def _parse_month(raw: str, *, is_date: bool) -> int | None:
    return _parse_month_from_date(raw) if is_date else _parse_month_bare(raw)


def _read_amount(
    row: list[str],
    layout: IncomeLayout,
) -> tuple[Decimal, str] | None:
    """Read amount + currency from a row, trying primary then fallback column."""
    amount = _parse_amount(_cell(row, layout.col_amount))
    if amount is not None:
        return amount, layout.currency
    if layout.col_amount_fallback is not None:
        amount = _parse_amount(_cell(row, layout.col_amount_fallback))
        if amount is not None:
            return amount, layout.fallback_currency
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
    from dinary.services.nbs import get_rate

    rate_date = date(year, month, 1)
    rate_cur = get_rate(config_con, rate_date, currency)
    rate_eur = get_rate(config_con, rate_date, "EUR")
    return (amount * rate_cur / rate_eur).quantize(Decimal("0.01"))


def _aggregate_from_sheet(
    year: int,
    source: duckdb_repo.ImportSourceRow,
    layout: IncomeLayout,
) -> tuple[dict[int, Decimal], int]:
    """Read Income worksheet and return {month: eur_total} and row count."""
    ss = get_sheet(source.spreadsheet_id)
    ws = ss.worksheet(source.income_worksheet_name)
    all_values = ws.get_all_values()

    monthly_eur: dict[int, Decimal] = defaultdict(Decimal)
    rows_read = 0

    config_con = duckdb_repo.get_config_connection(read_only=False)
    try:
        for row_idx in range(layout.header_rows, len(all_values)):
            row = all_values[row_idx]
            month = _parse_month(_cell(row, layout.col_date), is_date=layout.month_is_date)
            if month is None:
                continue
            parsed = _read_amount(row, layout)
            if parsed is None:
                continue
            amount, currency = parsed
            amount_eur = _convert_to_eur(amount, currency, year, month, config_con)
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

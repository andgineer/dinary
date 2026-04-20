"""Import monthly income from Google Sheets into DuckDB.

Reads the Income/Balance worksheet for a given year, aggregates EUR totals
per month, and stores one row per (year, month) in budget_YYYY.duckdb.income.

Destructive re-import: wipes existing `income` rows for the target year
before inserting.
"""

import dataclasses
import logging
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from dinary.imports.expense_import import MONTHS_IN_YEAR
from dinary.services import duckdb_repo
from dinary.services.nbs import get_rate
from dinary.services.sheets import get_sheet

logger = logging.getLogger(__name__)


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
    except InvalidOperation:
        return None
    return val if val != 0 else None


_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y")
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


def _convert_to_eur_from_cache(
    amount: Decimal,
    currency: str,
    month: int,
    rates: dict[int, dict[str, Decimal | None]],
) -> Decimal | None:
    """Pure (no-DB, no-HTTP) version of ``_convert_to_eur``.

    Returns None when the prefetch could not resolve a needed rate for
    ``(month, currency)`` — caller must skip the row.
    """
    if currency == "EUR":
        return amount
    rate_cur = rates[month][currency]
    rate_eur = rates[month]["EUR"]
    if rate_cur is None or rate_eur is None:
        return None
    return (amount * rate_cur / rate_eur).quantize(Decimal("0.01"))


def _prefetch_monthly_rates(
    year: int,
    layout: IncomeLayout,
) -> dict[int, dict[str, Decimal | None]]:
    """Pre-fetch every (month, currency) rate the year-long aggregation
    will need, then close the writer connection BEFORE iterating the
    sheet rows.

    Why prefetch: ``aggregate_from_sheet`` used to hold a ``config.duckdb``
    writer connection across the entire row loop, calling ``get_rate``
    (which may hit NBS HTTP) per row. Holding the writer slot across
    multi-second HTTP round-trips blocks every other writer on
    ``config.duckdb``. The writer slot is now held only during this short
    prefetch (12 months x <=2 currencies, mostly cache hits).

    Resilience: a missing rate for some ``(month, currency)`` produces a
    ``None`` entry instead of raising. ``_convert_to_eur_from_cache``
    returns None for a None-rate cell and the caller drops the row.
    """
    currencies_to_fetch: set[str] = {"EUR", layout.currency}
    if layout.transition_currency is not None:
        currencies_to_fetch.add(layout.transition_currency)

    rates: dict[int, dict[str, Decimal | None]] = {}
    config_con = duckdb_repo.get_config_connection(read_only=False)
    try:
        for month in range(1, MONTHS_IN_YEAR + 1):
            rate_date = date(year, month, 1)
            month_rates: dict[str, Decimal | None] = {}
            for cur in currencies_to_fetch:
                try:
                    month_rates[cur] = get_rate(config_con, rate_date, cur)
                except ValueError:
                    month_rates[cur] = None
            rates[month] = month_rates
    finally:
        config_con.close()
    return rates


def aggregate_from_sheet(
    year: int,
    source: duckdb_repo.ImportSourceRow,
    layout: IncomeLayout,
) -> tuple[dict[int, Decimal], int]:
    """Read Income/Balance worksheet and return {month: eur_total} and row count.

    Only rows whose date falls within the target year are included.
    """
    rates = _prefetch_monthly_rates(year, layout)

    ss = get_sheet(source.spreadsheet_id)
    ws = ss.worksheet(source.income_worksheet_name)
    all_values = ws.get_all_values()

    monthly_eur: dict[int, Decimal] = defaultdict(Decimal)
    rows_aggregated = 0
    warned_missing_rate: set[tuple[int, str]] = set()

    for row_idx in range(layout.header_rows, len(all_values)):
        row = all_values[row_idx]
        parsed_date = _parse_date(_cell(row, layout.col_date))
        if parsed_date is None:
            continue
        row_year, month = parsed_date
        if row_year != year:
            continue
        if not 1 <= month <= MONTHS_IN_YEAR:
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

        amount_eur = _convert_to_eur_from_cache(amount, currency, month, rates)
        if amount_eur is None:
            key = (month, currency)
            if key not in warned_missing_rate:
                warned_missing_rate.add(key)
                logger.warning(
                    "Dropping income row(s) for %d-%02d (currency=%s): "
                    "no rate available; rebuild this year after the "
                    "rate cache is populated to recover.",
                    year,
                    month,
                    currency,
                )
            continue
        monthly_eur[month] += amount_eur
        rows_aggregated += 1

    return dict(monthly_eur), rows_aggregated


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
    """Destructive re-import of monthly income for a single year.

    Returns a dict that always carries ``year``, ``status``, and one of:
      * ``status="imported"``: success — ``rows_aggregated``,
        ``months_written``, ``total_eur`` are populated.
      * ``status="skipped"``: nothing to import — ``reason`` explains why.
    """
    source = duckdb_repo.get_import_source(year)
    if source is None:
        return {"year": year, "status": "skipped", "reason": "no import source registered"}
    if not source.income_worksheet_name:
        return {"year": year, "status": "skipped", "reason": "no income worksheet registered"}

    layout = _resolve_layout(year, source)
    monthly_eur, rows_aggregated = aggregate_from_sheet(year, source, layout)

    con = duckdb_repo.get_budget_connection(year)
    try:
        con.execute("BEGIN")
        try:
            con.execute("DELETE FROM income WHERE year = ?", [year])
            for month in sorted(monthly_eur):
                con.execute(
                    "INSERT INTO income (year, month, amount) VALUES (?, ?, ?)",
                    [year, month, float(monthly_eur[month])],
                )
            con.execute("COMMIT")
        except Exception:
            duckdb_repo.best_effort_rollback(con, context=f"import_year_income({year})")
            raise
    finally:
        con.close()

    months_written = len(monthly_eur)
    logger.info(
        "Imported income for %d: %d months from %d rows",
        year,
        months_written,
        rows_aggregated,
    )
    return {
        "year": year,
        "status": "imported",
        "rows_aggregated": rows_aggregated,
        "months_written": months_written,
        "total_eur": float(sum(monthly_eur.values())),
    }

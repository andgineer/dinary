"""Import monthly income from Google Sheets into DuckDB.

Reads the Income/Balance worksheet for a given year, aggregates totals
per month in the configured app currency (``settings.app_currency``),
and stores one row per (year, month) in ``data/dinary.duckdb.income``.

Destructive re-import: wipes existing `income` rows for the target year
before inserting.
"""

import dataclasses
import logging
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from dinary.config import settings
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


def _convert_to_app_from_cache(
    amount: Decimal,
    currency: str,
    month: int,
    rates: dict[int, dict[str, Decimal | None]],
    *,
    app_currency: str,
) -> Decimal | None:
    """Convert ``amount`` from ``currency`` to ``app_currency``.

    Uses the NBS rates cached by ``_prefetch_monthly_rates`` (all as
    ``RSD per 1 unit of X``). Returns ``None`` when the prefetch could
    not resolve a needed rate — caller must skip the row.

    ``rate_src`` entries use ``Decimal(1)`` for RSD by convention; the
    same holds for the app currency when it is RSD.
    """
    cu = currency.upper()
    ac = app_currency.upper()
    if cu == ac:
        return amount.quantize(Decimal("0.01"))
    rate_src = rates[month].get(cu)
    rate_app = rates[month].get(ac)
    if rate_src is None or rate_app is None:
        return None
    return (amount * rate_src / rate_app).quantize(Decimal("0.01"))


def _prefetch_monthly_rates(
    year: int,
    layout: IncomeLayout,
) -> dict[int, dict[str, Decimal | None]]:
    """Pre-fetch every (month, currency) rate the year-long aggregation
    will need, then close the writer connection BEFORE iterating the
    sheet rows.

    Why prefetch: ``aggregate_from_sheet`` used to hold a DB writer
    connection across the entire row loop, calling ``get_rate`` (which
    may hit NBS HTTP) per row. Holding the writer slot across
    multi-second HTTP round-trips blocks every other writer on the
    single ``data/dinary.duckdb``. The writer slot is now held only
    during this short prefetch (12 months x <=3 currencies, mostly
    cache hits).

    Resilience: a missing rate for some ``(month, currency)`` produces a
    ``None`` entry instead of raising. ``_convert_to_app_from_cache``
    returns None for a None-rate cell and the caller drops the row.
    """
    app_currency = settings.app_currency.upper()
    currencies_to_fetch: set[str] = {layout.currency.upper(), app_currency}
    if layout.transition_currency is not None:
        currencies_to_fetch.add(layout.transition_currency.upper())
    # RSD is the NBS anchor currency: its "rate" is implicit 1.0 and
    # ``get_rate`` would raise for it.
    currencies_to_fetch.discard("RSD")

    rates: dict[int, dict[str, Decimal | None]] = {}
    con = duckdb_repo.get_connection()
    try:
        for month in range(1, MONTHS_IN_YEAR + 1):
            rate_date = date(year, month, 1)
            month_rates: dict[str, Decimal | None] = {"RSD": Decimal(1)}
            for cur in currencies_to_fetch:
                try:
                    month_rates[cur] = get_rate(con, rate_date, cur)
                except ValueError:
                    month_rates[cur] = None
            rates[month] = month_rates
    finally:
        con.close()
    return rates


def aggregate_from_sheet(
    year: int,
    source: duckdb_repo.ImportSourceRow,
    layout: IncomeLayout,
) -> tuple[dict[int, Decimal], int]:
    """Read Income/Balance worksheet and return ``{month: app_total}`` and row count.

    Amounts are converted to ``settings.app_currency`` (RSD by default).
    Only rows whose date falls within the target year are included.
    """
    app_currency = settings.app_currency.upper()
    rates = _prefetch_monthly_rates(year, layout)

    ss = get_sheet(source.spreadsheet_id)
    ws = ss.worksheet(source.income_worksheet_name)
    all_values = ws.get_all_values()

    monthly_app: dict[int, Decimal] = defaultdict(Decimal)
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

        amount_app = _convert_to_app_from_cache(
            amount,
            currency,
            month,
            rates,
            app_currency=app_currency,
        )
        if amount_app is None:
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
        monthly_app[month] += amount_app
        rows_aggregated += 1

    return dict(monthly_app), rows_aggregated


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
        ``months_written``, ``total_app``, ``app_currency`` are populated.
      * ``status="skipped"``: nothing to import — ``reason`` explains why.
    """
    source = duckdb_repo.get_import_source(year)
    if source is None:
        return {"year": year, "status": "skipped", "reason": "no import source registered"}
    if not source.income_worksheet_name:
        return {"year": year, "status": "skipped", "reason": "no income worksheet registered"}

    layout = _resolve_layout(year, source)
    monthly_app, rows_aggregated = aggregate_from_sheet(year, source, layout)

    con = duckdb_repo.get_connection()
    try:
        con.execute("BEGIN")
        try:
            con.execute("DELETE FROM income WHERE year = ?", [year])
            for month in sorted(monthly_app):
                con.execute(
                    "INSERT INTO income (year, month, amount) VALUES (?, ?, ?)",
                    [year, month, float(monthly_app[month])],
                )
            con.execute("COMMIT")
        except Exception:
            duckdb_repo.best_effort_rollback(con, context=f"import_year_income({year})")
            raise
    finally:
        con.close()

    months_written = len(monthly_app)
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
        "total_app": float(sum(monthly_app.values())),
        "app_currency": settings.app_currency,
    }

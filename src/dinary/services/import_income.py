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
from decimal import Decimal, InvalidOperation

from dinary.services import duckdb_repo
from dinary.services.import_sheet import MONTHS_IN_YEAR
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
        # Narrow catch: `Decimal(cleaned)` can only raise InvalidOperation
        # here because `cleaned` is always a `str` by construction (the
        # `.replace(...)` chain guarantees it). The previous bare
        # `Exception` swallowed e.g. a TypeError if a caller broke the
        # `raw: str` contract by passing None — surfacing that as "cannot
        # parse" instead of as a real bug was actively unhelpful.
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
    """Pure (no-DB, no-HTTP) version of `_convert_to_eur`.

    Returns None when the prefetch could not resolve a needed rate for
    `(month, currency)` — caller must skip the row, mirroring the
    pre-prefetch behavior where `get_rate` would raise mid-loop and the
    row would be lost. EUR is short-circuited (the amount is already in
    the target unit). KeyError on a missing month/currency entry is
    deliberately *not* caught: the prefetch is supposed to populate
    every key it might be asked about, so a KeyError is a real bug.
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

    Why prefetch: `aggregate_from_sheet` used to hold a `config.duckdb`
    writer connection across the entire row loop, calling `get_rate`
    (which may hit NBS/Frankfurter HTTP) per row. Holding the writer
    slot across multi-second HTTP round-trips blocks every other writer
    on `config.duckdb` — chiefly `POST /api/expenses` on a rate-cache
    miss, which is exactly the runtime path operators care about. Per
    the same rationale documented on `seed_from_sheet`, the writer slot
    is now held only during this short prefetch (12 months × ≤2
    currencies, mostly cache hits) and aggregation runs against an
    in-memory dict with no DB connection at all.

    Resilience: a missing rate for some `(month, currency)` produces a
    `None` entry instead of raising. The old per-row implementation only
    invoked `get_rate` for months that actually had sheet rows, so an
    unfetchable rate for a month with zero rows was harmless AND silent.
    The prefetch unconditionally walks all 12 months × all candidate
    currencies, so we MUST tolerate per-cell failure to preserve the
    correctness half of that behavior — otherwise an unfetchable rate
    for an empty month would abort an `inv rebuild-income` that
    previously imported cleanly. `_convert_to_eur_from_cache` returns
    None for a None-rate cell and the caller drops the row.

    Logging policy (K3): we do NOT warn here when a single cell fails
    to fetch — `aggregate_from_sheet` warns at the row-skip site
    instead, deduplicated by `(month, currency)`. That keeps log lines
    proportional to actually-lost rows: a year with sparse months no
    longer produces 12 WARNINGs that don't correspond to any data.
    `get_rate` itself logs at INFO/WARNING when it does HTTP work, so
    operational visibility into rate-fetch attempts is preserved.

    EUR rate is always fetched (denominator). The base currency
    (`layout.currency`) and any `layout.transition_currency` are also
    fetched. RSD short-circuits in `get_rate` to Decimal(1) without
    touching the DB.
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
                # Cache hit is one SELECT; a real miss does NBS HTTP and
                # writes the cache. ValueError surfaces from get_rate
                # when neither NBS nor Frankfurter has data after a
                # 10-day backwalk — record None and let the per-row
                # caller decide whether to skip (typically yes; the
                # alternative is an all-or-nothing year import that
                # regresses against the per-row behavior). Silent here
                # by design — see K3 note in docstring.
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
    """Read Income/Balance worksheet and return {month: eur_total} and aggregated row count.

    Only rows whose date falls within the target year are included — Balance
    tabs accumulate rows across multiple years. The returned count
    (`rows_aggregated`, K7) is the number of rows that successfully made
    it into `monthly_eur`; rows skipped for unparseable date/amount or
    for an unfetchable rate are excluded.

    The currency conversion uses a precomputed rate map (see
    `_prefetch_monthly_rates`) so the per-row loop runs with zero open DB
    connections — no `config.duckdb` writer slot is held while we iterate
    Sheets row data, eliminating contention with `POST /api/expenses`.
    """
    rates = _prefetch_monthly_rates(year, layout)

    ss = get_sheet(source.spreadsheet_id)
    ws = ss.worksheet(source.income_worksheet_name)
    all_values = ws.get_all_values()

    monthly_eur: dict[int, Decimal] = defaultdict(Decimal)
    rows_aggregated = 0
    # K3: dedup unfetchable-rate warnings by `(month, currency)` so a
    # year with N rows sharing the same missing rate logs one WARNING,
    # not N. The set is per-`aggregate_from_sheet` call (per-year),
    # which matches operator mental model: "what did I lose for THIS
    # year's rebuild?"
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
            # Prefetch could not resolve a rate for this (month, currency).
            # Skip the row and warn once per offending pair so the operator
            # sees that data was actually dropped (vs. the prefetch's
            # silent None-fill which intentionally never warns — see K3
            # note on `_prefetch_monthly_rates`).
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

    Returns a dict that always carries `year`, `status`, and one of:
      * `status="imported"`: success — `rows_aggregated`,
        `months_written`, `total_eur` are populated.
      * `status="skipped"`: nothing to import — `reason` explains why.

    Downstream callers (notably `inv rebuild-income-all`) `json.dumps` the
    result one line per year; the stable shape lets them filter on
    `status` instead of probing for the presence of specific keys.
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
        # Wrap DELETE + per-month INSERT in a single transaction. `income`
        # has no FK children (unlike the `expenses` wipe in import_sheet),
        # so DuckDB's "child-tombstones-not-visible-mid-txn" limitation
        # doesn't apply here. The transactional wrap prevents the
        # "process killed mid-loop -> only Jan-Apr present" failure mode
        # that autocommit would leave the operator to clean up by hand.
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
        # K7: previously named `rows_read`. The count excludes rows
        # skipped for unparseable date/amount or for an unfetchable
        # rate, so "aggregated" matches what the number actually means.
        # External operators consuming `inv rebuild-income` JSON output
        # need to update their key name.
        "rows_aggregated": rows_aggregated,
        "months_written": months_written,
        "total_eur": float(sum(monthly_eur.values())),
    }

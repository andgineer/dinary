"""NBS exchange rate client with DuckDB caching.

Fetches middle rates from kurs.resenje.org (National Bank of Serbia).
Rates are cached in config.duckdb exchange_rates table.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

import holidays
import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

BASE_URL = "https://kurs.resenje.org/api/v1"

_rs_holidays = holidays.country_holidays("RS")
_FIRST_WEEKEND_WEEKDAY = 5


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _fetch_rate(rate_date: date, currency: str) -> Decimal | None:
    url = f"{BASE_URL}/currencies/{currency.lower()}/rates/{rate_date.isoformat()}"
    resp = httpx.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if "exchange_middle" in data:
        return Decimal(str(data["exchange_middle"]))
    return None


def _get_cached(con, rate_date: date, currency: str) -> Decimal | None:
    row = con.execute(
        "SELECT rate FROM exchange_rates WHERE date = ? AND currency = ?",
        [rate_date, currency],
    ).fetchone()
    return Decimal(str(row[0])) if row else None


def _save_cache(con, rate_date: date, currency: str, rate: Decimal) -> None:
    con.execute(
        "INSERT INTO exchange_rates (date, currency, rate) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
        [rate_date, currency, rate],
    )


def get_rate(con, rate_date: date, currency: str) -> Decimal:
    """Get NBS middle rate for currency on date (1 unit = ? RSD).

    Uses DuckDB cache in config.duckdb. Falls back to previous working days
    if the target date is a weekend/holiday.
    Returns Decimal(1) for RSD.
    """
    if currency.upper() == "RSD":
        return Decimal(1)

    target = rate_date
    for _ in range(10):
        cached = _get_cached(con, target, currency)
        if cached:
            if target != rate_date:
                _save_cache(con, rate_date, currency, cached)
            return cached

        if target.weekday() >= _FIRST_WEEKEND_WEEKDAY or target in _rs_holidays:
            target -= timedelta(days=1)
            continue

        try:
            rate_val = _fetch_rate(target, currency)
            if rate_val:
                _save_cache(con, target, currency, rate_val)
                if target != rate_date:
                    _save_cache(con, rate_date, currency, rate_val)
                return rate_val
        except (httpx.HTTPError, ValueError):
            logger.debug("Failed to fetch rate for %s on %s", currency, target)

        target -= timedelta(days=1)

    msg = f"Could not find NBS rate for {currency} on {rate_date} (checked 10 days back)"
    raise ValueError(msg)


def convert_to_eur(
    con,
    amount_original: Decimal,
    currency_original: str,
    rate_date: date,
) -> Decimal:
    """Convert amount in currency_original to EUR using NBS cross-rate.

    Formula: amount_eur = amount_original * rate(currency) / rate(EUR)
    Both rates are NBS middle rates in RSD.
    """
    if currency_original.upper() == "EUR":
        return amount_original

    rate_cur = get_rate(con, rate_date, currency_original)
    rate_eur = get_rate(con, rate_date, "EUR")
    return (amount_original * rate_cur / rate_eur).quantize(Decimal("0.01"))

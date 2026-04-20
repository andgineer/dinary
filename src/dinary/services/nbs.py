"""NBS exchange rate client with DuckDB caching.

Fetches middle rates from kurs.resenje.org (National Bank of Serbia).
Rates are cached in the ``exchange_rates`` table in ``data/dinary.duckdb``.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

import holidays
import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

BASE_URL = "https://kurs.resenje.org/api/v1"
FRANKFURTER_URL = "https://api.frankfurter.dev/v1"

_rs_holidays = holidays.country_holidays("RS")
_FIRST_WEEKEND_WEEKDAY = 5
_HTTP_NOT_FOUND = 404


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _fetch_rate(rate_date: date, currency: str) -> Decimal | None:
    url = f"{BASE_URL}/currencies/{currency.lower()}/rates/{rate_date.isoformat()}"
    resp = httpx.get(url, timeout=10)
    if resp.status_code == _HTTP_NOT_FOUND:
        return None
    resp.raise_for_status()
    data = resp.json()
    if "exchange_middle" in data:
        return Decimal(str(data["exchange_middle"]))
    return None


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _fetch_frankfurter_pair(rate_date: date, base: str, quote: str) -> Decimal | None:
    """Fetch one unit of `base` expressed in `quote` from Frankfurter (ECB historical).

    Used as a fallback when NBS has no data (e.g. RUB rates for 2012).
    Frankfurter returns the nearest previous working day when the given date
    is a weekend/holiday, so we don't need our own walk-back for it.
    """
    url = f"{FRANKFURTER_URL}/{rate_date.isoformat()}"
    resp = httpx.get(url, params={"base": base, "symbols": quote}, timeout=10)
    if resp.status_code == _HTTP_NOT_FOUND:
        return None
    resp.raise_for_status()
    data = resp.json()
    rates = data.get("rates") or {}
    if quote in rates:
        return Decimal(str(rates[quote]))
    return None


def _fetch_rate_frankfurter_to_rsd(
    con,
    rate_date: date,
    currency: str,
) -> Decimal | None:
    """Cross-rate CURRENCY -> RSD using Frankfurter for CURRENCY->EUR and NBS for EUR->RSD.

    Frankfurter does not support RSD directly. NBS has EUR->RSD back to 2002
    but no RUB before late December 2012. This function bridges the two so
    that RUB amounts from 2012 can still be converted.
    """
    cur_eur = _fetch_frankfurter_pair(rate_date, currency, "EUR")
    if cur_eur is None or cur_eur == 0:
        return None
    eur_rsd = _get_cached(con, rate_date, "EUR")
    if eur_rsd is None:
        eur_rsd = _fetch_rate(rate_date, "EUR")
        if eur_rsd is None:
            return None
        _save_cache(con, rate_date, "EUR", eur_rsd)
    return (cur_eur * eur_rsd).quantize(Decimal("0.0001"))


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


def _resolve_from_nbs(con, rate_date: date, currency: str) -> Decimal | None:
    """Walk back up to 10 days looking for an NBS rate (cache or API)."""
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
        except (httpx.HTTPError, ValueError):
            logger.debug("Failed to fetch rate for %s on %s", currency, target)
            rate_val = None
        if rate_val:
            _save_cache(con, target, currency, rate_val)
            if target != rate_date:
                _save_cache(con, rate_date, currency, rate_val)
            return rate_val

        target -= timedelta(days=1)
    return None


def _resolve_from_frankfurter(con, rate_date: date, currency: str) -> Decimal | None:
    try:
        fallback = _fetch_rate_frankfurter_to_rsd(con, rate_date, currency)
    except (httpx.HTTPError, ValueError):
        return None
    if fallback:
        logger.info("Using Frankfurter fallback for %s on %s", currency, rate_date)
        _save_cache(con, rate_date, currency, fallback)
    return fallback


def get_rate(con, rate_date: date, currency: str) -> Decimal:
    """Get NBS middle rate for currency on date (1 unit = ? RSD).

    Uses DuckDB cache in data/dinary.duckdb. Falls back to previous working days
    if the target date is a weekend/holiday, then to Frankfurter (ECB) when
    NBS has no data for the currency (e.g. RUB before Dec 2012).
    Returns Decimal(1) for RSD.
    """
    if currency.upper() == "RSD":
        return Decimal(1)

    rate = _resolve_from_nbs(con, rate_date, currency)
    if rate is not None:
        return rate

    rate = _resolve_from_frankfurter(con, rate_date, currency)
    if rate is not None:
        return rate

    msg = f"Could not find NBS rate for {currency} on {rate_date} (checked 10 days back)"
    raise ValueError(msg)


def convert(
    con,
    amount: Decimal,
    from_ccy: str,
    to_ccy: str,
    rate_date: date,
) -> tuple[Decimal, Decimal]:
    """Convert *amount* from *from_ccy* to *to_ccy* using NBS rates.

    Returns ``(converted_amount, rate)`` where ``rate`` is the
    from_ccy/to_ccy exchange rate used (1 unit of from_ccy = rate units
    of to_ccy). When ``from_ccy == to_ccy``, returns ``(amount, 1)``.
    """
    if from_ccy.upper() == to_ccy.upper():
        return amount, Decimal(1)

    rate_from = get_rate(con, rate_date, from_ccy)
    rate_to = get_rate(con, rate_date, to_ccy)
    rate = (rate_from / rate_to).quantize(Decimal("0.000001"))
    converted = (amount * rate).quantize(Decimal("0.01"))
    return converted, rate

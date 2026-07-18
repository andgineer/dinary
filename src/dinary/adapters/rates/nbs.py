"""NBS (National Bank of Serbia) exchange rate client.

Fetches middle rates from kurs.resenje.org. Only used when one of the
currencies in a pair is RSD.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

import holidays
from cachetools import TTLCache, cached

from dinary.adapters.rates.helpers import (
    _FETCH_RATE_CACHE_TIME,
    _get_json_or_none,
    get_db_rate,
    save_db_rate,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://kurs.resenje.org/api/v1"

_rs_holidays = holidays.country_holidays("RS")
_FIRST_WEEKEND_WEEKDAY = 5
_RATE_LOOKBACK_DAYS = 10


def _is_working_day(d: date) -> bool:
    return d.weekday() < _FIRST_WEEKEND_WEEKDAY and d not in _rs_holidays


@cached(cache=TTLCache(maxsize=10000, ttl=_FETCH_RATE_CACHE_TIME))
def _fetch_nbs_rate(rate_date: date, currency: str) -> Decimal | None:
    """``None`` (HTTP failure) is intentionally cached for the full TTL so a down
    NBS service isn't hammered with retries — do NOT "fix" this by skipping it."""
    url = f"{BASE_URL}/currencies/{currency.lower()}/rates/{rate_date.isoformat()}"
    data = _get_json_or_none(url)
    if data and "exchange_middle" in data:
        logger.info("Got rate from NBS")
        return Decimal(str(data["exchange_middle"]))
    return None


def resolve_from_nbs(con, rate_date: date, source: str, target: str) -> Decimal | None:
    """Stores the found rate under ``rate_date`` only when it's a non-working day
    and the rate is from the immediately previous working day — an older fallback
    is returned but never aliased, since stale rates must not masquerade as current."""
    # NBS gives "1 currency = X RSD". Determine which side is RSD.
    if target.upper() == "RSD":
        nbs_currency = source
        invert = False
    else:
        nbs_currency = target
        invert = True

    rate_date_is_working = _is_working_day(rate_date)
    working_day_without_rate_seen = False
    for i in range(_RATE_LOOKBACK_DAYS):
        check_date = rate_date - timedelta(days=i)

        db_rate = get_db_rate(con, check_date, source, target)
        if db_rate:
            if (
                check_date != rate_date
                and not rate_date_is_working
                and not working_day_without_rate_seen
            ):
                save_db_rate(con, rate_date, source, target, db_rate)
            return db_rate

        if not _is_working_day(check_date):
            continue

        nbs_rate = _fetch_nbs_rate(check_date, nbs_currency)
        if nbs_rate:
            rate_val = (Decimal(1) / nbs_rate).quantize(Decimal("0.000001")) if invert else nbs_rate
            save_db_rate(con, check_date, source, target, rate_val)
            if not rate_date_is_working and not working_day_without_rate_seen:
                save_db_rate(con, rate_date, source, target, rate_val)
            return rate_val
        working_day_without_rate_seen = True
    return None

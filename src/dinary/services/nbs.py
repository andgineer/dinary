"""NBS (National Bank of Serbia) exchange rate client.

Fetches middle rates from kurs.resenje.org. Only used when one of the
currencies in a pair is RSD.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

import holidays
from cachetools import TTLCache, cached

from dinary.services.rate_helpers import (
    _FETCH_RATE_CACHE_TIME,
    _get_db_rate,
    _get_json_or_none,
    _save_db_rate,
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
    """Fetch NBS middle rate: 1 unit of `currency` = ? RSD.

    ``None`` (HTTP failure) is intentionally cached for the full TTL so
    that a down NBS service is not hammered with retries on every request.
    Callers fall back to DB rates when this returns ``None``.
    Do NOT "fix" this by skipping ``None`` caching.
    """
    url = f"{BASE_URL}/currencies/{currency.lower()}/rates/{rate_date.isoformat()}"
    data = _get_json_or_none(url)
    if data and "exchange_middle" in data:
        logger.info("Got rate from NBS")
        return Decimal(str(data["exchange_middle"]))
    return None


def _resolve_from_nbs(con, rate_date: date, source: str, target: str) -> Decimal | None:
    """Walk back up to _RATE_LOOKBACK_DAYS looking for an NBS rate (DB or API).

    Stores the found rate under ``rate_date`` only when ``rate_date``
    is a non-working day AND the rate comes from the immediately
    previous working day. If that day has no rate (e.g. NBS was
    down) and the walkback finds an older rate, it is returned but
    not aliased under ``rate_date`` — stale rates must not masquerade
    as current ones.

    On a regular working day a pre-publication miss walks back to the
    previous day's rate but does not write it to DB — the real
    rate will be stored on the first request after NBS publishes
    (~08:00 Belgrade time).
    """
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

        db_rate = _get_db_rate(con, check_date, source, target)
        if db_rate:
            if (
                check_date != rate_date
                and not rate_date_is_working
                and not working_day_without_rate_seen
            ):
                _save_db_rate(con, rate_date, source, target, db_rate)
            return db_rate

        if not _is_working_day(check_date):
            continue

        nbs_rate = _fetch_nbs_rate(check_date, nbs_currency)
        if nbs_rate:
            rate_val = (Decimal(1) / nbs_rate).quantize(Decimal("0.000001")) if invert else nbs_rate
            _save_db_rate(con, check_date, source, target, rate_val)
            if not rate_date_is_working and not working_day_without_rate_seen:
                _save_db_rate(con, rate_date, source, target, rate_val)
            return rate_val
        working_day_without_rate_seen = True
    return None

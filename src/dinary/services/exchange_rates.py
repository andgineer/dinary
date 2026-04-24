"""Exchange rate service with SQLite storage.

Fetches rates from kurs.resenje.org (NBS) when one of the currencies is RSD,
and from api.frankfurter.dev (ECB) otherwise. Rates are stored in the
``exchange_rates`` table in ``data/dinary.db`` as
``1 source_currency * rate = amount in target_currency``.
"""

import logging
from datetime import date
from decimal import Decimal

from cachetools import TTLCache, cached

from dinary.services.nbs import _resolve_from_nbs
from dinary.services.rate_helpers import (
    _FETCH_RATE_CACHE_TIME,
    _get_db_rate,
    _get_json_or_none,
    _get_latest_db_rate,
    _save_db_rate,
)

logger = logging.getLogger(__name__)

FRANKFURTER_URL = "https://api.frankfurter.dev/v1"


# ---------------------------------------------------------------------------
# Frankfurter fetcher (TTL-cached to avoid rate-limiting)
# ---------------------------------------------------------------------------


@cached(cache=TTLCache(maxsize=10000, ttl=_FETCH_RATE_CACHE_TIME))
def _fetch_frankfurter_rate(rate_date: date, source: str, target: str) -> Decimal | None:
    """Fetch rate from Frankfurter (ECB): 1 unit of `source` = ? `target`.

    Frankfurter returns the nearest previous working day when the given date
    is a weekend/holiday, so we don't need our own walk-back for it.

    ``None`` (HTTP failure) is intentionally cached for the full TTL so
    that a down Frankfurter service is not hammered with retries on every
    request.  Callers fall back to DB rates when this returns ``None``.
    Do NOT "fix" this by skipping ``None`` caching.
    """
    url = f"{FRANKFURTER_URL}/{rate_date.isoformat()}"
    data = _get_json_or_none(url, params={"base": source, "symbols": target})
    if data:
        rates = data.get("rates") or {}
        if target in rates:
            logger.info("Got rate from frankfurter")
            return Decimal(str(rates[target]))
    return None


# ---------------------------------------------------------------------------
# Frankfurter resolution
# ---------------------------------------------------------------------------


def _resolve_from_frankfurter(con, rate_date: date, source: str, target: str) -> Decimal | None:
    """Fetch rate from Frankfurter. Falls back to last known DB rate on failure."""
    rate = _fetch_frankfurter_rate(rate_date, source, target)
    if rate is not None:
        _save_db_rate(con, rate_date, source, target, rate)
        return rate
    fallback = _get_latest_db_rate(con, source, target)
    if fallback is not None:
        logger.debug("Frankfurter unavailable, using last known rate for %s/%s", source, target)
    return fallback


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_rate(
    con,
    rate_date: date,
    source: str,
    target: str,
    *,
    offline: bool = False,
) -> Decimal:
    """Get exchange rate: ``amount_source * rate = amount_target``.

    When *offline* is True the DB is checked first; if a rate is already
    stored the function returns immediately without any HTTP calls.
    This keeps API request latency low — the background prefetch task
    populates the DB ahead of time.  If the DB has no rate, the normal
    online resolution path runs as a fallback.

    Uses NBS when one of the currencies is RSD, Frankfurter (ECB) otherwise.
    Rates are stored in the exchange_rates table for subsequent lookups.
    """
    if source.upper() == target.upper():
        return Decimal(1)

    if offline:
        db_rate = _get_db_rate(con, rate_date, source, target)
        if db_rate is not None:
            return db_rate

    rsd_involved = source.upper() == "RSD" or target.upper() == "RSD"

    if rsd_involved:
        rate = _resolve_from_nbs(con, rate_date, source, target)
        if rate is not None:
            return rate

    rate = _resolve_from_frankfurter(con, rate_date, source, target)
    if rate is not None:
        return rate

    msg = f"Could not find rate for {source}/{target} on {rate_date}"
    raise ValueError(msg)

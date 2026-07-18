"""NBP (Polish National Bank) exchange rate client — fallback for NBS.
Bridges any pair through PLN: ``X/Y = (X/PLN) / (Y/PLN)``.
See ``specs/reference/currencies.md``.
"""

import logging
from datetime import date
from decimal import Decimal

from cachetools import TTLCache, cached

from dinary.adapters.rates.helpers import (
    _FETCH_RATE_CACHE_TIME,
    _get_json_or_none,
    save_db_rate,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.nbp.pl/api/exchangerates"
_TABLES = ("A", "B")


@cached(cache=TTLCache(maxsize=10000, ttl=_FETCH_RATE_CACHE_TIME))
def _fetch_nbp_pln_leg(rate_date: date | None, currency: str) -> Decimal | None:
    """Tries table A (daily majors) then B (weekly). ``rate_date=None`` requests
    the most recent rate, used as fallback when a date-specific lookup 404s.
    Failures are intentionally cached as ``None`` for the full TTL — do NOT
    "fix" this by skipping it (same DOS-guard rationale as NBS)."""
    for table in _TABLES:
        path = f"rates/{table}/{currency.lower()}"
        url = (
            f"{BASE_URL}/{path}/{rate_date.isoformat()}/"
            if rate_date is not None
            else f"{BASE_URL}/{path}/"
        )
        data = _get_json_or_none(url, params={"format": "json"})
        if data and data.get("rates"):
            logger.info("Got rate from NBP table %s for %s", table, currency)
            return Decimal(str(data["rates"][0]["mid"]))
    return None


def _pln_leg(rate_date: date, currency: str) -> Decimal | None:
    """Falls back to NBP's "latest published" no-date form, which covers
    table-B currencies on a non-Wednesday request."""
    if currency.upper() == "PLN":
        return Decimal(1)
    rate = _fetch_nbp_pln_leg(rate_date, currency)
    if rate is not None:
        return rate
    return _fetch_nbp_pln_leg(None, currency)


def resolve_from_nbp(con, rate_date: date, source: str, target: str) -> Decimal | None:
    """Bridges through PLN; the result is cached so subsequent lookups (including
    the rate-prefetch task and ``offline=True`` requests) skip the two HTTP calls."""
    # Identity short-circuit: ``get_rate`` already does this, but
    # guarding here too keeps direct callers (and the unit tests) from
    # writing a useless ``(X, X, 1)`` row into ``exchange_rates``.
    if source.upper() == target.upper():
        return Decimal(1)
    rate_src = _pln_leg(rate_date, source)
    if rate_src is None:
        return None
    rate_tgt = _pln_leg(rate_date, target)
    if rate_tgt is None:
        return None
    bridged = (rate_src / rate_tgt).quantize(Decimal("0.000001"))
    save_db_rate(con, rate_date, source, target, bridged)
    return bridged

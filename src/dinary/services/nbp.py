"""NBP (Polish National Bank) exchange rate client.

Used as the fallback for NBS (``kurs.resenje.org``) — same role
Frankfurter used to play, but with coverage that actually overlaps
ours: NBP quotes 148 currencies as ``1 X = N PLN``:

* **Table A** (32 majors: USD, EUR, GBP, JPY, CHF, …) — published
  daily on Polish working days.
* **Table B** (116 less-common: RSD, BAM, MKD, BYN, RUB, AED, …) —
  published *weekly*, every Wednesday.

Any pair is resolved by bridging through PLN:
``X/Y = (X/PLN) / (Y/PLN)``. The two legs are queried independently
so we transparently mix table A and table B (e.g. ``RSD/EUR`` =
RSD-from-table-B / EUR-from-table-A).

Why NBP and not "the official NBS SOAP / Frankfurter / CBR / …":

* Free, no auth, REST + JSON.
* Polish ECB-aligned source — geopolitically neutral for a
  Belgrade-deployed app.
* Lists every currency NBS lists (RSD/BAM/MKD/BYN/RUB) **plus** the
  full ECB roster (USD/EUR/GBP/JPY/...). So when NBS is unavailable
  the fallback can serve the same pairs we already serve.

Date semantics:

* Table A: any Polish working day → 200; weekend/holiday → 404.
* Table B: only Wednesdays → 200; other days → 404. Up to 6 days
  stale data is acceptable for a fallback.
* When a date-specific lookup 404s we fall back to the no-date form
  (``rates/{table}/{code}/``) which returns the most recently
  published rate.
"""

import logging
from datetime import date
from decimal import Decimal

from cachetools import TTLCache, cached

from dinary.services.rate_helpers import (
    _FETCH_RATE_CACHE_TIME,
    _get_json_or_none,
    _save_db_rate,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.nbp.pl/api/exchangerates"
_TABLES = ("A", "B")


@cached(cache=TTLCache(maxsize=10000, ttl=_FETCH_RATE_CACHE_TIME))
def _fetch_nbp_pln_leg(rate_date: date | None, currency: str) -> Decimal | None:
    """Fetch ``1 currency = N PLN`` from NBP for *rate_date*.

    Tries table A first (cheaper, daily majors), then table B
    (weekly less-common). ``rate_date=None`` requests the most
    recently published rate; callers use this as a fallback when the
    date-specific lookup returns 404 (table B publishes weekly so
    most weekdays 404).

    HTTP failures and 404s are intentionally cached as ``None`` for
    the full TTL — the same DOS guard rationale as NBS. Do NOT
    "fix" this by skipping ``None`` caching.
    """
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
    """Resolve ``1 currency = N PLN`` for *rate_date*, with fallbacks.

    Order of attempts:
        1. Identity short-circuit if *currency* is PLN.
        2. NBP for *rate_date* directly (table A, then table B).
        3. NBP "latest published" no-date form. This is the path that
           covers table-B currencies on a non-Wednesday request.
    """
    if currency.upper() == "PLN":
        return Decimal(1)
    rate = _fetch_nbp_pln_leg(rate_date, currency)
    if rate is not None:
        return rate
    return _fetch_nbp_pln_leg(None, currency)


def _resolve_from_nbp(con, rate_date: date, source: str, target: str) -> Decimal | None:
    """Resolve ``source/target`` via NBP, bridging through PLN.

    Returns ``None`` when NBP has no rate for either side. The
    bridged value is written to ``exchange_rates`` so subsequent
    lookups (including the rate-prefetch task and ``offline=True``
    requests) short-circuit the two HTTP calls.
    """
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
    _save_db_rate(con, rate_date, source, target, bridged)
    return bridged

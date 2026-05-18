"""Exchange rate service: NBS primary, NBP fallback, SQLite cache.

Resolution: RSD pair → NBS direct → NBP via PLN.
           Non-RSD pair → NBS via RSD → NBP via PLN.
Stored as ``1 source * rate = N target``; inverse written alongside.
See ``specs/reference/exchange-rates.md``.
"""

import logging
from datetime import date
from decimal import Decimal

from dinary.adapters.nbp import _resolve_from_nbp
from dinary.adapters.nbs import _resolve_from_nbs
from dinary.adapters.rate_helpers import _get_db_rate, _save_db_rate

logger = logging.getLogger(__name__)


def _bridge_through_rsd_via_nbs(
    con,
    rate_date: date,
    source: str,
    target: str,
) -> Decimal | None:
    """Resolve a non-RSD pair as ``source/RSD * RSD/target`` via NBS.

    NBS quotes everything as ``1 X = N RSD``, so any pair becomes a
    two-leg bridge through RSD. Returns ``None`` when either leg has
    no NBS rate. The bridged value (and its inverse) is written into
    the rates DB so subsequent lookups short-circuit the two NBS
    calls.
    """
    rate_src_rsd = _resolve_from_nbs(con, rate_date, source, "RSD")
    if rate_src_rsd is None:
        return None
    rate_rsd_tgt = _resolve_from_nbs(con, rate_date, "RSD", target)
    if rate_rsd_tgt is None:
        return None
    bridged = (rate_src_rsd * rate_rsd_tgt).quantize(Decimal("0.000001"))
    _save_db_rate(con, rate_date, source, target, bridged)
    return bridged


def get_rate(
    con,
    rate_date: date,
    source: str,
    target: str,
    *,
    offline: bool = False,
) -> Decimal:
    """Get exchange rate: ``amount_source * rate = amount_target``.

    When *offline* is True the DB is checked first; if a rate is
    already stored the function returns immediately without any HTTP
    calls. This keeps API request latency low — the background
    prefetch task populates the DB ahead of time. If the DB has no
    rate, the normal online resolution path runs as a fallback.

    See the module docstring for the full multi-source resolution
    policy. Briefly: NBS first (direct for RSD pairs, RSD-bridge for
    the rest), NBP second (PLN-bridge) when NBS is unavailable or
    doesn't list one of the sides.
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
    else:
        bridged = _bridge_through_rsd_via_nbs(con, rate_date, source, target)
        if bridged is not None:
            return bridged

    # Reaching here means NBS could not serve the pair — log it so a
    # sustained NBS outage shows up in journald instead of being
    # silently masked by the fallback.
    logger.warning(
        "NBS could not resolve %s/%s on %s, falling back to NBP",
        source,
        target,
        rate_date,
    )
    rate = _resolve_from_nbp(con, rate_date, source, target)
    if rate is not None:
        return rate

    msg = f"Could not find rate for {source}/{target} on {rate_date}"
    raise ValueError(msg)

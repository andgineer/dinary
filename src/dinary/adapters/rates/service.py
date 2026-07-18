"""Exchange rate service: NBS primary, NBP fallback, SQLite cache.
Stored as ``1 source * rate = N target``; inverse written alongside.
See ``specs/reference/currencies.md``.
"""

import logging
import sqlite3
from datetime import date
from decimal import Decimal

from dinary.adapters.rates.helpers import get_db_rate, save_db_rate
from dinary.adapters.rates.nbp import resolve_from_nbp
from dinary.adapters.rates.nbs import resolve_from_nbs
from dinary.config import settings

logger = logging.getLogger(__name__)


def _bridge_through_rsd_via_nbs(
    con,
    rate_date: date,
    source: str,
    target: str,
) -> Decimal | None:
    """NBS quotes everything as ``1 X = N RSD``, so any pair bridges through RSD.
    The bridged value is cached so subsequent lookups skip the two NBS calls."""
    rate_src_rsd = resolve_from_nbs(con, rate_date, source, "RSD")
    if rate_src_rsd is None:
        return None
    rate_rsd_tgt = resolve_from_nbs(con, rate_date, "RSD", target)
    if rate_rsd_tgt is None:
        return None
    bridged = (rate_src_rsd * rate_rsd_tgt).quantize(Decimal("0.000001"))
    save_db_rate(con, rate_date, source, target, bridged)
    return bridged


def get_rate(
    con,
    rate_date: date,
    source: str,
    target: str,
    *,
    offline: bool = False,
) -> Decimal:
    """``amount_source * rate = amount_target``. When ``offline``, checks the DB
    first and returns without HTTP calls if already cached (the background
    prefetch task populates it ahead of time), falling back to online resolution."""
    if source.upper() == target.upper():
        return Decimal(1)

    if offline:
        db_rate = get_db_rate(con, rate_date, source, target)
        if db_rate is not None:
            return db_rate

    rsd_involved = source.upper() == "RSD" or target.upper() == "RSD"

    if rsd_involved:
        rate = resolve_from_nbs(con, rate_date, source, target)
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
    rate = resolve_from_nbp(con, rate_date, source, target)
    if rate is not None:
        return rate

    msg = f"Could not find rate for {source}/{target} on {rate_date}"
    raise ValueError(msg)


def convert_to_accounting_amount(
    con: sqlite3.Connection,
    amount: Decimal,
    currency: str,
    rate_date: date,
) -> Decimal:
    """Convert ``amount`` in ``currency`` to the accounting currency, quantized to cents.

    Raises ``ValueError`` if no rate is available for the pair on ``rate_date``.
    """
    rate = get_rate(con, rate_date, currency, settings.accounting_currency, offline=True)
    return (amount * rate).quantize(Decimal("0.01"))

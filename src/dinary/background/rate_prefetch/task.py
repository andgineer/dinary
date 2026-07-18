"""Background task that prefetches today's exchange rate at 08:00 Belgrade time
(when kurs.resenje.org publishes NBS rates); retries every 30 minutes on failure.
See ``specs/reference/architecture.md`` for why this write is load-bearing beyond
the rate itself."""

import asyncio
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from dinary.adapters.rates.helpers import get_db_rate
from dinary.adapters.rates.service import get_rate
from dinary.config import settings
from dinary.db import storage

logger = logging.getLogger(__name__)

_BELGRADE = ZoneInfo("Europe/Belgrade")
_PREFETCH_HOUR = 8
_RETRY_INTERVAL_SEC = 1800  # 30 minutes


def _seconds_until_prefetch_hour() -> float:
    """Seconds until the next _PREFETCH_HOUR Belgrade time; DST-correct via ZoneInfo."""
    now = datetime.now(tz=_BELGRADE)
    target = now.replace(hour=_PREFETCH_HOUR, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max((target - now).total_seconds(), _RETRY_INTERVAL_SEC)


def _get_rate_blocking(rate_date: date, source: str, target: str) -> Decimal:
    """Runs in a worker thread; opens its own connection since sharing a SQLite
    handle across threads causes CPython 3.14 access violations."""
    with storage.connection() as con:
        return get_rate(con, rate_date, source, target)


async def rate_prefetch_task() -> None:
    source = settings.app_currency
    target = settings.accounting_currency

    while True:
        try:
            now_belgrade = datetime.now(tz=_BELGRADE)
            today = now_belgrade.date()

            if now_belgrade.hour < _PREFETCH_HOUR:
                await asyncio.sleep(_seconds_until_prefetch_hour())
                continue

            with storage.connection() as con:
                already_stored = get_db_rate(con, today, source, target) is not None

            if already_stored:
                logger.debug("rate for %s already stored", today)
                await asyncio.sleep(_seconds_until_prefetch_hour())
                continue

            rate = await asyncio.to_thread(_get_rate_blocking, today, source, target)

            with storage.connection() as con:
                stored_now = get_db_rate(con, today, source, target) is not None

            if not stored_now:
                # get_rate returned a stale fallback without writing today's rate.
                # Retry later so the daily write still happens once upstream recovers.
                logger.warning(
                    "rate for %s/%s on %s not written to DB"
                    " (stale fallback %s), retrying in %d min",
                    source,
                    target,
                    today,
                    rate,
                    _RETRY_INTERVAL_SEC // 60,
                )
            else:
                logger.info(
                    "prefetched %s/%s rate for %s: %s",
                    source,
                    target,
                    today,
                    rate,
                )
                await asyncio.sleep(_seconds_until_prefetch_hour())
                continue
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("rate prefetch failed")

        await asyncio.sleep(_RETRY_INTERVAL_SEC)

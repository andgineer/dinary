"""Background task that prefetches today's exchange rate.

Waits until 08:00 Belgrade time (when kurs.resenje.org publishes
NBS rates), then calls ``get_rate`` for today's date.  On working
days this fetches a fresh rate from the API; on weekends and
holidays ``_resolve_from_nbs`` walks back and stores the previous
working day's rate under today's date.

Once the rate for today is in the DB the task sleeps until
tomorrow 08:00 — no further work needed.  If the fetch fails the
task retries every 30 minutes.

This guarantees at least one write to the ``exchange_rates`` table
every calendar day, which produces a Litestream LTX segment that
the off-site backup script on VM2 can use as a replication health
indicator.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from dinary.config import settings
from dinary.services import ledger_repo
from dinary.services.exchange_rates import _get_db_rate, get_rate

logger = logging.getLogger(__name__)

_BELGRADE = ZoneInfo("Europe/Belgrade")
_PREFETCH_HOUR = 8
_RETRY_INTERVAL_SEC = 1800  # 30 minutes


def _seconds_until_prefetch_hour() -> float:
    """Seconds until the next occurrence of _PREFETCH_HOUR Belgrade time.

    If the hour has already passed today, returns seconds until
    tomorrow.  DST transitions are handled correctly by ZoneInfo
    arithmetic (spring-forward = 23h day, fall-back = 25h day).
    """
    now = datetime.now(tz=_BELGRADE)
    target = now.replace(hour=_PREFETCH_HOUR, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max((target - now).total_seconds(), _RETRY_INTERVAL_SEC)


def _get_rate_blocking(rate_date: date, source: str, target: str) -> Decimal:
    """Fetch and persist a rate; runs in a ThreadPoolExecutor worker thread.

    Opens its own connection so the SQLite handle is never shared
    across thread boundaries (avoids CPython 3.14 access violations
    caused by passing a connection object between threads).
    """
    con = ledger_repo.get_connection()
    try:
        return get_rate(con, rate_date, source, target)
    finally:
        con.close()


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

            con = ledger_repo.get_connection()
            try:
                already_stored = _get_db_rate(con, today, source, target) is not None
            finally:
                con.close()

            if already_stored:
                logger.debug("rate for %s already stored", today)
                await asyncio.sleep(_seconds_until_prefetch_hour())
                continue

            rate = await asyncio.to_thread(_get_rate_blocking, today, source, target)

            con = ledger_repo.get_connection()
            try:
                stored_now = _get_db_rate(con, today, source, target) is not None
            finally:
                con.close()

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

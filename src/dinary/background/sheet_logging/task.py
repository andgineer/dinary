"""Background task that drains the sheet-logging queue to Google Sheets."""

import asyncio
import contextlib
import logging

from dinary.background.sheet_logging import sheet_logging
from dinary.background.sheet_logging.income_sheet_logging import drain_income_pending
from dinary.config import settings
from dinary.sheets import sheet_mapping

logger = logging.getLogger(__name__)


async def warm_sheet_mapping() -> None:
    """Moves the ~1s Drive+Sheets round-trip off the first-expense hot path. Bounded
    by ``warm_sheet_mapping_timeout_sec`` so a slow Google backend can't delay
    ``/api/health`` and starve the platform's health probe; on timeout/failure it
    logs and continues, since the drain loop retries on its own schedule.
    ``asyncio.wait_for`` cancels the awaitable but not the underlying ``to_thread``
    worker, which keeps running until its socket timeout fires."""
    if settings.sheet_logging_drain_interval_sec <= 0:
        return
    if not settings.sheet_logging_enabled:
        return
    timeout = settings.warm_sheet_mapping_timeout_sec
    if timeout <= 0:
        return
    try:
        summary = await asyncio.wait_for(
            asyncio.to_thread(sheet_mapping.reload_now),
            timeout=timeout,
        )
        logger.info("sheet_mapping preloaded at startup: %s", summary)
    except TimeoutError:
        logger.warning(
            "sheet_mapping preload timed out after %.1fs; "
            "drain loop will retry on its own schedule",
            timeout,
        )
    except Exception:
        logger.exception("sheet_mapping preload failed; drain loop will retry")


async def sheet_logging_task() -> None:
    interval = settings.sheet_logging_drain_interval_sec
    enabled = settings.sheet_logging_enabled
    if not enabled or interval <= 0:
        logger.info(
            "sheet-logging task disabled (interval<=0 or DINARY_SHEET_LOGGING_SPREADSHEET unset)",
        )
        return
    # Lets producers (e.g. POST /api/expenses) kick a sweep immediately; the
    # timer remains the fallback for crash-recovery sweeps.
    wake = asyncio.Event()
    loop = asyncio.get_running_loop()
    sheet_logging.register_wake_channel(wake, loop)
    logger.info("sheet-logging task started: interval=%gs", interval)
    try:
        first = True
        while True:
            if not first:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), timeout=interval)
                wake.clear()
            first = False
            try:
                summary = await asyncio.to_thread(sheet_logging.drain_pending)
                income_summary = await asyncio.to_thread(drain_income_pending)
                attempted = summary.get("attempted", 0)
                cap_reached = summary.get("cap_reached", False)
                poisoned = summary.get("poisoned", 0)
                failed = summary.get("failed", 0)
                if attempted > 0 or cap_reached or poisoned > 0 or failed > 0:
                    logger.info("drain sweep: %s", summary)
                else:
                    logger.debug("drain sweep: %s", summary)
                if income_summary.get("attempted", 0) > 0 or income_summary.get("poisoned", 0) > 0:
                    logger.info("income drain sweep: %s", income_summary)
                else:
                    logger.debug("income drain sweep: %s", income_summary)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("drain sweep failed")
    finally:
        sheet_logging.clear_wake_channel()

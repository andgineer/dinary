"""Background task that drains the sheet-logging queue to Google Sheets."""

import asyncio
import contextlib
import logging

from dinary.config import settings
from dinary.services import sheet_logging, sheet_mapping

logger = logging.getLogger(__name__)


async def warm_sheet_mapping() -> None:
    """Preload ``sheet_mapping`` from the ``map`` tab at startup.

    Moves the ~1s Drive+Sheets round-trip off the first-expense hot
    path. Skipped when the drain loop itself is disabled (interval
    <= 0 — the test fixture uses this to avoid network I/O in
    lifespan) or when sheet-logging is unconfigured, or when the
    operator explicitly disabled the warm-up via
    ``warm_sheet_mapping_timeout_sec <= 0``.

    Bounded by ``settings.warm_sheet_mapping_timeout_sec`` so a slow
    or unreachable Google backend cannot wedge the lifespan startup
    and delay ``/api/health`` from answering (a long startup starves
    Railway's health probe). On timeout or any other failure we log
    and continue — the drain loop's ``ensure_fresh`` will retry on
    its own schedule, and the cached mapping (from the last
    successful reload before the restart) keeps the runtime
    functional in the meantime.

    Note on cancellation: ``asyncio.wait_for`` cancels the awaitable
    but cannot cancel the underlying ``to_thread`` worker (sync
    Google I/O). The worker continues running until its socket
    timeout fires; the event loop is free to move on immediately.
    """
    if settings.sheet_logging_drain_interval_sec <= 0:
        return
    if not sheet_logging.is_sheet_logging_enabled():
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
    enabled = sheet_logging.is_sheet_logging_enabled()
    if not enabled or interval <= 0:
        logger.info(
            "sheet-logging task disabled (interval<=0 or DINARY_SHEET_LOGGING_SPREADSHEET unset)",
        )
        return
    # Register a wake-up event so producers (e.g. POST /api/expenses)
    # can kick a sweep immediately instead of waiting for the next
    # periodic tick. The timer is still the fallback for crash-recovery
    # sweeps over jobs left behind by a previous worker.
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
                attempted = summary.get("attempted", 0)
                cap_reached = summary.get("cap_reached", False)
                poisoned = summary.get("poisoned", 0)
                failed = summary.get("failed", 0)
                if attempted > 0 or cap_reached or poisoned > 0 or failed > 0:
                    logger.info("drain sweep: %s", summary)
                else:
                    logger.debug("drain sweep: %s", summary)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("drain sweep failed")
    finally:
        sheet_logging.clear_wake_channel()

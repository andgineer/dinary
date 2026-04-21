"""FastAPI application for dinary-server."""

import asyncio
import contextlib
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from dinary import __version__
from dinary.api import admin_catalog, catalog, expenses, qr
from dinary.config import settings
from dinary.services import duckdb_repo, sheet_logging, sheet_mapping

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_BUILT_STATIC = _PROJECT_ROOT / "_static"
STATIC_DIR = _BUILT_STATIC if _BUILT_STATIC.is_dir() else _PROJECT_ROOT / "static"


def _read_deployed_version() -> str:
    """Return the deployed git hash if available, else the package version."""
    ver_file = _PROJECT_ROOT / "data" / ".deployed_version"
    if ver_file.exists():
        v = ver_file.read_text().strip()
        if v:
            return v
    return __version__


_DEPLOYED_VERSION: str = _read_deployed_version()


def _setup_logging() -> None:
    fmt = (
        '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'
        if settings.log_json
        else "%(asctime)s %(levelname)-8s %(name)s — %(message)s"
    )
    logging.basicConfig(
        level=settings.log_level.upper(),
        format=fmt,
        stream=sys.stdout,
    )


_drain_logger = logging.getLogger("dinary.sheet_logging.drain_loop")


async def _drain_loop() -> None:
    interval = settings.sheet_logging_drain_interval_sec
    enabled = sheet_logging.is_sheet_logging_enabled()
    if not enabled or interval <= 0:
        _drain_logger.info(
            "sheet-logging drain loop disabled"
            " (interval<=0 or DINARY_SHEET_LOGGING_SPREADSHEET unset)",
        )
        return
    # Register a wake-up event so producers (e.g. POST /api/expenses)
    # can kick a sweep immediately instead of waiting for the next
    # periodic tick. The timer is still the fallback for crash-recovery
    # sweeps over jobs left behind by a previous worker.
    wake = asyncio.Event()
    loop = asyncio.get_running_loop()
    sheet_logging.register_wake_channel(wake, loop)
    _drain_logger.info("sheet-logging drain loop started: interval=%gs", interval)
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
                    _drain_logger.info("drain sweep: %s", summary)
                else:
                    _drain_logger.debug("drain sweep: %s", summary)
            except asyncio.CancelledError:
                raise
            except Exception:
                _drain_logger.exception("drain sweep failed")
    finally:
        sheet_logging.clear_wake_channel()


async def _warm_sheet_mapping() -> None:
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
    log = logging.getLogger(__name__)
    try:
        summary = await asyncio.wait_for(
            asyncio.to_thread(sheet_mapping.reload_now),
            timeout=timeout,
        )
        log.info("sheet_mapping preloaded at startup: %s", summary)
    except TimeoutError:
        log.warning(
            "sheet_mapping preload timed out after %.1fs; "
            "drain loop will retry on its own schedule",
            timeout,
        )
    except Exception:
        log.exception("sheet_mapping preload failed; drain loop will retry")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    duckdb_repo.init_db()
    await _warm_sheet_mapping()
    drain_task = asyncio.create_task(_drain_loop(), name="sheet-logging-drain")
    try:
        yield
    finally:
        drain_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await drain_task


def create_app() -> FastAPI:
    _setup_logging()

    app = FastAPI(
        title="dinary-server",
        version=__version__,
        lifespan=_lifespan,
    )

    app.include_router(expenses.router)
    app.include_router(qr.router)
    app.include_router(catalog.router)
    app.include_router(admin_catalog.router)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": _DEPLOYED_VERSION}

    @app.get("/api/version")
    def api_version() -> dict:
        return {"version": _DEPLOYED_VERSION}

    class NoCacheSW(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):  # noqa: ANN001
            response: Response = await call_next(request)
            if request.url.path == "/sw.js":
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            return response

    app.add_middleware(NoCacheSW)

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app


app = create_app()


def main() -> None:
    """Entry point for ``dinary`` CLI command — runs uvicorn with auto-reload for local dev."""
    uvicorn.run(
        "dinary.main:app",
        host="127.0.0.1",
        port=settings.port,
        log_level=settings.log_level,
        reload=True,
    )


if __name__ == "__main__":
    main()

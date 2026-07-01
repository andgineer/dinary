"""FastAPI application for dinary."""

import asyncio
import contextlib
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import llmbroker
import llmbroker.sqlite
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from dinary import __version__
from dinary.api import (
    analytics,
    catalog,
    category_templates,
    currencies,
    expense_corrections,
    expenses,
    income,
    llm,
    receipts,
    rules,
)
from dinary.background.classification.task import receipt_classification_task
from dinary.background.rate_prefetch.task import rate_prefetch_task
from dinary.background.sheet_logging.task import sheet_logging_task, warm_sheet_mapping
from dinary.config import settings
from dinary.db import category_seed, storage

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_STATIC_DIR = _PROJECT_ROOT / "_static"


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
    logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    storage.init_db()
    with storage.connection() as con:
        category_seed.bootstrap_categories(con)
    load_dotenv(_PROJECT_ROOT / ".deploy" / ".env", override=False)
    llms = llmbroker.AsyncBroker(
        registry=llmbroker.sqlite.Registry(storage.DB_PATH),
        telemetry=llmbroker.sqlite.Telemetry(storage.DB_PATH),
        secrets=llmbroker.sqlite.Secrets(storage.DB_PATH),
        state_store=llmbroker.sqlite.StateStore(storage.DB_PATH),
        seed=llmbroker.Registry(_PROJECT_ROOT / ".deploy" / "llms.toml"),
        seed_policy=llmbroker.SeedPolicy.ADD,
    )
    _app.state.llms = llms
    await llms.ensure_pool()
    await warm_sheet_mapping()
    sheet_logging_bg = asyncio.create_task(sheet_logging_task(), name="sheet-logging-task")
    rate_prefetch_bg = asyncio.create_task(rate_prefetch_task(), name="rate-prefetch-task")
    receipt_classification_bg = asyncio.create_task(
        receipt_classification_task(llms),
        name="receipt-classification-task",
    )
    try:
        yield
    finally:
        sheet_logging_bg.cancel()
        rate_prefetch_bg.cancel()
        receipt_classification_bg.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sheet_logging_bg
        with contextlib.suppress(asyncio.CancelledError):
            await rate_prefetch_bg
        with contextlib.suppress(asyncio.CancelledError):
            await receipt_classification_bg
        await llms.aclose()
        _app.state.llms = None


def create_app() -> FastAPI:
    _setup_logging()

    app = FastAPI(
        title="dinary",
        version=__version__,
        lifespan=_lifespan,
    )
    app.state.llms = None

    app.include_router(analytics.router)
    app.include_router(expenses.router)
    app.include_router(expense_corrections.router)
    app.include_router(income.router)
    app.include_router(catalog.router)
    app.include_router(category_templates.router)
    app.include_router(currencies.router)
    app.include_router(receipts.router)
    app.include_router(rules.router)
    app.include_router(llm.router)

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

    if _STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")

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

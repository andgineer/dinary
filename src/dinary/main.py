"""FastAPI application for dinary-server."""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from dinary import __version__
from dinary.api import categories, expenses, qr
from dinary.config import settings
from dinary.services import duckdb_repo

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


@asynccontextmanager
async def _lifespan(app: FastAPI):
    duckdb_repo.init_config_db()
    yield


def create_app() -> FastAPI:
    _setup_logging()

    app = FastAPI(
        title="dinary-server",
        version=__version__,
        lifespan=_lifespan,
    )

    app.include_router(expenses.router)
    app.include_router(qr.router)
    app.include_router(categories.router)

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

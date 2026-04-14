"""FastAPI application for dinary-server."""

import logging
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from dinary import __version__
from dinary.api import categories, expenses, qr
from dinary.config import settings

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"


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


def create_app() -> FastAPI:
    _setup_logging()

    app = FastAPI(
        title="dinary-server",
        version=__version__,
    )

    app.include_router(expenses.router)
    app.include_router(qr.router)
    app.include_router(categories.router)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app


app = create_app()


def main() -> None:
    """Entry point for ``dinary`` CLI command — runs uvicorn."""
    uvicorn.run(
        "dinary.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        reload=False,
    )


if __name__ == "__main__":
    main()

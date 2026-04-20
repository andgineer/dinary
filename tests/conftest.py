import pytest
from fastapi.testclient import TestClient

from dinary.config import settings
from dinary.main import create_app
from dinary.services import duckdb_repo


def _reset_duckdb_singleton() -> None:
    """Tear down the process-wide DuckDB connection between tests.

    Lives in conftest (not in production ``duckdb_repo``) because
    nothing in production should ever close the singleton out from
    under live callers; only the test harness needs this hook. The
    production repo keeps a single connection to ``data/dinary.duckdb``
    for the whole process; most tests monkeypatch ``DB_PATH`` (and
    ``DATA_DIR``) to a per-test ``tmp_path``, so the singleton from
    the previous test must be closed or it would still point at the
    previous test's tmp file.
    """
    duckdb_repo.close_connection()


@pytest.fixture(autouse=True)
def _reset_duckdb_connection():
    """Reset the singleton connection before AND after each test."""
    _reset_duckdb_singleton()
    yield
    _reset_duckdb_singleton()


@pytest.fixture(autouse=True)
def _disable_drain_loop(monkeypatch):
    """Silence the lifespan periodic drain for every test by default.

    Dedicated lifespan tests in tests/test_main.py opt back in by
    overriding this setting inside their own body.
    """
    monkeypatch.setattr(settings, "sheet_logging_drain_interval_sec", 0)


@pytest.fixture
def client():
    """FastAPI TestClient that surfaces server-side exceptions as HTTP 500.

    The default ``TestClient`` re-raises unhandled exceptions straight into
    the test body, which prevents assertions on the HTTP response Starlette
    would actually serve to a real client. ``raise_server_exceptions=False``
    flips the client to production-like behavior: unhandled exceptions go
    through Starlette's error-handling middleware and come back as 500 with
    an empty body, just like a deployed instance would see.
    """
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

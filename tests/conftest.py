import pytest
from fastapi.testclient import TestClient

from dinary.config import settings
from dinary.main import create_app
from dinary.services import ledger_repo


def _reset_db_singleton() -> None:
    """Reset repo-level DB state between tests.

    ``ledger_repo.get_connection`` returns a fresh ``sqlite3``
    connection per call, so there is no singleton to close today —
    ``close_connection`` is a no-op. The fixture hook is retained as
    a known tear-down point if any future connection-pool-style
    caching is added.
    """
    ledger_repo.close_connection()


@pytest.fixture(autouse=True)
def _reset_db_connection():
    """Reset repo-level DB state before AND after each test."""
    _reset_db_singleton()
    yield
    _reset_db_singleton()


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

import shutil
import sqlite3
import unittest.mock

import pytest
from fastapi.testclient import TestClient

from dinary.config import settings
from dinary.main import create_app
from dinary.services import db_migrations, ledger_repo, sheet_mapping

_REAL_ENSURE_FRESH = sheet_mapping.ensure_fresh


def _migration_connect(self, dburi):
    con = sqlite3.connect(
        str(self.uri.database), detect_types=sqlite3.PARSE_DECLTYPES, isolation_level=None
    )
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("PRAGMA busy_timeout=5000")
    return con


@pytest.fixture(scope="session")
def blank_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("db_template") / "dinary.db"
    with unittest.mock.patch.object(db_migrations.SQLiteBackend, "connect", _migration_connect):
        db_migrations.migrate_db(path)
    return path


@pytest.fixture
def db(tmp_path, monkeypatch, blank_db):
    dst = tmp_path / "dinary.db"
    shutil.copy(blank_db, dst)
    monkeypatch.setattr(ledger_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ledger_repo, "DB_PATH", dst)


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


@pytest.fixture(autouse=True)
def _disable_sheet_mapping_preload(monkeypatch):
    """Skip the lifespan sheet_mapping warm-up for every test by default.

    ``dinary.main._lifespan`` calls ``_warm_sheet_mapping`` on entry,
    which reaches out to Drive + Sheets via ``sheet_mapping.reload_now``.
    Any test that enters the lifespan (``test_main.py``, anything using
    the ``client`` TestClient fixture) would pay ~1-2s of real network
    per test on a good connection, and up to 10s on a bad one — enough
    to mask the actual slowness the test is trying to observe and to
    make CI flaky when Google is rate-limiting.

    Setting ``warm_sheet_mapping_timeout_sec=0`` is the documented
    escape hatch (see ``settings.warm_sheet_mapping_timeout_sec``): the
    warm-up short-circuits immediately before any client is constructed.
    Tests that specifically want to exercise ``reload_now`` (e.g.
    ``tests/test_sheet_mapping.py``) call it directly with ``get_sheet``
    and ``drive_get_modified_time`` patched, so this default does not
    weaken their coverage.
    """
    monkeypatch.setattr(settings, "warm_sheet_mapping_timeout_sec", 0)


@pytest.fixture(autouse=True)
def _stub_sheet_mapping_ensure_fresh(monkeypatch):
    """Neutralise ``sheet_mapping.ensure_fresh`` for every test by default.

    ``sheet_logging.drain_pending`` calls ``sheet_mapping.ensure_fresh``
    on every sweep, which issues a real Drive ``modifiedTime`` GET
    against ``settings.sheet_logging_spreadsheet``. The drain-focused
    suites patch ``get_sheet`` / ``append_expense_atomic`` but not this
    freshness probe, so each test paid a ~0.3-0.7s Drive 404 round-trip
    (swallowed by the broad ``except Exception`` in ``ensure_fresh``,
    hence invisible until you look at ``--durations``).

    A module-level ``lambda: None`` keeps the call signature honest
    and the drain body unchanged. Tests that specifically exercise
    ``ensure_fresh`` (see ``tests/test_sheet_mapping.py::TestEnsureFresh``)
    patch ``drive_get_modified_time`` inside their own body, so this
    default does not weaken their coverage.
    """
    monkeypatch.setattr(sheet_mapping, "ensure_fresh", lambda: None)


@pytest.fixture
def real_ensure_fresh(monkeypatch):
    """Opt out of ``_stub_sheet_mapping_ensure_fresh`` for the current test.

    Tests that actually drive ``sheet_mapping.ensure_fresh`` through
    its real branches (see ``tests/test_sheet_mapping.py::TestEnsureFresh``)
    depend on the unpatched function; declare this fixture in their
    signature and the stubbed default is replaced with the real
    callable for the duration of the test.
    """
    monkeypatch.setattr(sheet_mapping, "ensure_fresh", _REAL_ENSURE_FRESH)


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

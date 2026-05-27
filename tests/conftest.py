import shutil
import socket
import sqlite3
import unittest.mock
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dinary.adapters import rate_helpers
from dinary.adapters.llm_storage import SqliteLLMBrokerStorage
from dinary.adapters.llmbroker import CallEvent, ProviderConfig
from dinary.config import settings
from dinary.db import db_migrations, storage
from dinary.main import create_app
from dinary.sheets import sheet_mapping

_REAL_LLM_SEED = SqliteLLMBrokerStorage._seed


class NullStorage:
    """No-op BrokerStorage for tests — no providers, no persistence."""

    async def load_providers(self) -> list[ProviderConfig]:
        return []

    async def on_call_logged(self, event: CallEvent) -> None:
        pass

    async def on_rate_limited(self, provider_id: object, until: datetime) -> None:
        pass


# Tests that need the built Vue PWA must depend on ``built_static_dir``;
# the fixture FAILS LOUDLY when ``_static/`` is absent (instead of
# silently skipping) so a missing build never hides a regression.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BUILT_STATIC = _PROJECT_ROOT / "_static"

_REAL_ENSURE_FRESH = sheet_mapping.ensure_fresh


def _migration_connect(self, dburi):
    con = sqlite3.connect(
        str(self.uri.database), detect_types=sqlite3.PARSE_DECLTYPES, isolation_level=None
    )
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("PRAGMA busy_timeout=5000")
    return con


@pytest.fixture(scope="session", autouse=True)
def _stub_socket_getfqdn():
    """Prevent yoyo's migration logger from hanging on macOS CI reverse-DNS."""
    with unittest.mock.patch.object(socket, "getfqdn", return_value="localhost"):
        yield


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
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_PATH", dst)


@pytest.fixture(autouse=True)
def _reset_db_connection():
    """Reset repo-level DB state before AND after each test (currently a no-op placeholder)."""
    yield


@pytest.fixture(autouse=True)
def _disable_llm_seed(monkeypatch):
    """Prevent SqliteLLMBrokerStorage from auto-seeding providers from .deploy/llm_providers.toml.

    Tests that specifically assert on llmbroker_providers state start from an empty
    table; the operator's real credentials or local TOML must not interfere.
    """

    async def _no_op(self, db):  # noqa: ARG001
        pass

    monkeypatch.setattr(SqliteLLMBrokerStorage, "_seed", _no_op)


@pytest.fixture
def real_llm_seed(monkeypatch):
    """Opt out of _disable_llm_seed for tests that specifically verify provider seeding."""
    monkeypatch.setattr(SqliteLLMBrokerStorage, "_seed", _REAL_LLM_SEED)


@pytest.fixture(autouse=True)
def _disable_drain_loop(monkeypatch):
    """Silence the lifespan periodic drain for every test by default.

    Dedicated lifespan tests in tests/test_main.py opt back in by
    overriding this setting inside their own body.
    """
    monkeypatch.setattr(settings, "sheet_logging_drain_interval_sec", 0)
    monkeypatch.setattr(settings, "receipt_classification_enabled", False)


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


@pytest.fixture(scope="session")
def built_static_dir() -> Path:
    """Path to the built Vue PWA (``_static/``); FAIL LOUDLY if absent.

    Opt-in fixture. Tests that exercise the built Vue bundle (icons,
    index.html, service worker, hashed assets) declare this fixture.
    Building the PWA is a prerequisite for the test suite — silently
    skipping these tests would mask real regressions in the bundle,
    so we fail with an actionable instruction instead.
    """
    if not _BUILT_STATIC.is_dir():
        pytest.fail(
            "_static/ not built — run `uv run inv build-static` "
            "(or `npm --prefix webapp run build`) before the tests.",
        )
    return _BUILT_STATIC


@pytest.fixture
def client():
    """FastAPI TestClient that surfaces server-side exceptions as HTTP 500.

    The default ``TestClient`` re-raises unhandled exceptions straight into
    the test body, which prevents assertions on the HTTP response Starlette
    would actually serve to a real client. ``raise_server_exceptions=False``
    flips the client to production-like behavior: unhandled exceptions go
    through Starlette's error-handling middleware and come back as 500 with
    an empty body, just like a deployed instance would see.

    ``_get_json_or_none`` is stubbed so the ``rate_prefetch_task`` background
    task started by the lifespan never reaches kurs.resenje.org.

    ``migrate_db`` is stubbed to a no-op so the lifespan's ``init_db()``
    call never attempts DDL against the current developer DB — tests that
    depend on a specific DB state use the ``db`` / ``_api_helpers.db``
    fixtures which always point at a freshly-migrated temp path.
    """
    with (
        unittest.mock.patch.object(rate_helpers, "_get_json_or_none", return_value=None),
        unittest.mock.patch.object(db_migrations, "migrate_db"),
    ):
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

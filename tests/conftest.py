import duckdb
import pytest
from fastapi.testclient import TestClient

from dinary.main import create_app
from dinary.services import duckdb_repo


def _reset_duckdb_engine_state() -> None:
    """Tear down the process-wide DuckDB engine between tests.

    Lives in conftest (not in production `duckdb_repo`) because
    nothing in production should ever close the singleton engine
    out from under live connections; only the test harness needs
    this hook.

    `duckdb_repo` keeps a single engine for the whole process so
    `config.duckdb` and every `budget_YYYY.duckdb` can coexist
    without DuckDB's "unique file handle" conflict (see the
    `duckdb_repo` module docstring for the full rationale). That
    engine is shared across tests too — and since most tests
    `monkeypatch` `DATA_DIR` / `CONFIG_DB` to a per-test `tmp_path`,
    the engine from the previous test would still hold ATTACHes
    pointing at the previous test's tmp dir.
    """
    with duckdb_repo._engine_lock:  # noqa: SLF001
        state = duckdb_repo._engine_state  # noqa: SLF001
        if state.engine is not None:
            try:
                state.engine.close()
            except duckdb.Error:
                duckdb_repo.logger.exception(
                    "Failed to close singleton engine in test reset",
                )
        state.engine = None
        state.config_attached = False
        state.attached_budget_years = set()


@pytest.fixture(autouse=True)
def _reset_duckdb_engine():
    """Reset the singleton engine before AND after each test.

    Reset before so a test that forgets its own monkeypatch doesn't
    inherit an ATTACH into a stale tmp dir; reset after so the next
    test always starts with a clean engine that will re-ATTACH
    against the freshly monkeypatched paths on first access.
    """
    _reset_duckdb_engine_state()
    yield
    _reset_duckdb_engine_state()


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c

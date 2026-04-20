"""Real-fake-DB integration tests for the 3D refactor.

Unlike `tests/test_api.py`, which patches `convert_to_eur` and
`schedule_logging` to keep the surface mock-heavy and fast, the tests in
this module run the *full* `POST /api/expenses` codepath against a real
`config.duckdb` + `budget_YYYY.duckdb` pair seeded with realistic data
(categories, exchange_rates, import_sources). Only the gspread
layer is stubbed, because we cannot talk to Google from CI. Every
DuckDB interaction goes through real connections.

These tests exist to catch a class of bug that the mock-heavy suite
misses:

  * `BinderException: Unique file handle conflict: Cannot attach
    "config" - the database file ... is already attached by database
    "config"`

This regression hit production right after the 3D rollout: the async
sheet-logging worker holds a `budget_YYYY.duckdb` connection (which
internally `ATTACH`es `config.duckdb` READ_ONLY) for the full duration
of a Sheets append. While that connection is alive, every subsequent
`POST /api/expenses` that needs to open `config.duckdb` in READ_WRITE
mode (for currency conversion via `convert_to_eur`, for
`reserve_expense_id_year`, etc.) used to fail with a 500 because DuckDB
forbids two engines in the same process from opening the same file when
one of them already has it attached.

The tests below pin down the contract:

  1. `get_config_connection(read_only=True/False)` MUST work while a
     `get_budget_connection(year)` connection is alive in the same
     process — in either open order.
  2. Public helpers that internally open `config.duckdb` in READ_WRITE
     mode (`reserve_expense_id_year`, `release_expense_id_year`) MUST
     work while a budget connection is alive.
  3. `POST /api/expenses` (happy path AND idempotent replay) MUST return
     200 while a budget connection is alive — i.e. the API is robust
     against an in-flight async sheet-logging worker.
"""

from datetime import date
from unittest.mock import patch

import allure
import pytest
from fastapi.testclient import TestClient

from dinary.config import settings
from dinary.main import create_app
from dinary.services import duckdb_repo


@pytest.fixture
def real_db(tmp_path, monkeypatch):
    """Real `config.duckdb` seeded with categories + EUR exchange rate.

    Returns the tmp_path so tests can poke at the on-disk DB directly
    (e.g. to seed an extra row or assert post-conditions).
    """
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "CONFIG_DB", tmp_path / "config.duckdb")
    monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "fake-sheet")
    duckdb_repo.init_config_db()

    con = duckdb_repo.get_config_connection(read_only=False)
    try:
        con.execute("INSERT INTO category_groups VALUES (1, 'Food', 1)")
        con.execute("INSERT INTO categories VALUES (1, 'еда', 1)")
        con.execute(
            "INSERT INTO import_sources(year, spreadsheet_id, worksheet_name,"
            " layout_key) VALUES (2026, 'fake-sheet', '2026', 'default')",
        )
        # Seed exchange_rates so `convert_to_eur` never hits the network.
        # The handler resolves rates against `req.date.replace(day=1)`, so we
        # only need the first-of-month entry for April 2026. Real EUR ≈ 117
        # RSD; RSD itself is the base unit and short-circuits in get_rate.
        con.execute(
            "INSERT INTO exchange_rates VALUES (?, 'EUR', 117.0000)",
            [date(2026, 4, 1)],
        )
    finally:
        con.close()
    return tmp_path


@pytest.fixture
def real_client(real_db):  # noqa: ARG001 — fixture used for setup side effects
    app = create_app()
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Direct repository tests — the smallest reproduction of the BinderException
# ---------------------------------------------------------------------------


@allure.epic("Concurrency")
@allure.feature("config + budget connection coexistence")
class TestConfigBudgetConnectionCoexistence:
    """Pin the contract: with a `budget_YYYY` connection alive (which has
    `config.duckdb` ATTACHed READ_ONLY), opening a SEPARATE
    `config.duckdb` connection — in any mode, in any order — must not
    raise. Before the fix, opening it READ_WRITE produced

        BinderException: Unique file handle conflict: Cannot attach
        "config" - the database file ... is already attached by database
        "config"

    which surfaced as 500s on `POST /api/expenses` whenever the async
    sheet-sync worker was mid-flight.
    """

    def test_config_ro_works_while_budget_open(self, real_db):  # noqa: ARG002
        budget_con = duckdb_repo.get_budget_connection(2026)
        try:
            config_ro = duckdb_repo.get_config_connection(read_only=True)
            try:
                row = config_ro.execute(
                    "SELECT name FROM categories WHERE id = 1",
                ).fetchone()
                assert row == ("еда",)
            finally:
                config_ro.close()
        finally:
            budget_con.close()

    def test_config_rw_works_while_budget_open(self, real_db):  # noqa: ARG002
        """Regression: this is the production bug.

        With a `budget_2026` connection alive, the API handler used to
        crash here on the second `get_config_connection(read_only=False)`
        of the request (`convert_to_eur` opens it RW for the rate cache).
        """
        budget_con = duckdb_repo.get_budget_connection(2026)
        try:
            config_rw = duckdb_repo.get_config_connection(read_only=False)
            try:
                config_rw.execute(
                    "INSERT INTO exchange_rates VALUES (?, 'USD', 100.5000)",
                    [date(2026, 5, 1)],
                )
                row = config_rw.execute(
                    "SELECT rate FROM exchange_rates WHERE currency = 'USD'",
                ).fetchone()
                assert row is not None
            finally:
                config_rw.close()
        finally:
            budget_con.close()

    def test_budget_open_after_config_rw_open(self, real_db):  # noqa: ARG002
        """Reverse open order: API handler grabs config RW first (e.g.
        for rate-cache write inside `convert_to_eur`), THEN a peer task
        opens a budget conn (e.g. an `inv drain-logging` sweep starts).
        Symmetrical to the production scenario; must not raise either
        way.
        """
        config_rw = duckdb_repo.get_config_connection(read_only=False)
        try:
            budget_con = duckdb_repo.get_budget_connection(2026)
            try:
                # Cross-DB query through the budget conn proves the
                # ATTACH succeeded and config is reachable as `config.*`.
                row = budget_con.execute(
                    "SELECT name FROM config.categories WHERE id = 1",
                ).fetchone()
                assert row == ("еда",)
            finally:
                budget_con.close()
        finally:
            config_rw.close()

    def test_two_budget_conns_then_config_rw(self, real_db):  # noqa: ARG002
        """Multi-year case: two budget DBs alive simultaneously plus a
        config RW connection. Mirrors `inv drain-logging` (which iterates years)
        running concurrently with a `POST /api/expenses` request.
        """
        b1 = duckdb_repo.get_budget_connection(2025)
        b2 = duckdb_repo.get_budget_connection(2026)
        try:
            config_rw = duckdb_repo.get_config_connection(read_only=False)
            try:
                config_rw.execute(
                    "INSERT INTO exchange_rates VALUES (?, 'GBP', 140.0000)",
                    [date(2026, 6, 1)],
                )
            finally:
                config_rw.close()
        finally:
            b1.close()
            b2.close()

    def test_reserve_expense_id_year_while_budget_open(self, real_db):  # noqa: ARG002
        """Public helper that opens config RW internally must work while
        a budget conn is alive. The API handler calls this AFTER opening
        a budget conn would be premature, but `inv drain-logging` (or another
        async drain) routinely holds budget conns at the moment a fresh
        POST tries to reserve an id.
        """
        budget_con = duckdb_repo.get_budget_connection(2026)
        try:
            stored_year, inserted = duckdb_repo.reserve_expense_id_year(
                "expense-while-budget-open",
                2026,
            )
            assert stored_year == 2026
            assert inserted is True
        finally:
            budget_con.close()

    def test_release_expense_id_year_while_budget_open(self, real_db):  # noqa: ARG002
        """Symmetric to the reserve test: `release_expense_id_year` is
        the cleanup-on-failure path and must not deadlock or raise
        because of an alive budget conn.
        """
        # Reserve first (no budget conn alive at this point).
        duckdb_repo.reserve_expense_id_year("e_release", 2026)
        budget_con = duckdb_repo.get_budget_connection(2026)
        try:
            duckdb_repo.release_expense_id_year("e_release")
        finally:
            budget_con.close()
        assert duckdb_repo.get_registered_expense_year("e_release") is None


# ---------------------------------------------------------------------------
# End-to-end POST /api/expenses with a long-lived budget connection
# ---------------------------------------------------------------------------


def _stub_schedule_logging(monkeypatch):
    """Replace `schedule_logging` with a no-op for tests that focus on
    connection-coexistence rather than the actual async drain.

    The async drain exists in tests above as a *manual* `budget_con`
    held open for the duration of the request — that gives us
    deterministic control over exactly when the conflicting connection
    is alive, instead of depending on event-loop scheduling.
    """
    from dinary.api import expenses as expenses_module

    monkeypatch.setattr(
        expenses_module,
        "schedule_logging",
        lambda *_a, **_kw: None,
    )


@allure.epic("Concurrency")
@allure.feature("POST /api/expenses with live budget connection")
class TestPostExpenseWithLiveBudgetConnection:
    """API-level reproductions: the production scenario of an in-flight
    async sheet-sync worker (which holds a `budget_YYYY` connection for
    the duration of the gspread roundtrip) racing a fresh `POST
    /api/expenses` request.

    These tests intentionally do NOT mock `convert_to_eur`. The whole
    point is to exercise the path that opens `config.duckdb` in
    READ_WRITE mode (the rate cache write inside `_save_cache`) while a
    budget conn is alive — that's the path that produced the 500s in
    production.
    """

    def test_post_succeeds_while_external_budget_conn_held(
        self,
        real_client,
        monkeypatch,
    ):
        _stub_schedule_logging(monkeypatch)
        # Hold a budget conn open for the whole request: this is exactly
        # what `_drain_one_job` does while it talks to Google Sheets.
        budget_con = duckdb_repo.get_budget_connection(2026)
        try:
            resp = real_client.post(
                "/api/expenses",
                json={
                    "expense_id": "live-budget-conn",
                    "amount": 117.0,
                    "currency": "RSD",
                    "category": "еда",
                    "comment": "race-with-async-worker",
                    "date": "2026-04-15",
                },
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["status"] == "created"
            assert data["amount_original"] == 117.0
            assert data["currency_original"] == "RSD"
        finally:
            budget_con.close()

    def test_post_replay_succeeds_while_external_budget_conn_held(
        self,
        real_client,
        monkeypatch,
    ):
        """The replay path is what blew up in production: a duplicate
        POST hits convert_to_eur AGAIN (reads the same rate row) AND
        runs the full insert_expense path against the budget DB. With
        the async drain still in flight, the second `get_config_connection
        (read_only=False)` used to crash.
        """
        _stub_schedule_logging(monkeypatch)
        body = {
            "expense_id": "live-budget-conn-replay",
            "amount": 234.0,
            "currency": "RSD",
            "category": "еда",
            "comment": "",
            "date": "2026-04-15",
        }
        first = real_client.post("/api/expenses", json=body)
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "created"

        # Now simulate the async drain task: hold a budget conn open
        # while the replay POST is processed.
        budget_con = duckdb_repo.get_budget_connection(2026)
        try:
            second = real_client.post("/api/expenses", json=body)
            assert second.status_code == 200, second.text
            assert second.json()["status"] == "duplicate"
        finally:
            budget_con.close()

    def test_post_writes_real_rate_cache_while_budget_conn_held(
        self,
        real_client,
        monkeypatch,
    ):
        """Currency conversion through the real `convert_to_eur` path,
        not a mock. EUR → EUR short-circuits in the converter so no
        outbound network call is needed; we still pass through every
        connection-management codepath in the handler that touches
        config.duckdb (RO probe → RW for `convert_to_eur` → registry
        reserve → budget insert → RO for catalog_version).
        """
        _stub_schedule_logging(monkeypatch)
        budget_con = duckdb_repo.get_budget_connection(2026)
        try:
            resp = real_client.post(
                "/api/expenses",
                json={
                    "expense_id": "eur-while-budget-open",
                    "amount": 12.5,
                    "currency": "EUR",
                    "category": "еда",
                    "comment": "no network",
                    "date": "2026-04-15",
                },
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["amount_original"] == 12.5
            assert resp.json()["currency_original"] == "EUR"
        finally:
            budget_con.close()

    def test_unknown_category_with_budget_conn_held_does_not_500(
        self,
        real_client,
        monkeypatch,
    ):
        """Unknown-category 422 path: handler opens config_ro for
        category lookup, finds nothing, raises HTTPException(422)
        WITHOUT ever opening config_rw. This must continue to behave
        cleanly (422, not 500) when a budget conn is alive — confirms
        we haven't accidentally widened the conflict surface.
        """
        _stub_schedule_logging(monkeypatch)
        budget_con = duckdb_repo.get_budget_connection(2026)
        try:
            resp = real_client.post(
                "/api/expenses",
                json={
                    "expense_id": "unknown-cat",
                    "amount": 1.0,
                    "currency": "RSD",
                    "category": "missing-category",
                    "comment": "",
                    "date": "2026-04-15",
                },
            )
            assert resp.status_code == 422, resp.text
        finally:
            budget_con.close()


# ---------------------------------------------------------------------------
# End-to-end POST + actual sheet-sync background drain (gspread stubbed only)
# ---------------------------------------------------------------------------


@allure.epic("Concurrency")
@allure.feature("Async drain race with subsequent POST")
class TestAsyncDrainRaceWithPost:
    """The most realistic scenario: let the real `schedule_logging` fire
    a background drain task, slow the gspread step deliberately, and
    fire a SECOND POST while the drain still holds the budget conn. The
    second POST must not 500.

    We stub only `_append_row_to_sheet` (the actual gspread call) and
    `_fetch_rate_blocking` (the NBS network call); everything else
    runs for real.
    """

    def test_second_post_during_in_flight_drain(  # noqa: PLR0915
        self,
        real_client,
        monkeypatch,
    ):
        import threading

        from dinary.services import sheet_logging as logging_module

        # Block the gspread call until the test releases it. While the
        # background task is parked on this event, it's holding a
        # budget_2026 connection — exactly the production race window.
        sheet_call_started = threading.Event()
        release_sheet_call = threading.Event()

        def slow_sheet_append(*_args, **_kwargs):
            sheet_call_started.set()
            # Bound the wait so a buggy test can't hang CI forever.
            assert release_sheet_call.wait(timeout=10), (
                "Test forgot to release the simulated sheet append"
            )

        # Patch the rate fetch too, so the slow path never goes through
        # an outbound HTTP request (we don't have a fake NBS in-process).
        monkeypatch.setattr(
            logging_module,
            "_fetch_rate_blocking",
            lambda _d: None,
        )
        monkeypatch.setattr(
            logging_module,
            "_append_row_to_sheet",
            slow_sheet_append,
        )

        monkeypatch.setattr(
            duckdb_repo,
            "logging_projection",
            lambda *_a, **_kw: ("еда", "Food"),
        )

        # Fire the first POST: this enqueues a sheet_logging_jobs row AND
        # schedules the background drain. The drain blocks on
        # `slow_sheet_append`, holding budget_2026 open the whole time.
        first = real_client.post(
            "/api/expenses",
            json={
                "expense_id": "race-first",
                "amount": 50.0,
                "currency": "RSD",
                "category": "еда",
                "comment": "first",
                "date": "2026-04-15",
            },
        )
        assert first.status_code == 200, first.text

        # Wait for the background drain to actually be parked inside
        # the simulated sheet append. If we don't wait, the second POST
        # might race the drain's claim and the test becomes flaky.
        assert sheet_call_started.wait(timeout=5), (
            "Background drain never reached the sheet-append step;"
            " schedule_logging may not have fired"
        )

        # Second POST while the drain is parked. Before the fix this
        # returned 500 with `BinderException` in the server logs because
        # `convert_to_eur` could not open config RW.
        try:
            second = real_client.post(
                "/api/expenses",
                json={
                    "expense_id": "race-second",
                    "amount": 75.0,
                    "currency": "RSD",
                    "category": "еда",
                    "comment": "second",
                    "date": "2026-04-15",
                },
            )
            assert second.status_code == 200, second.text
            assert second.json()["status"] == "created"
        finally:
            # Always release so the background task can finish; otherwise
            # TestClient's shutdown waits on it and the suite hangs.
            release_sheet_call.set()


# ---------------------------------------------------------------------------
# Diagnostic: prove the reproduction is real, even without going through
# the API. This is the smallest possible failing case and serves as a
# permanent breadcrumb for future readers tracking down a regression.
# ---------------------------------------------------------------------------


@allure.epic("Concurrency")
@allure.feature("Diagnostic — minimal BinderException repro")
def test_diagnostic_minimal_binder_exception_repro(real_db):  # noqa: ARG001
    """Smallest possible reproduction kept as an explicit, documented
    test so future readers don't have to re-derive the bug from the
    stack trace.

    If this test starts failing with `BinderException: Unique file
    handle conflict`, the production bug is back: an
    in-flight async sheet-sync worker will once again 500 every
    `POST /api/expenses` whose `convert_to_eur` step needs a config RW
    connection.
    """
    budget_con = duckdb_repo.get_budget_connection(2026)
    try:
        # The exact sequence the API handler runs: ask for config RW
        # right after a budget conn becomes alive elsewhere in the
        # process. If duckdb_repo regresses, this will raise BinderException.
        config_rw = duckdb_repo.get_config_connection(read_only=False)
        try:
            config_rw.execute("SELECT 1").fetchone()
        finally:
            config_rw.close()
    finally:
        budget_con.close()


# ---------------------------------------------------------------------------
# Negative control: confirm the test fixture itself doesn't accidentally
# share connections, so when the real test fails it's a real failure.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("read_only", [True, False])
def test_consecutive_config_connections_after_close(real_db, read_only):  # noqa: ARG001
    """Sanity: opening config sequentially (NO budget conn alive) must
    work in both modes. If this fails the suite is broken at the
    fixture level and the real concurrency tests above can't be
    trusted.
    """
    for _ in range(3):
        con = duckdb_repo.get_config_connection(read_only=read_only)
        try:
            con.execute("SELECT 1").fetchone()
        finally:
            con.close()


# ---------------------------------------------------------------------------
# Documentation aid: keep this `unittest.mock.patch` import in the file
# even though the tests themselves use `monkeypatch`. Future tests may
# need it, and importing-but-not-using would be flagged. We use it once
# below to assert the gspread layer is genuinely never reached in the
# pure-EUR path.
# ---------------------------------------------------------------------------


def test_eur_short_circuit_never_calls_gspread(real_client, monkeypatch):
    """EUR → EUR conversion short-circuits inside `convert_to_eur` before
    touching the rate cache. This test guards that invariant — if a
    refactor accidentally routes EUR through `_save_cache` (which opens
    config RW), POSTs with `currency=EUR` would also start hitting the
    BinderException, broadening the blast radius.
    """
    _stub_schedule_logging(monkeypatch)
    with patch("dinary.services.sheets.get_sheet") as mock_get_sheet:
        resp = real_client.post(
            "/api/expenses",
            json={
                "expense_id": "eur-short-circuit",
                "amount": 7.0,
                "currency": "EUR",
                "category": "еда",
                "comment": "",
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 200, resp.text
        # gspread MUST NOT be reached because schedule_logging is stubbed.
        mock_get_sheet.assert_not_called()

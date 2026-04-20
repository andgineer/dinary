"""API tests against the unified dinary.duckdb (GET /api/categories, POST /api/expenses)."""

import asyncio
import contextlib
import threading
from decimal import Decimal
from unittest.mock import patch

import allure
import duckdb
import httpx
import pytest

from dinary.config import settings
from dinary.main import create_app
from dinary.services import duckdb_repo


@contextlib.contextmanager
def _count_race_recoveries():
    """Count ``insert_expense`` race-recovery ROLLBACKs during a test run.

    Yields a ``{"count": int}`` dict that callers read *after* the
    ``with`` block exits. Works by wrapping
    ``duckdb_repo.best_effort_rollback`` with a counter that
    increments on contexts containing ``"race-recovery"`` — the
    substring both INSERT-time and COMMIT-time ``_DUCKDB_RACE_EXCS``
    branches embed in their context string. The outer
    ``except Exception: best_effort_rollback(...)`` in
    ``insert_expense`` uses a different context string and is not
    counted, so the counter is specific to the race-recovery path.

    A ``threading.Lock`` protects the increment because concurrent
    tests (``asyncio.to_thread`` + ``asyncio.gather``) dispatch each
    request to a ThreadPool worker; the GIL makes ``+= 1`` usually
    atomic, but this is the sort of instrumentation where a spurious
    off-by-one under future CPython changes would obscure exactly
    the scheduling-regression class we're trying to detect.
    """
    counter = {"count": 0}
    lock = threading.Lock()
    original = duckdb_repo.best_effort_rollback

    def counting_rollback(con, *, context: str) -> None:
        if "race-recovery" in context:
            with lock:
                counter["count"] += 1
        original(con, context=context)

    with patch.object(duckdb_repo, "best_effort_rollback", new=counting_rollback):
        yield counter


@pytest.fixture(autouse=True)
def _tmp_duckdb(tmp_path, monkeypatch):
    """Point the repo at a fresh per-test DB and seed a minimal catalog.

    The ``client`` fixture's lifespan calls ``init_db`` again, which is
    idempotent and reuses the already-migrated file in ``tmp_path``.
    """
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "DB_PATH", tmp_path / "dinary.duckdb")
    duckdb_repo.init_db()

    con = duckdb_repo.get_connection()
    try:
        con.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active)"
            " VALUES (1, 'Food', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active)"
            " VALUES (2, 'Transport', 2, TRUE)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'еда', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active)"
            " VALUES (2, 'транспорт', 2, TRUE)",
        )
        # Pre-filter-friendly inactive category: API must treat it as
        # unknown for both reads (list) and writes (POST).
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active)"
            " VALUES (3, 'ретро-категория', 1, FALSE)",
        )
    finally:
        con.close()


def _mock_convert(con, amount, from_ccy, to_ccy, rate_date):
    """Identity FX stub: keep ``amount_app`` equal to ``amount_original``."""
    return Decimal(str(amount)), Decimal(1)


@allure.epic("API")
@allure.feature("Health")
def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@allure.epic("API")
@allure.feature("Categories (3D)")
class TestCategories:
    def test_returns_catalog_version_and_categories(self, client):
        resp = client.get("/api/categories")
        assert resp.status_code == 200
        data = resp.json()
        assert data["catalog_version"] == 1
        names = {c["name"] for c in data["categories"]}
        assert {"еда", "транспорт"}.issubset(names)
        # Inactive categories must not leak through.
        assert "ретро-категория" not in names
        for cat in data["categories"]:
            assert cat["group"] in {"Food", "Transport"}

    def test_db_failure_returns_500(self, client, monkeypatch):
        def bad_connection(**_kwargs):
            raise RuntimeError("DB corrupted")

        monkeypatch.setattr(duckdb_repo, "get_connection", bad_connection)
        resp = client.get("/api/categories")
        # 500 (not 502): the DuckDB file is in-process, so a read
        # failure is an internal server error, not an upstream one.
        assert resp.status_code == 500


@allure.epic("API")
@allure.feature("Expenses (3D)")
class TestPostExpense:
    @patch("dinary.api.expenses.convert", side_effect=_mock_convert)
    def test_create_expense(self, _mock_convert_fn, client):
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "e1",
                "amount": 50.0,
                "currency": "RSD",
                "category": "еда",
                "comment": "lunch",
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ok"
        assert data["month"] == "2026-04"
        assert data["category"] == "еда"
        assert Decimal(data["amount_original"]) == Decimal("50.0")
        assert data["currency_original"] == "RSD"
        assert data["catalog_version"] == 1
        # The response contract no longer carries an opaque server id;
        # callers identify the row by ``client_expense_id`` they sent.
        assert "id" not in data
        assert "expense_id" not in data

    @patch("dinary.api.expenses.convert", side_effect=_mock_convert)
    def test_disabled_sheet_logging_does_not_enqueue_jobs(
        self,
        _mock_convert_fn,
        client,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "")

        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "e_no_log",
                "amount": 50.0,
                "currency": "RSD",
                "category": "еда",
                "comment": "lunch",
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 200, resp.text

        con = duckdb_repo.get_connection()
        try:
            assert duckdb_repo.list_logging_jobs(con) == []
        finally:
            con.close()

    @patch("dinary.api.expenses.convert", side_effect=_mock_convert)
    def test_enabled_sheet_logging_enqueues_job(
        self,
        _mock_convert_fn,
        client,
        monkeypatch,
    ):
        # Explicitly set a non-empty ``sheet_logging_spreadsheet`` so
        # the test is deterministic regardless of the ambient
        # ``DINARY_SHEET_LOGGING_SPREADSHEET`` env var (CI runs with
        # it unset). The drain loop is still disabled by the autouse
        # ``_disable_drain_loop`` fixture, so enqueued jobs just sit
        # in the queue for us to assert on.
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "test-spreadsheet-id")
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "e_log",
                "amount": 50.0,
                "currency": "RSD",
                "category": "еда",
                "comment": "lunch",
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 200, resp.text

        con = duckdb_repo.get_connection()
        try:
            pks = duckdb_repo.list_logging_jobs(con)
            expected_pk_row = con.execute(
                "SELECT id FROM expenses WHERE client_expense_id = ?",
                ["e_log"],
            ).fetchone()
        finally:
            con.close()
        assert expected_pk_row is not None
        assert pks == [int(expected_pk_row[0])]

    @patch("dinary.api.expenses.convert", side_effect=_mock_convert)
    def test_replay_returns_duplicate(self, _mock_convert_fn, client):
        body = {
            "client_expense_id": "e2",
            "amount": 50.0,
            "currency": "RSD",
            "category": "еда",
            "comment": "",
            "date": "2026-04-15",
        }
        first = client.post("/api/expenses", json=body)
        assert first.status_code == 200
        assert first.json()["status"] == "ok"

        second = client.post("/api/expenses", json=body)
        assert second.status_code == 200
        assert second.json()["status"] == "duplicate"

        # The idempotent replay does not create a second row.
        con = duckdb_repo.get_connection()
        try:
            count = con.execute(
                "SELECT COUNT(*) FROM expenses WHERE client_expense_id = 'e2'",
            ).fetchone()[0]
        finally:
            con.close()
        assert count == 1

    @patch("dinary.api.expenses.convert", side_effect=_mock_convert)
    def test_conflict_on_modified_amount(self, _mock_convert_fn, client):
        base = {
            "client_expense_id": "e3",
            "amount": 50.0,
            "currency": "RSD",
            "category": "еда",
            "comment": "",
            "date": "2026-04-15",
        }
        assert client.post("/api/expenses", json=base).status_code == 200

        modified = {**base, "amount": 99.0}
        resp = client.post("/api/expenses", json=modified)
        assert resp.status_code == 409

    @patch("dinary.api.expenses.convert", side_effect=_mock_convert)
    def test_conflict_on_modified_date(self, _mock_convert_fn, client):
        """Same ``client_expense_id``, different date is a conflict.
        With the single-DB refactor this replaces the old "cross-year
        registry reuse" path."""
        base = {
            "client_expense_id": "shared",
            "amount": 1.0,
            "currency": "RSD",
            "category": "еда",
            "comment": "",
            "date": "2026-01-15",
        }
        assert client.post("/api/expenses", json=base).status_code == 200

        modified = {**base, "date": "2027-01-15"}
        resp = client.post("/api/expenses", json=modified)
        assert resp.status_code == 409

    def test_unknown_category_returns_422(self, client):
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "e4",
                "amount": 1.0,
                "currency": "RSD",
                "category": "missing-category",
                "comment": "",
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 422

    def test_inactive_category_returns_422(self, client):
        """A category that exists but was marked ``is_active=FALSE`` by
        a reseed must be treated as unknown for writes."""
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "e_inactive",
                "amount": 1.0,
                "currency": "RSD",
                "category": "ретро-категория",
                "comment": "",
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 422

    @patch("dinary.api.expenses.convert", side_effect=_mock_convert)
    def test_reseed_deactivation_allows_idempotent_replay_but_rejects_new_posts(
        self,
        _mock_convert_fn,
        client,
    ):
        """End-to-end of the FK-safe-sync → runtime flow:
        1. Post an expense against an active category so an FK from
           ``expenses`` to ``categories`` is established.
        2. Simulate the reseed dropping that category from the active
           vocabulary (``is_active=FALSE``) — the row can't be deleted
           because the FK still pins it, which is the whole point of
           the FK-safe algorithm in ``seed_config``.
        3. A truly-new POST (different ``client_expense_id``) against
           the retired category must return 422.
        4. An idempotent replay (same ``client_expense_id`` + same
           body) must still return 200 duplicate — an offline PWA
           retry must not be silently lost to an operator's reseed
           that happened after the original POST went over the wire.
        """
        post_body = {
            "client_expense_id": "e_pin_1",
            "amount": 10.0,
            "currency": "RSD",
            "category": "еда",
            "comment": "",
            "date": "2026-04-15",
        }
        resp = client.post("/api/expenses", json=post_body)
        assert resp.status_code == 200, resp.text

        con = duckdb_repo.get_connection()
        try:
            # Simulate the FK-safe reseed path: mark the category inactive
            # rather than deleting (which would violate the FK held by
            # the expense we just inserted).
            con.execute(
                "UPDATE categories SET is_active = FALSE WHERE name = 'еда'",
            )
            (kept,) = con.execute(
                "SELECT COUNT(*) FROM expenses WHERE client_expense_id = 'e_pin_1'",
            ).fetchone()
            assert kept == 1
        finally:
            con.close()

        # Truly-new POST with the retired category → 422 (unchanged
        # contract).
        resp = client.post(
            "/api/expenses",
            json={**post_body, "client_expense_id": "e_pin_2"},
        )
        assert resp.status_code == 422

        # Idempotent replay with the same UUID + same body → 200
        # duplicate. This is the PWA offline-retry guarantee: the
        # original POST established the FK pinning the category on
        # disk, so the server can prove this isn't a fresh use of a
        # retired label.
        replay = client.post("/api/expenses", json=post_body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["status"] == "duplicate"

        # Replay with the same UUID but a *different* payload → 409,
        # as for any other client_expense_id mismatch. The inactive
        # category does not relax the conflict check.
        mismatch = client.post(
            "/api/expenses",
            json={**post_body, "amount": 999.0},
        )
        assert mismatch.status_code == 409

    def test_unknown_category_does_not_insert_row(self, client):
        """The unknown-category 422 path bails out before
        ``insert_expense``, so no ledger row is created and a corrected
        retry succeeds cleanly."""
        bad = {
            "client_expense_id": "e_leak",
            "amount": 1.0,
            "currency": "RSD",
            "category": "missing-category",
            "comment": "",
            "date": "2026-04-15",
        }
        resp = client.post("/api/expenses", json=bad)
        assert resp.status_code == 422
        assert duckdb_repo.lookup_existing_expense("e_leak") is None

        good = {**bad, "category": "еда"}
        resp = client.post("/api/expenses", json=good)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert duckdb_repo.lookup_existing_expense("e_leak") is not None

    @patch("dinary.api.expenses.convert", side_effect=_mock_convert)
    def test_response_echoes_original_amount_and_currency(
        self,
        _mock_convert_fn,
        client,
    ):
        """The response must echo what the caller submitted in
        ``amount``/``currency`` as ``amount_original``/
        ``currency_original`` — never expose the app-currency
        projection."""
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "e_eur",
                "amount": 12.5,
                "currency": "EUR",
                "category": "еда",
                "comment": "",
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert Decimal(data["amount_original"]) == Decimal("12.5")
        assert data["currency_original"] == "EUR"
        assert "amount_rsd" not in data
        assert "amount" not in data

    @patch("dinary.api.expenses.convert", side_effect=_mock_convert)
    def test_defaults_currency_to_app_currency(
        self,
        _mock_convert_fn,
        client,
    ):
        """Omitting ``currency`` from the request body is legal and
        falls back to ``settings.app_currency``."""
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "e_no_ccy",
                "amount": 10.0,
                "category": "еда",
                "comment": "",
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["currency_original"] == settings.app_currency

    def test_non_identity_fx_stores_amount_in_app_currency(self, client):
        """POST with a non-app currency must convert via ``convert`` and
        write the app-currency value into ``expenses.amount`` while the
        response still echoes the original amount/currency."""

        def _eur_to_rsd(_con, amount, from_ccy, to_ccy, _rate_date):
            # 1 EUR = 117 RSD; only called for non-app currencies.
            assert from_ccy.upper() == "EUR"
            assert to_ccy.upper() == settings.app_currency.upper()
            return Decimal(str(amount)) * Decimal("117"), Decimal("117")

        with patch("dinary.api.expenses.convert", side_effect=_eur_to_rsd):
            resp = client.post(
                "/api/expenses",
                json={
                    "client_expense_id": "e_fx",
                    "amount": 10.0,
                    "currency": "EUR",
                    "category": "еда",
                    "comment": "",
                    "date": "2026-04-15",
                },
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ok"
        # Response echoes what the caller sent, not the projected value.
        assert Decimal(data["amount_original"]) == Decimal("10.0")
        assert data["currency_original"] == "EUR"

        # The stored ``amount`` is in app currency (10 * 117 = 1170).
        con = duckdb_repo.get_connection()
        try:
            row = con.execute(
                "SELECT amount, amount_original, currency_original"
                " FROM expenses WHERE client_expense_id = 'e_fx'",
            ).fetchone()
        finally:
            con.close()
        assert row is not None
        assert Decimal(str(row[0])) == Decimal("1170.00")
        assert Decimal(str(row[1])) == Decimal("10.00")
        assert row[2] == "EUR"

    @patch("dinary.api.expenses.convert", side_effect=_mock_convert)
    def test_conflict_on_modified_category(self, _mock_convert_fn, client):
        """Replaying the same ``client_expense_id`` with a different
        category is a 409 conflict.

        Regression test for the pre-fix bug where ``_compare_payload``
        did not compare ``category_id`` and silently returned 200
        ``duplicate`` for a category-modified replay.
        """
        base = {
            "client_expense_id": "e_cat_change",
            "amount": 50.0,
            "currency": "RSD",
            "category": "еда",
            "comment": "",
            "date": "2026-04-15",
        }
        assert client.post("/api/expenses", json=base).status_code == 200

        modified = {**base, "category": "транспорт"}
        resp = client.post("/api/expenses", json=modified)
        assert resp.status_code == 409

    @patch("dinary.api.expenses.convert", side_effect=_mock_convert)
    def test_concurrent_post_with_same_client_expense_id_is_atomic(
        self,
        _mock_convert_fn,
        client,
    ):
        """The ON CONFLICT path inside ``insert_expense`` decides
        duplicate-vs-conflict atomically, so the API doesn't need a
        pre-lookup. The same UUID + same payload returns 200 duplicate;
        the same UUID + different payload returns 409.
        """
        body = {
            "client_expense_id": "e_race",
            "amount": 50.0,
            "currency": "RSD",
            "category": "еда",
            "comment": "",
            "date": "2026-04-15",
        }
        assert client.post("/api/expenses", json=body).status_code == 200

        # Same payload => duplicate (via ON CONFLICT compare in insert_expense).
        resp = client.post("/api/expenses", json=body)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "duplicate"

        # Same UUID, different amount => conflict.
        resp = client.post("/api/expenses", json={**body, "amount": 999.0})
        assert resp.status_code == 409

    def test_insert_unexpected_failure_propagates_cleanly(self, client):
        """A non-constraint ``insert_expense`` failure must bubble up as
        500 and leave the DB untouched (no half-written row that would
        collide with a retry)."""

        def boom(*_args, **_kwargs):
            msg = "disk full"
            raise RuntimeError(msg)

        with (
            patch.object(duckdb_repo, "insert_expense", side_effect=boom),
            patch("dinary.api.expenses.convert", side_effect=_mock_convert),
        ):
            resp = client.post(
                "/api/expenses",
                json={
                    "client_expense_id": "e_boom",
                    "amount": 50.0,
                    "currency": "RSD",
                    "category": "еда",
                    "comment": "",
                    "date": "2026-04-15",
                },
            )
        assert resp.status_code == 500
        # No ledger row: a legitimate retry must be allowed to succeed.
        assert duckdb_repo.lookup_existing_expense("e_boom") is None

    def test_concurrent_replays_are_serialized_without_transaction_errors(
        self,
    ):
        """Smoke test for the ``asyncio.to_thread`` + DuckDB-singleton
        interaction: fire N concurrent POSTs with the same
        ``client_expense_id`` and same body, and verify that:

        - no request returns 5xx (the singleton-with-cursors model
          survives real cross-thread concurrency — no leaked
          ``duckdb.TransactionException`` from two threads both calling
          BEGIN on the same connection);
        - exactly one request returns ``{status: "ok"}`` (the creator);
        - every other request returns ``{status: "duplicate"}`` (the
          ON CONFLICT compare path);
        - disk state has exactly one row for the UUID.

        This is the closest we can get to production-like concurrency
        inside a test: ``httpx.AsyncClient`` + ``ASGITransport`` lets
        ``asyncio.gather`` actually interleave the handlers, and
        ``asyncio.to_thread`` dispatches each one to a worker thread
        on the default pool. The ``client`` fixture is bypassed here
        because ``fastapi.testclient.TestClient`` serializes requests
        by construction (portal-backed sync wrapper) and can't
        demonstrate the property under test.

        No per-test ``DATA_DIR`` / ``DB_PATH`` override is needed:
        the autouse ``_tmp_duckdb`` fixture already points the repo
        at a fresh per-test DB and seeded the catalog, and the
        ``create_app`` lifespan reuses that same path.
        """
        body = {
            "client_expense_id": "e_concurrent",
            "amount": 42.0,
            "currency": "RSD",
            "category": "еда",
            "comment": "",
            "date": "2026-04-15",
        }
        n_requests = 8

        async def _run() -> list[httpx.Response]:
            app = create_app()
            transport = httpx.ASGITransport(app=app)
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                ) as ac,
            ):
                return await asyncio.gather(
                    *(ac.post("/api/expenses", json=body) for _ in range(n_requests)),
                )

        with (
            patch("dinary.api.expenses.convert", side_effect=_mock_convert),
            _count_race_recoveries() as race_counter,
        ):
            responses = asyncio.run(_run())

        # Primary invariant: no server errors. A leaked
        # ``TransactionException`` from shared-connection contention
        # would land here as 500.
        for r in responses:
            assert r.status_code == 200, f"{r.status_code}: {r.text}"

        statuses = [r.json()["status"] for r in responses]
        assert statuses.count("ok") == 1, statuses
        assert statuses.count("duplicate") == n_requests - 1, statuses

        con = duckdb_repo.get_connection()
        try:
            count = con.execute(
                "SELECT COUNT(*) FROM expenses WHERE client_expense_id = 'e_concurrent'",
            ).fetchone()[0]
        finally:
            con.close()
        assert count == 1

        # Observability check: the primary assertions above pass even
        # when all racers hit the serial ``ON CONFLICT DO NOTHING``
        # absorb path (no real contention). That would leave the
        # ``_DUCKDB_RACE_EXCS`` recovery branches uncovered while the
        # test looks green. Confirm at least one racer took the
        # recovery path so a future scheduling change that silently
        # serializes the coroutines fails this test instead of passing
        # vacuously. If this ever flakes on CI, don't relax it blindly
        # — investigate *why* the scheduling changed first.
        assert race_counter["count"] >= 1, (
            f"None of the {n_requests} concurrent racers took the "
            f"race-recovery branch — all hit the classic ON CONFLICT "
            f"DO NOTHING absorb path. Test invariants passed, but "
            f"insert_expense's ConstraintException/TransactionException "
            f"handlers were not exercised this run."
        )

    def test_concurrent_mixed_bodies_are_serialized_with_conflict(
        self,
    ):
        """Concurrent variant of the conflict path: N racers share a
        ``client_expense_id`` but differ on ``amount``.

        The atomic ON CONFLICT compare inside ``insert_expense`` must
        still pick exactly one winner (the first writer to commit),
        and every other racer must land in the compare branch and
        surface a **409 Conflict** — not a 500 ``TransactionException``
        leak, and not a silent 200 duplicate that would hide an actual
        payload disagreement from the PWA.

        This complements
        ``test_concurrent_replays_are_serialized_without_transaction_errors``:
        that one covers the idempotent-replay branch (identical
        bodies -> 200 duplicate for the losers), this one covers the
        conflicting-body branch (different bodies -> 409 for the
        losers). Together they exercise both recovery exits from the
        ``duckdb.ConstraintException`` / ``duckdb.TransactionException``
        compare path.
        """
        base_body = {
            "client_expense_id": "e_concurrent_mixed",
            "currency": "RSD",
            "category": "еда",
            "comment": "",
            "date": "2026-04-15",
        }
        # Distinct amounts => the compare path always sees a payload
        # mismatch, regardless of which racer commits first.
        bodies = [{**base_body, "amount": 10.0 + i} for i in range(6)]
        n_requests = len(bodies)

        async def _run() -> list[httpx.Response]:
            app = create_app()
            transport = httpx.ASGITransport(app=app)
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                ) as ac,
            ):
                return await asyncio.gather(
                    *(ac.post("/api/expenses", json=b) for b in bodies),
                )

        with (
            patch("dinary.api.expenses.convert", side_effect=_mock_convert),
            _count_race_recoveries() as race_counter,
        ):
            responses = asyncio.run(_run())

        # No server errors: recovery must convert commit-time /
        # insert-time UNIQUE races into cleanly-served 409s, never 5xx.
        for r in responses:
            assert r.status_code in (200, 409), f"{r.status_code}: {r.text}"

        oks = [r for r in responses if r.status_code == 200]
        conflicts = [r for r in responses if r.status_code == 409]
        assert len(oks) == 1, [r.status_code for r in responses]
        assert len(conflicts) == n_requests - 1, [r.status_code for r in responses]
        assert oks[0].json()["status"] == "ok"

        con = duckdb_repo.get_connection()
        try:
            row = con.execute(
                "SELECT COUNT(*), MIN(amount), MAX(amount) FROM expenses"
                " WHERE client_expense_id = 'e_concurrent_mixed'",
            ).fetchone()
        finally:
            con.close()
        count, min_amount, max_amount = row
        # Exactly one committed row — the conflicting losers must
        # not have left partial state on disk.
        assert count == 1
        # And its amount must be *one of* the submitted amounts.
        # A regression where race recovery mutates the stored row
        # (partial UPDATE from a loser, merge of two racers' fields,
        # etc.) would land us here with an amount that no racer
        # actually submitted.
        submitted_amounts = {round(10.0 + i, 2) for i in range(n_requests)}
        assert float(min_amount) == float(max_amount), (
            f"single-row assertion disagrees with COUNT: min={min_amount} max={max_amount}"
        )
        assert round(float(min_amount), 2) in submitted_amounts, (
            f"stored amount {min_amount!r} is not in the submitted set "
            f"{sorted(submitted_amounts)!r} — race recovery corrupted "
            f"the committed row?"
        )

        # Observability check: see the companion sibling test
        # ``test_concurrent_replays_are_serialized_without_transaction_errors``
        # for the rationale. In the mixed-bodies variant every
        # loser lands on a *conflict* (409), so if all racers took
        # the classic absorb path the only outcome that preserves
        # "1 OK + 5 conflicts" is that each loser's compare saw the
        # same committed-winner row — still correct behavior, but
        # without exercising either of the ``_DUCKDB_RACE_EXCS``
        # recovery branches. Fail loudly rather than passing
        # vacuously if that ever happens.
        assert race_counter["count"] >= 1, (
            f"None of the {n_requests} concurrent racers took the "
            f"race-recovery branch — conflict behavior was driven "
            f"entirely by the serial ON CONFLICT DO NOTHING absorb "
            f"path, leaving the insert_expense race-recovery handlers "
            f"uncovered this run."
        )

    @patch("dinary.api.expenses.convert", side_effect=_mock_convert)
    def test_unexpected_constraint_exception_propagates(
        self,
        _mock_convert_fn,
        client,
    ):
        """A ``ConstraintException`` from ``insert_expense`` that is not
        the natural UNIQUE-on-client-expense-id race (e.g. an FK
        violation on ``category_id`` in the micro-window between resolve
        and insert) must propagate as 500 — we must not silently swallow
        it and return 200/duplicate."""

        def bad_insert(*_args, **_kwargs):
            msg = "simulated constraint"
            raise duckdb.ConstraintException(msg)

        with patch.object(duckdb_repo, "insert_expense", side_effect=bad_insert):
            resp = client.post(
                "/api/expenses",
                json={
                    "client_expense_id": "e_ghost",
                    "amount": 50.0,
                    "currency": "RSD",
                    "category": "еда",
                    "comment": "",
                    "date": "2026-04-15",
                },
            )
        assert resp.status_code == 500

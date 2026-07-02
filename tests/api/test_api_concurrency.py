"""POST ``/api/expenses`` under real concurrency: ON CONFLICT duplicate/conflict
handling, SQLite's serialized-writer guarantees, and failure-propagation paths."""

import asyncio
import sqlite3
from unittest.mock import patch

import allure
import httpx

from dinary.main import create_app
from dinary.db import category_seed, storage
from dinary.db.expenses import lookup_existing_expense

from _api_helpers import (  # noqa: F401  (autouse + helpers)
    _count_race_recoveries,
    _mock_get_rate,
    db,
)


@allure.epic("Expenses")
@allure.feature("API")
@allure.story("Concurrency")
class TestPostExpenseConcurrency:
    @patch("dinary.adapters.exchange_rates.get_rate", side_effect=_mock_get_rate)
    def test_concurrent_post_with_same_client_expense_id_is_atomic(
        self,
        _mock_convert_fn,
        client,
    ):
        """The ON CONFLICT compare in ``insert_expense`` decides duplicate-vs-conflict
        atomically, so the API doesn't need a pre-lookup."""
        body = {
            "client_expense_id": "e_race",
            "amount": 50.0,
            "currency": "RSD",
            "category_id": 1,
            "comment": "",
            "expense_datetime": "2026-04-15T12:00:00+02:00",
        }
        assert client.post("/api/expenses", json=body).status_code == 200

        # Same payload => duplicate (via ON CONFLICT compare in insert_expense).
        resp = client.post("/api/expenses", json=body)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "duplicate"

        # Same UUID, different amount => conflict.
        resp = client.post("/api/expenses", json={**body, "amount": 999.0})
        assert resp.status_code == 409

    def test_concurrent_replays_are_serialized_without_transaction_errors(
        self,
    ):
        """Uses ``httpx.AsyncClient`` + ``ASGITransport`` (not the ``client`` fixture)
        because ``fastapi.testclient.TestClient`` serializes requests by construction
        and can't demonstrate real cross-thread concurrency."""
        body = {
            "client_expense_id": "e_concurrent",
            "amount": 42.0,
            "currency": "RSD",
            "category_id": 1,
            "comment": "",
            "expense_datetime": "2026-04-15T12:00:00+02:00",
        }
        n_requests = 8

        async def _run() -> list[httpx.Response]:
            app = create_app()
            transport = httpx.ASGITransport(app=app)
            with patch.object(category_seed, "bootstrap_categories"):
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
            patch("dinary.adapters.exchange_rates.get_rate", side_effect=_mock_get_rate),
            _count_race_recoveries() as race_counter,
        ):
            responses = asyncio.run(_run())

        # Primary invariant: no server errors. A leaked
        # ``OperationalError`` from BEGIN IMMEDIATE contention or a
        # mis-handled ``IntegrityError`` would land here as 500.
        for r in responses:
            assert r.status_code == 200, f"{r.status_code}: {r.text}"

        statuses = [r.json()["status"] for r in responses]
        assert statuses.count("ok") == 1, statuses
        assert statuses.count("duplicate") == n_requests - 1, statuses

        con = storage.get_connection()
        try:
            count = con.execute(
                "SELECT COUNT(*) FROM expenses WHERE client_expense_id = 'e_concurrent'",
            ).fetchone()[0]
        finally:
            con.close()
        assert count == 1

        # SQLite's single-writer model absorbs losers via ON CONFLICT before they
        # ever raise IntegrityError, so this should stay 0; a nonzero count means
        # something (e.g. a dropped ON CONFLICT) broke that guarantee.
        assert race_counter["count"] == 0, (
            f"Unexpectedly saw {race_counter['count']} race-recovery "
            f"rollbacks under SQLite's serialized-writer model — "
            f"concurrent POSTs should absorb through ON CONFLICT "
            f"DO NOTHING, not surface as IntegrityError/OperationalError."
        )

    def test_concurrent_mixed_bodies_are_serialized_with_conflict(
        self,
    ):
        """Conflict-path counterpart to
        ``test_concurrent_replays_are_serialized_without_transaction_errors``: racers
        share a ``client_expense_id`` but differ on ``amount``, so every loser must
        land in the compare branch as 409, not a silent 200 duplicate."""
        base_body = {
            "client_expense_id": "e_concurrent_mixed",
            "currency": "RSD",
            "category_id": 1,
            "comment": "",
            "expense_datetime": "2026-04-15T12:00:00+02:00",
        }
        # Distinct amounts => the compare path always sees a payload
        # mismatch, regardless of which racer commits first.
        bodies = [{**base_body, "amount": 10.0 + i} for i in range(6)]
        n_requests = len(bodies)

        async def _run() -> list[httpx.Response]:
            app = create_app()
            transport = httpx.ASGITransport(app=app)
            with patch.object(category_seed, "bootstrap_categories"):
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
            patch("dinary.adapters.exchange_rates.get_rate", side_effect=_mock_get_rate),
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

        con = storage.get_connection()
        try:
            row = con.execute(
                "SELECT COUNT(*), MIN(amount), MAX(amount) FROM expenses"
                " WHERE client_expense_id = 'e_concurrent_mixed'",
            ).fetchone()
        finally:
            con.close()
        count, min_amount, max_amount = row
        assert count == 1
        submitted_amounts = {round(10.0 + i, 2) for i in range(n_requests)}
        assert float(min_amount) == float(max_amount), (
            f"single-row assertion disagrees with COUNT: min={min_amount} max={max_amount}"
        )
        assert round(float(min_amount), 2) in submitted_amounts, (
            f"stored amount {min_amount!r} is not in the submitted set "
            f"{sorted(submitted_amounts)!r} — race recovery corrupted "
            f"the committed row?"
        )

        assert race_counter["count"] == 0, (
            f"Unexpectedly saw {race_counter['count']} race-recovery "
            f"rollbacks under SQLite's serialized-writer model — "
            f"concurrent mixed-body POSTs should absorb through ON "
            f"CONFLICT DO NOTHING, not surface as "
            f"IntegrityError/OperationalError."
        )


@allure.epic("Expenses")
@allure.feature("API")
@allure.story("Concurrency")
class TestPostExpenseFailurePropagation:
    def test_insert_unexpected_failure_propagates_cleanly(self, client):
        """A non-constraint ``insert_expense`` failure must bubble up as
        500 and leave the DB untouched (no half-written row that would
        collide with a retry)."""

        def boom(*_args, **_kwargs):
            msg = "disk full"
            raise RuntimeError(msg)

        with (
            patch("dinary.api.controllers.expenses.insert_expense", side_effect=boom),
            patch("dinary.adapters.exchange_rates.get_rate", side_effect=_mock_get_rate),
        ):
            resp = client.post(
                "/api/expenses",
                json={
                    "client_expense_id": "e_boom",
                    "amount": 50.0,
                    "currency": "RSD",
                    "category_id": 1,
                    "comment": "",
                    "expense_datetime": "2026-04-15T12:00:00+02:00",
                },
            )
        assert resp.status_code == 500
        # No ledger row: a legitimate retry must be allowed to succeed.
        assert lookup_existing_expense("e_boom") is None

    @patch("dinary.adapters.exchange_rates.get_rate", side_effect=_mock_get_rate)
    def test_unexpected_constraint_exception_propagates(
        self,
        _mock_convert_fn,
        client,
    ):
        """A ``sqlite3.IntegrityError`` from ``insert_expense`` that is
        not the natural UNIQUE-on-client-expense-id race (e.g. an FK
        violation on ``category_id`` in the micro-window between resolve
        and insert) must propagate as 500 — we must not silently swallow
        it and return 200/duplicate."""

        def bad_insert(*_args, **_kwargs):
            msg = "simulated constraint"
            raise sqlite3.IntegrityError(msg)

        with patch("dinary.api.controllers.expenses.insert_expense", side_effect=bad_insert):
            resp = client.post(
                "/api/expenses",
                json={
                    "client_expense_id": "e_ghost",
                    "amount": 50.0,
                    "currency": "RSD",
                    "category_id": 1,
                    "comment": "",
                    "expense_datetime": "2026-04-15T12:00:00+02:00",
                },
            )
        assert resp.status_code == 500

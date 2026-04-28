"""POST ``/api/expenses`` under real concurrency:

- Atomic ON CONFLICT compare in ``insert_expense`` keeps duplicate vs
  conflict decisions correct under same-UUID racers.
- ``asyncio.to_thread`` + ``asyncio.gather`` exercise the per-request
  SQLite-connection model and SQLite's serialized-writer guarantees.
- Failure-propagation paths around ``insert_expense`` (unexpected
  ``RuntimeError`` and unexpected ``IntegrityError`` carry-over) must
  surface as 500, not silently 200/duplicate.
"""

import asyncio
import sqlite3
from unittest.mock import patch

import allure
import httpx

from dinary.main import create_app
from dinary.services import ledger_repo

from _api_helpers import (  # noqa: F401  (autouse + helpers)
    _count_race_recoveries,
    _mock_get_rate,
    db,
)


@allure.epic("API")
@allure.feature("Expenses (3D) — concurrency")
class TestPostExpenseConcurrency:
    @patch("dinary.api.expenses.get_rate", side_effect=_mock_get_rate)
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
            "category_id": 1,
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

    def test_concurrent_replays_are_serialized_without_transaction_errors(
        self,
    ):
        """Smoke test for the ``asyncio.to_thread`` + per-request
        SQLite-connection interaction: fire N concurrent POSTs with
        the same ``client_expense_id`` and same body, and verify that:

        - no request returns 5xx (the per-request-connection model
          survives real cross-thread concurrency — no leaked
          ``sqlite3.OperationalError`` from ``BEGIN IMMEDIATE`` write
          contention between two worker threads);
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
        the autouse ``_tmp_db`` fixture already points the repo
        at a fresh per-test DB and seeded the catalog, and the
        ``create_app`` lifespan reuses that same path.
        """
        body = {
            "client_expense_id": "e_concurrent",
            "amount": 42.0,
            "currency": "RSD",
            "category_id": 1,
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
            patch("dinary.api.expenses.get_rate", side_effect=_mock_get_rate),
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

        con = ledger_repo.get_connection()
        try:
            count = con.execute(
                "SELECT COUNT(*) FROM expenses WHERE client_expense_id = 'e_concurrent'",
            ).fetchone()[0]
        finally:
            con.close()
        assert count == 1

        # Observability: under SQLite's single-writer model
        # (``BEGIN IMMEDIATE`` + ``busy_timeout``), concurrent writers
        # serialize on the database-level write lock and the winner
        # commits before any loser's INSERT runs. Losers therefore
        # hit ``ON CONFLICT (client_expense_id) DO NOTHING`` on an
        # already-committed row and absorb the conflict without ever
        # raising ``IntegrityError`` — so ``_RACE_EXCS`` recovery is
        # structurally unreachable from this coroutine-gather. The
        # recovery branches in ``insert_expense`` remain as defensive
        # code for any future writer that bypasses ``ON CONFLICT``;
        # they have dedicated unit coverage elsewhere. Assert the
        # counter stays at 0 so a regression that *does* start
        # surfacing ``IntegrityError`` here (e.g. a busy_timeout drop
        # or an ON CONFLICT removal) trips this test loudly.
        assert race_counter["count"] == 0, (
            f"Unexpectedly saw {race_counter['count']} race-recovery "
            f"rollbacks under SQLite's serialized-writer model — "
            f"concurrent POSTs should absorb through ON CONFLICT "
            f"DO NOTHING, not surface as IntegrityError/OperationalError."
        )

    def test_concurrent_mixed_bodies_are_serialized_with_conflict(
        self,
    ):
        """Concurrent variant of the conflict path: N racers share a
        ``client_expense_id`` but differ on ``amount``.

        The atomic ON CONFLICT compare inside ``insert_expense`` must
        still pick exactly one winner (the first writer to commit),
        and every other racer must land in the compare branch and
        surface a **409 Conflict** — not a 500 ``OperationalError``
        leak, and not a silent 200 duplicate that would hide an actual
        payload disagreement from the PWA.

        This complements
        ``test_concurrent_replays_are_serialized_without_transaction_errors``:
        that one covers the idempotent-replay branch (identical
        bodies -> 200 duplicate for the losers), this one covers the
        conflicting-body branch (different bodies -> 409 for the
        losers). Together they exercise both recovery exits from the
        ``sqlite3.IntegrityError`` / ``sqlite3.OperationalError``
        compare path.
        """
        base_body = {
            "client_expense_id": "e_concurrent_mixed",
            "currency": "RSD",
            "category_id": 1,
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
            patch("dinary.api.expenses.get_rate", side_effect=_mock_get_rate),
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

        con = ledger_repo.get_connection()
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

        # Observability: see the companion sibling
        # ``test_concurrent_replays_are_serialized_without_transaction_errors``
        # for the rationale. Under SQLite's serialized-writer model
        # every loser absorbs the conflict through ON CONFLICT DO NOTHING
        # and then falls into the compare path without raising. A
        # non-zero counter would mean a regression started surfacing
        # ``IntegrityError`` / ``OperationalError`` at this layer,
        # which is exactly what ``BEGIN IMMEDIATE`` + ``busy_timeout``
        # is meant to prevent.
        assert race_counter["count"] == 0, (
            f"Unexpectedly saw {race_counter['count']} race-recovery "
            f"rollbacks under SQLite's serialized-writer model — "
            f"concurrent mixed-body POSTs should absorb through ON "
            f"CONFLICT DO NOTHING, not surface as "
            f"IntegrityError/OperationalError."
        )


@allure.epic("API")
@allure.feature("Expenses (3D) — failure propagation")
class TestPostExpenseFailurePropagation:
    def test_insert_unexpected_failure_propagates_cleanly(self, client):
        """A non-constraint ``insert_expense`` failure must bubble up as
        500 and leave the DB untouched (no half-written row that would
        collide with a retry)."""

        def boom(*_args, **_kwargs):
            msg = "disk full"
            raise RuntimeError(msg)

        with (
            patch.object(ledger_repo, "insert_expense", side_effect=boom),
            patch("dinary.api.expenses.get_rate", side_effect=_mock_get_rate),
        ):
            resp = client.post(
                "/api/expenses",
                json={
                    "client_expense_id": "e_boom",
                    "amount": 50.0,
                    "currency": "RSD",
                    "category_id": 1,
                    "comment": "",
                    "date": "2026-04-15",
                },
            )
        assert resp.status_code == 500
        # No ledger row: a legitimate retry must be allowed to succeed.
        assert ledger_repo.lookup_existing_expense("e_boom") is None

    @patch("dinary.api.expenses.get_rate", side_effect=_mock_get_rate)
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

        with patch.object(ledger_repo, "insert_expense", side_effect=bad_insert):
            resp = client.post(
                "/api/expenses",
                json={
                    "client_expense_id": "e_ghost",
                    "amount": 50.0,
                    "currency": "RSD",
                    "category_id": 1,
                    "comment": "",
                    "date": "2026-04-15",
                },
            )
        assert resp.status_code == 500

"""POST ``/api/expenses`` happy-path: create, replay duplicate, response
contract (``amount_original``/``currency_original``), FX projection,
event auto-tags union, and sheet-logging enqueue gating.

Validation (422), conflict (409), and concurrency live in dedicated
sibling files (``test_api_validation.py``, ``test_api_conflict.py``,
``test_api_concurrency.py``).
"""

from decimal import Decimal
from unittest.mock import patch

import allure

from dinary.config import settings
from dinary.services import ledger_repo

from _api_helpers import _mock_get_rate, db  # noqa: F401  (autouse + helper)


@allure.epic("API")
@allure.feature("Expenses (3D)")
class TestPostExpenseHappyPath:
    @patch("dinary.api.expenses.get_rate", side_effect=_mock_get_rate)
    def test_create_expense(self, _mock_convert_fn, client):
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "e1",
                "amount": 50.0,
                "currency": "RSD",
                "category_id": 1,
                "comment": "lunch",
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ok"
        assert data["month"] == "2026-04"
        assert data["category_id"] == 1
        assert Decimal(data["amount_original"]) == Decimal("50.0")
        assert data["currency_original"] == "RSD"
        assert data["catalog_version"] == 1
        # The response contract no longer carries an opaque server id;
        # callers identify the row by ``client_expense_id`` they sent.
        assert "id" not in data
        assert "expense_id" not in data

    @patch("dinary.api.expenses.get_rate", side_effect=_mock_get_rate)
    def test_replay_returns_duplicate(self, _mock_convert_fn, client):
        body = {
            "client_expense_id": "e2",
            "amount": 50.0,
            "currency": "RSD",
            "category_id": 1,
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
        con = ledger_repo.get_connection()
        try:
            count = con.execute(
                "SELECT COUNT(*) FROM expenses WHERE client_expense_id = 'e2'",
            ).fetchone()[0]
        finally:
            con.close()
        assert count == 1

    @patch("dinary.api.expenses.get_rate", side_effect=_mock_get_rate)
    def test_event_and_tags_are_stored(self, _mock_convert_fn, client):
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "e_evt",
                "amount": 10.0,
                "currency": "RSD",
                "category_id": 1,
                "event_id": 1,
                "tag_ids": [1, 2],
                "comment": "",
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 200, resp.text
        con = ledger_repo.get_connection()
        try:
            row = con.execute(
                "SELECT id, event_id FROM expenses WHERE client_expense_id = 'e_evt'",
            ).fetchone()
            assert row is not None
            assert int(row[1]) == 1
            tags = sorted(ledger_repo.get_expense_tags(con, int(row[0])))
        finally:
            con.close()
        assert tags == [1, 2]

    @patch("dinary.api.expenses.get_rate", side_effect=_mock_get_rate)
    def test_event_auto_tags_unioned_into_expense(self, _mock_convert_fn, client):
        """POST ``/api/expenses`` must union ``events.auto_tags`` into
        the stored tag set so runtime writes carry the same invariant
        the historical importer applies: attaching a vacation event to
        an expense guarantees both ``отпуск`` and ``путешествия`` show
        up regardless of what the client submitted. This mirrors the
        importer's ``_union_event_auto_tags`` behaviour and is the
        main "same-invariant-on-both-paths" contract tests around
        ``events.auto_tags`` rely on.
        """
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "UPDATE events SET auto_tags = ? WHERE id = 1",
                ['["собака"]'],
            )
        finally:
            con.close()

        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "e_auto",
                "amount": 12.0,
                "currency": "RSD",
                "category_id": 1,
                "event_id": 1,
                "tag_ids": [2],
                "comment": "",
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 200, resp.text

        con = ledger_repo.get_connection()
        try:
            row = con.execute(
                "SELECT id FROM expenses WHERE client_expense_id = 'e_auto'",
            ).fetchone()
            assert row is not None
            stored = sorted(ledger_repo.get_expense_tags(con, int(row[0])))
        finally:
            con.close()
        assert stored == [1, 2]

        replay = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "e_auto",
                "amount": 12.0,
                "currency": "RSD",
                "category_id": 1,
                "event_id": 1,
                "tag_ids": [2],
                "comment": "",
                "date": "2026-04-15",
            },
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["status"] == "duplicate"

    @patch("dinary.api.expenses.get_rate", side_effect=_mock_get_rate)
    def test_response_echoes_original_amount_and_currency(
        self,
        _mock_convert_fn,
        client,
    ):
        """The response must echo what the caller submitted in
        ``amount``/``currency`` as ``amount_original``/
        ``currency_original`` — never expose the accounting-currency
        projection."""
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "e_eur",
                "amount": 12.5,
                "currency": "EUR",
                "category_id": 1,
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

    @patch("dinary.api.expenses.get_rate", side_effect=_mock_get_rate)
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
                "category_id": 1,
                "comment": "",
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["currency_original"] == settings.app_currency

    def test_non_identity_fx_stores_amount_in_accounting_currency(self, client):
        """POST with a currency that differs from the accounting currency
        must convert via ``convert`` and write the accounting-currency
        value into ``expenses.amount``, while the response still echoes
        the original amount/currency. The PWA default input currency
        (``app_currency`` = RSD) becomes the source here so the stored
        ``amount`` ends up in EUR (the accounting currency)."""

        def _rsd_to_eur(_con, _rate_date, from_ccy, to_ccy, *, offline=False):
            # 117 RSD = 1 EUR; return rate so amount * rate gives EUR
            assert from_ccy.upper() == "RSD"
            assert to_ccy.upper() == settings.accounting_currency.upper()
            return Decimal("1") / Decimal("117")

        with patch("dinary.api.expenses.get_rate", side_effect=_rsd_to_eur):
            resp = client.post(
                "/api/expenses",
                json={
                    "client_expense_id": "e_fx",
                    "amount": 1170.0,
                    "currency": "RSD",
                    "category_id": 1,
                    "comment": "",
                    "date": "2026-04-15",
                },
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ok"
        # Response echoes what the caller sent, not the projected value.
        assert Decimal(data["amount_original"]) == Decimal("1170.0")
        assert data["currency_original"] == "RSD"

        # The stored ``amount`` is in accounting currency (1170 / 117 = 10 EUR).
        con = ledger_repo.get_connection()
        try:
            row = con.execute(
                "SELECT amount, amount_original, currency_original"
                " FROM expenses WHERE client_expense_id = 'e_fx'",
            ).fetchone()
        finally:
            con.close()
        assert row is not None
        assert Decimal(str(row[0])) == Decimal("10.00")
        assert Decimal(str(row[1])) == Decimal("1170.00")
        assert row[2] == "RSD"


@allure.epic("API")
@allure.feature("Expenses (3D) — sheet-logging enqueue")
class TestPostExpenseSheetLogging:
    @patch("dinary.api.expenses.get_rate", side_effect=_mock_get_rate)
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
                "category_id": 1,
                "comment": "lunch",
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 200, resp.text

        con = ledger_repo.get_connection()
        try:
            assert ledger_repo.list_logging_jobs(con) == []
        finally:
            con.close()

    @patch("dinary.api.expenses.get_rate", side_effect=_mock_get_rate)
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
                "category_id": 1,
                "comment": "lunch",
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 200, resp.text

        con = ledger_repo.get_connection()
        try:
            pks = ledger_repo.list_logging_jobs(con)
            expected_pk_row = con.execute(
                "SELECT id FROM expenses WHERE client_expense_id = ?",
                ["e_log"],
            ).fetchone()
        finally:
            con.close()
        assert expected_pk_row is not None
        assert pks == [int(expected_pk_row[0])]


def _insert_expense_direct(con, eid, cid, *, receipt_id=None, days_ago=0):
    dt = f"datetime('now', '-{days_ago} days')"
    receipt_col = "" if receipt_id is None else ", receipt_id"
    receipt_val = "" if receipt_id is None else f", {receipt_id}"
    con.execute(
        f"INSERT INTO expenses (id, client_expense_id, datetime, amount,"  # noqa: S608
        f" amount_original, currency_original, category_id{receipt_col})"
        f" VALUES ({eid}, 'e{eid}', {dt}, 10.0, 10.0, 'RSD', {cid}{receipt_val})",
    )


@allure.epic("API")
@allure.feature("Expenses (3D) — default_group_id / default_category_ids in response")
class TestExpenseDefaults:
    """POST /api/expenses returns usage-based defaults so the PWA can
    pre-select the most-used group and category on next form open."""

    def test_no_history_returns_null_defaults(self, client):
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "d1",
                "amount": 10.0,
                "currency": "RSD",
                "category_id": 1,
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # The just-saved expense is manual and recent — group 1 / category 1
        # should now be the default.
        assert data["default_group_id"] == 1
        assert data["default_category_ids"]["1"] == 1

    def test_most_used_manual_category_returned(self, client):
        con = ledger_repo.get_connection()
        try:
            _insert_expense_direct(con, 10, 1)
            _insert_expense_direct(con, 11, 1)
            _insert_expense_direct(con, 12, 2)
        finally:
            con.close()
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "d2",
                "amount": 10.0,
                "currency": "RSD",
                "category_id": 1,
                "date": "2026-04-15",
            },
        )
        data = resp.json()
        assert data["default_group_id"] == 1
        assert data["default_category_ids"]["1"] == 1

    def test_receipt_sourced_expenses_excluded(self, client):
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO receipts (id, client_receipt_id, url, store_name_raw,"
                " store_pib_raw, total_amount, invoice_number)"
                " VALUES (1, 'r1', 'http://x', '', '', 0, '')",
            )
            for i in range(10, 15):
                _insert_expense_direct(con, i, 1, receipt_id=1)
        finally:
            con.close()
        # Now post one manual expense with category 1 → it should win
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "d3",
                "amount": 10.0,
                "currency": "RSD",
                "category_id": 1,
                "date": "2026-04-15",
            },
        )
        data = resp.json()
        # Only the manual one counts; receipt ones are excluded
        assert data["default_group_id"] == 1
        assert data["default_category_ids"]["1"] == 1

    def test_old_expenses_excluded_from_defaults(self, client):
        con = ledger_repo.get_connection()
        try:
            # Category 2 used heavily but 100 days ago (outside 3-month window)
            for i in range(20, 26):
                _insert_expense_direct(con, i, 2, days_ago=100)
        finally:
            con.close()
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "d4",
                "amount": 10.0,
                "currency": "RSD",
                "category_id": 1,
                "date": "2026-04-15",
            },
        )
        data = resp.json()
        # Old category 2 expenses excluded; only the fresh manual save counts
        assert data["default_category_ids"]["1"] == 1

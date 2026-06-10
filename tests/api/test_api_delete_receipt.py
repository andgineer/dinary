"""GET /api/receipts/:id and DELETE /api/receipts/:id — receipt fetch and cascade deletion."""

import allure

from dinary.db import storage

from _api_helpers import db  # noqa: F401 (autouse fixture)


def _insert_receipt_with_expenses(n_expenses=2):
    """Insert a receipt and n expenses linked to it. Returns (receipt_id, [expense_ids])."""
    con = storage.get_connection()
    try:
        con.execute(
            "INSERT INTO receipts (client_receipt_id, url, store_name_raw, purchase_datetime)"
            " VALUES ('rcid-test','https://x','Maxi','2026-05-10T12:00:00')",
        )
        rid = int(con.execute("SELECT last_insert_rowid()").fetchone()[0])
        eids = []
        for i in range(n_expenses):
            con.execute(
                "INSERT INTO expenses (client_expense_id, datetime, amount, amount_original,"
                " currency_original, category_id, receipt_id)"
                " VALUES (?,datetime('now'),100,100,'RSD',1,?)",
                [f"ce-{i}", rid],
            )
            eids.append(int(con.execute("SELECT last_insert_rowid()").fetchone()[0]))
            con.execute(
                "INSERT INTO receipt_items (receipt_id, name_raw, unit_price, quantity,"
                " total_price, tax_label, expense_id) VALUES (?,?,100,1,100,'Е',?)",
                [rid, f"item-{i}", eids[-1]],
            )
        return rid, eids
    finally:
        con.close()


def _insert_job(
    rid,
    status="pending",
    retry_count=0,
    last_error=None,
    retry_after=None,
    claimed_at=None,
):
    con = storage.get_connection()
    try:
        con.execute(
            "INSERT INTO receipt_classification_jobs"
            " (receipt_id, status, retry_count, last_error, retry_after, claimed_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [rid, status, retry_count, last_error, retry_after, claimed_at],
        )
    finally:
        con.close()


@allure.epic("Receipts")
@allure.feature("API")
class TestGetReceipt:
    def test_get_receipt_returns_metadata(self, client, db):  # noqa: ARG002
        rid, _ = _insert_receipt_with_expenses(2)
        resp = client.get(f"/api/receipts/{rid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == rid
        assert data["merchant"] == "Maxi"

    def test_get_receipt_without_job_has_null_job(self, client, db):  # noqa: ARG002
        rid, _ = _insert_receipt_with_expenses(0)
        resp = client.get(f"/api/receipts/{rid}")
        assert resp.json()["job"] is None

    def test_get_receipt_with_pending_job(self, client, db):  # noqa: ARG002
        rid, _ = _insert_receipt_with_expenses(0)
        _insert_job(rid, status="pending", retry_count=2, retry_after="2026-06-10 12:00:00")
        resp = client.get(f"/api/receipts/{rid}")
        assert resp.json()["job"] == {
            "status": "pending",
            "retry_count": 2,
            "last_error": None,
            "retry_after": "2026-06-10 12:00:00",
            "last_attempted_at": None,
        }

    def test_get_receipt_with_in_progress_job(self, client, db):  # noqa: ARG002
        rid, _ = _insert_receipt_with_expenses(0)
        _insert_job(rid, status="in_progress", retry_count=1, claimed_at="2026-06-10 11:00:00")
        resp = client.get(f"/api/receipts/{rid}")
        job = resp.json()["job"]
        assert job["status"] == "in_progress"
        assert job["retry_after"] is None
        assert job["last_attempted_at"] == "2026-06-10 11:00:00"

    def test_get_receipt_with_poisoned_job(self, client, db):  # noqa: ARG002
        rid, _ = _insert_receipt_with_expenses(0)
        _insert_job(
            rid,
            status="poisoned",
            retry_count=4,
            last_error="No items found via /specifications or journal for https://x",
            claimed_at="2026-06-10 11:00:00",
        )
        resp = client.get(f"/api/receipts/{rid}")
        job = resp.json()["job"]
        assert job["status"] == "poisoned"
        assert job["retry_count"] == 4
        assert job["last_error"] == "No items found via /specifications or journal for https://x"
        assert job["retry_after"] is None
        assert job["last_attempted_at"] == "2026-06-10 11:00:00"

    def test_get_receipt_without_include_omits_expenses(self, client, db):  # noqa: ARG002
        rid, _ = _insert_receipt_with_expenses(2)
        resp = client.get(f"/api/receipts/{rid}")
        assert resp.status_code == 200
        assert "expenses" not in resp.json()

    def test_get_receipt_with_include_expenses_returns_expenses(self, client, db):  # noqa: ARG002
        rid, eids = _insert_receipt_with_expenses(3)
        resp = client.get(f"/api/receipts/{rid}?include=expenses")
        assert resp.status_code == 200
        data = resp.json()
        assert "expenses" in data
        assert len(data["expenses"]) == 3
        assert "total" in data

    def test_get_nonexistent_receipt_returns_404(self, client, db):  # noqa: ARG002
        resp = client.get("/api/receipts/999999")
        assert resp.status_code == 404


@allure.epic("Receipts")
@allure.feature("API")
class TestDeleteReceipt:
    def test_delete_receipt_returns_204(self, client, db):  # noqa: ARG002
        rid, _ = _insert_receipt_with_expenses(2)
        resp = client.delete(f"/api/receipts/{rid}")
        assert resp.status_code == 204

    def test_delete_receipt_cascades_to_expenses(self, client, db):  # noqa: ARG002
        rid, eids = _insert_receipt_with_expenses(3)
        client.delete(f"/api/receipts/{rid}")
        con = storage.get_connection()
        try:
            for eid in eids:
                row = con.execute("SELECT id FROM expenses WHERE id = ?", [eid]).fetchone()
                assert row is None, f"expense {eid} should have been deleted"
            receipt_row = con.execute("SELECT id FROM receipts WHERE id = ?", [rid]).fetchone()
            assert receipt_row is None
        finally:
            con.close()

    def test_delete_receipt_with_expense_tags(self, client, db):  # noqa: ARG002
        """expense_tags FK to expenses must be cleared before expenses are deleted."""
        rid, eids = _insert_receipt_with_expenses(1)
        con = storage.get_connection()
        try:
            tag_id = con.execute(
                "INSERT INTO tags (name) VALUES ('groceries') RETURNING id"
            ).fetchone()[0]
            con.execute(
                "INSERT INTO expense_tags (expense_id, tag_id) VALUES (?, ?)",
                [eids[0], tag_id],
            )
        finally:
            con.close()
        resp = client.delete(f"/api/receipts/{rid}")
        assert resp.status_code == 204

    def test_delete_receipt_with_sheet_logging_job(self, client, db):  # noqa: ARG002
        """sheet_logging_jobs FK to expenses must be cleared before expenses are deleted."""
        rid, eids = _insert_receipt_with_expenses(1)
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO sheet_logging_jobs (expense_id, status) VALUES (?, 'pending')",
                [eids[0]],
            )
        finally:
            con.close()
        resp = client.delete(f"/api/receipts/{rid}")
        assert resp.status_code == 204

    def test_delete_receipt_with_llm_call_log(self, client, db):  # noqa: ARG002
        """llmbroker_call_log FK to receipts must be cleared before receipts are deleted."""
        rid, _ = _insert_receipt_with_expenses(1)
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO llmbroker_call_log (execution_id, status) VALUES (?, 'ok')",
                [str(rid)],
            )
        finally:
            con.close()
        resp = client.delete(f"/api/receipts/{rid}")
        assert resp.status_code == 204

    def test_delete_nonexistent_receipt_returns_404(self, client, db):  # noqa: ARG002
        resp = client.delete("/api/receipts/999999")
        assert resp.status_code == 404

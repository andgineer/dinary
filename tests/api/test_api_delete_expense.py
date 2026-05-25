"""DELETE /api/expenses/{id} — manual expense deletion."""

import allure

from dinary.db import storage

from _api_helpers import db  # noqa: F401 (autouse fixture)


def _insert_expense(client, *, receipt_id=None):
    """Insert a minimal expense via direct SQL and return its id."""
    con = storage.get_connection()
    try:
        con.execute(
            "INSERT INTO expenses (client_expense_id, datetime, amount, amount_original,"
            " currency_original, category_id) VALUES (?,datetime('now'),100,100,'RSD',1)",
            [f"ceid-{id(client)}"],
        )
        expense_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        if receipt_id is not None:
            con.execute("UPDATE expenses SET receipt_id = ? WHERE id = ?", [receipt_id, expense_id])
        return int(expense_id)
    finally:
        con.close()


def _insert_receipt(url="https://suf.purs.gov.rs/v/?vl=test"):
    con = storage.get_connection()
    try:
        con.execute("INSERT INTO receipts (client_receipt_id, url) VALUES (?,?)", [f"r-{url}", url])
        rid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        return int(rid)
    finally:
        con.close()


@allure.epic("Expenses")
@allure.feature("API")
class TestDeleteExpense:
    def test_delete_manual_expense_returns_204(self, client, db):  # noqa: ARG002
        eid = _insert_expense(client)
        resp = client.delete(f"/api/expenses/{eid}")
        assert resp.status_code == 204

    def test_deleted_expense_is_gone_from_db(self, client, db):  # noqa: ARG002
        eid = _insert_expense(client)
        client.delete(f"/api/expenses/{eid}")
        con = storage.get_connection()
        try:
            row = con.execute("SELECT id FROM expenses WHERE id = ?", [eid]).fetchone()
        finally:
            con.close()
        assert row is None

    def test_delete_nonexistent_expense_returns_404(self, client, db):  # noqa: ARG002
        resp = client.delete("/api/expenses/999999")
        assert resp.status_code == 404

    def test_delete_receipt_backed_expense_returns_409(self, client, db):  # noqa: ARG002
        rid = _insert_receipt()
        eid = _insert_expense(client, receipt_id=rid)
        resp = client.delete(f"/api/expenses/{eid}")
        assert resp.status_code == 409
        assert "receipt" in resp.json()["detail"].lower()

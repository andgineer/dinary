"""Tests for GET /api/expenses/recent and PATCH /api/expenses/{id}."""

import json

import allure

from dinary.db import storage

from _api_helpers import db  # noqa: F401


def _insert_expense(con, eid, cid, *, client_expense_id=None, days_ago=0, receipt_id=None):
    eid_str = client_expense_id or f"e{eid}"
    dt_expr = f"datetime('now', '-{days_ago} days')"
    if receipt_id is not None:
        con.execute(
            f"INSERT INTO expenses (id, client_expense_id, datetime, amount,"  # noqa: S608
            f" amount_original, currency_original, category_id, receipt_id)"
            f" VALUES ({eid}, '{eid_str}', {dt_expr}, 10.0, 10.0, 'RSD', {cid}, {receipt_id})",
        )
    else:
        con.execute(
            f"INSERT INTO expenses (id, client_expense_id, datetime, amount,"  # noqa: S608
            f" amount_original, currency_original, category_id)"
            f" VALUES ({eid}, '{eid_str}', {dt_expr}, 10.0, 10.0, 'RSD', {cid})",
        )


@allure.epic("API")
@allure.feature("Expenses — GET /api/expenses/recent")
class TestGetRecentExpenses:
    def test_returns_list(self, client, db):  # noqa: ARG002
        resp = client.get("/api/expenses/recent")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_empty_when_no_expenses(self, client, db):  # noqa: ARG002
        resp = client.get("/api/expenses/recent")
        assert resp.json() == []

    def test_returns_newest_first(self, client, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            _insert_expense(con, 1, 1, days_ago=5)
            _insert_expense(con, 2, 1, days_ago=1)
            _insert_expense(con, 3, 1, days_ago=10)
        finally:
            con.close()

        resp = client.get("/api/expenses/recent")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 3
        # Newest first: days_ago=1 (id=2), then days_ago=5 (id=1), then days_ago=10 (id=3)
        assert items[0]["id"] == 2
        assert items[1]["id"] == 1
        assert items[2]["id"] == 3

    def test_items_have_tags_field(self, client, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            _insert_expense(con, 1, 1)
        finally:
            con.close()

        resp = client.get("/api/expenses/recent")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert "tags" in items[0]
        assert isinstance(items[0]["tags"], list)

    def test_items_have_has_rule_field(self, client, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            _insert_expense(con, 1, 1)
        finally:
            con.close()

        resp = client.get("/api/expenses/recent")
        assert resp.status_code == 200
        items = resp.json()
        assert "has_rule" in items[0]
        assert isinstance(items[0]["has_rule"], bool)

    def test_has_rule_false_for_non_receipt_expense(self, client, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            _insert_expense(con, 1, 1)
        finally:
            con.close()

        resp = client.get("/api/expenses/recent")
        assert resp.json()[0]["has_rule"] is False

    def test_tags_populated_for_expense_with_tags(self, client, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            _insert_expense(con, 1, 1)
            con.execute("INSERT INTO expense_tags (expense_id, tag_id) VALUES (1, 1)")
        finally:
            con.close()

        resp = client.get("/api/expenses/recent")
        assert resp.status_code == 200
        tags = resp.json()[0]["tags"]
        assert len(tags) == 1
        assert tags[0]["id"] == 1
        assert tags[0]["name"] == "собака"

    def test_capped_at_30(self, client, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            for i in range(1, 35):
                _insert_expense(con, i, 1, client_expense_id=f"cap-{i}")
        finally:
            con.close()

        resp = client.get("/api/expenses/recent")
        assert resp.status_code == 200
        assert len(resp.json()) == 30

    def test_has_rule_true_for_receipt_expense_with_matching_rule(self, client, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            con.execute("INSERT INTO stores (id, chain_name, pib) VALUES (1, 'Lidl', '100')")
            con.execute(
                "INSERT INTO receipts (id, client_receipt_id, url, store_id)"
                " VALUES (1, 'hr-r1', 'https://x', 1)"
            )
            con.execute(
                "INSERT INTO classification_rules"
                " (store_id, item_name_normalized, category_id, confidence_level, source)"
                " VALUES (1, 'hleb', 1, 3, 'llm')"
            )
            con.execute(
                "INSERT INTO expenses"
                " (id, client_expense_id, datetime, amount, amount_original,"
                "  currency_original, category_id, receipt_id, store_id)"
                " VALUES (1, 'hr-e1', datetime('now'), 100.0, 100.0, 'RSD', 1, 1, 1)"
            )
            con.execute(
                "INSERT INTO receipt_items"
                " (id, receipt_id, name_raw, name_normalized, total_price,"
                "  quantity, unit_price, expense_id)"
                " VALUES (1, 1, 'hleb raw', 'hleb', 100.0, 1, 100.0, 1)"
            )
        finally:
            con.close()

        resp = client.get("/api/expenses/recent")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["has_rule"] is True


@allure.epic("API")
@allure.feature("Expenses — PATCH /api/expenses/{id}")
class TestPatchExpense:
    def _seed_receipt_expense(self, con, expense_id=1, category_id=1):
        con.execute("INSERT INTO stores (id, chain_name, pib) VALUES (1, 'Lidl', '100')")
        con.execute(
            "INSERT INTO receipts (id, client_receipt_id, url, store_id)"
            " VALUES (1, 'pe-r1', 'https://x', 1)"
        )
        con.execute(
            f"INSERT INTO expenses (id, client_expense_id, datetime, amount,"  # noqa: S608
            f" amount_original, currency_original, category_id, confidence_level, receipt_id, store_id)"
            f" VALUES ({expense_id}, 'pe-e{expense_id}', '2026-05-01T10:00:00', 100.0, 100.0,"
            f" 'RSD', {category_id}, 3, 1, 1)",
        )
        con.execute(
            f"INSERT INTO receipt_items"
            f" (id, receipt_id, name_raw, name_normalized, total_price, quantity, unit_price,"
            f"  category_id, confidence_level, expense_id)"
            f" VALUES (1, 1, 'hleb raw', 'hleb', 100.0, 1, 100.0, {category_id}, 3, {expense_id})",
        )

    def test_not_found_returns_404(self, client, db):  # noqa: ARG002
        resp = client.patch("/api/expenses/9999", json={})
        assert resp.status_code == 404

    def test_category_update(self, client, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            self._seed_receipt_expense(con)
        finally:
            con.close()

        resp = client.patch("/api/expenses/1", json={"category_id": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 1
        assert data["category_id"] == 2

        con = storage.get_connection()
        try:
            row = con.execute("SELECT category_id FROM expenses WHERE id = 1").fetchone()
        finally:
            con.close()
        assert row[0] == 2

    def test_tag_update(self, client, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            self._seed_receipt_expense(con)
        finally:
            con.close()

        resp = client.patch("/api/expenses/1", json={"tag_ids": [1, 2]})
        assert resp.status_code == 200
        data = resp.json()
        assert sorted(data["tag_ids"]) == [1, 2]

        con = storage.get_connection()
        try:
            tag_ids = sorted(
                r[0]
                for r in con.execute(
                    "SELECT tag_id FROM expense_tags WHERE expense_id = 1"
                ).fetchall()
            )
        finally:
            con.close()
        assert tag_ids == [1, 2]

    def test_event_update(self, client, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            self._seed_receipt_expense(con)
        finally:
            con.close()

        resp = client.patch("/api/expenses/1", json={"event_id": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["event_id"] == 1
        assert data["event_name"] == "evt-2026"

    def test_clear_event(self, client, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            self._seed_receipt_expense(con)
            con.execute("UPDATE expenses SET event_id = 1 WHERE id = 1")
        finally:
            con.close()

        resp = client.patch("/api/expenses/1", json={"clear_event": True})
        assert resp.status_code == 200
        assert resp.json()["event_id"] is None

    def test_update_rule_false_does_not_touch_rules(self, client, db):  # noqa: ARG002
        """PATCH with only tag changes and update_rule=False must not create any rules."""
        con = storage.get_connection()
        try:
            self._seed_receipt_expense(con)
        finally:
            con.close()

        # Only change tags, no category_id → correct_category_sync is NOT called,
        # update_rule=False → the rule upsert block is also skipped.
        resp = client.patch("/api/expenses/1", json={"tag_ids": [1], "update_rule": False})
        assert resp.status_code == 200

        con = storage.get_connection()
        try:
            rule_count = con.execute("SELECT COUNT(*) FROM classification_rules").fetchone()[0]
        finally:
            con.close()
        assert rule_count == 0, (
            "update_rule=False must not create or modify any classification rules"
        )

    def test_update_rule_true_creates_rule_for_receipt_expense(self, client, db):  # noqa: ARG002
        """PATCH with update_rule=True persists the current expense category as a rule."""
        con = storage.get_connection()
        try:
            self._seed_receipt_expense(con)
        finally:
            con.close()

        # Only change tags, no category_id → correct_category_sync not called.
        # update_rule=True → rule is upserted from the expense's current category.
        resp = client.patch("/api/expenses/1", json={"tag_ids": [1], "update_rule": True})
        assert resp.status_code == 200

        con = storage.get_connection()
        try:
            rule = con.execute(
                "SELECT category_id, source FROM classification_rules"
                " WHERE item_name_normalized = 'hleb'"
            ).fetchone()
        finally:
            con.close()
        assert rule is not None, "update_rule=True must create a classification rule"
        assert rule[0] == 1  # expense category_id=1
        assert rule[1] == "user_correction"

    def test_update_rule_true_no_rule_for_non_receipt_expense(self, client, db):  # noqa: ARG002
        """PATCH with update_rule=True on a non-receipt expense creates no rules."""
        con = storage.get_connection()
        try:
            _insert_expense(con, 1, 1)
        finally:
            con.close()

        resp = client.patch("/api/expenses/1", json={"tag_ids": [1], "update_rule": True})
        assert resp.status_code == 200

        con = storage.get_connection()
        try:
            rule_count = con.execute("SELECT COUNT(*) FROM classification_rules").fetchone()[0]
        finally:
            con.close()
        assert rule_count == 0, "non-receipt expense has no receipt_items → no rule created"

    def test_response_includes_category_name(self, client, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            self._seed_receipt_expense(con)
        finally:
            con.close()

        resp = client.patch("/api/expenses/1", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "category_name" in data
        assert isinstance(data["category_name"], str)

    def test_category_and_update_rule_true_writes_rule_once_with_tags(self, client, db):  # noqa: ARG002
        """PATCH with category_id + update_rule=True writes the rule once, carrying tag_ids."""
        con = storage.get_connection()
        try:
            self._seed_receipt_expense(con)
        finally:
            con.close()

        resp = client.patch(
            "/api/expenses/1",
            json={"category_id": 2, "tag_ids": [1], "update_rule": True},
        )
        assert resp.status_code == 200

        con = storage.get_connection()
        try:
            rows = con.execute(
                "SELECT category_id, tag_ids FROM classification_rules"
                " WHERE item_name_normalized = 'hleb'"
            ).fetchall()
        finally:
            con.close()

        assert len(rows) == 1, "exactly one rule row — no duplicate upsert"
        assert rows[0][0] == 2, "rule must reflect the new category"
        assert json.loads(rows[0][1]) == [1], "rule must carry the supplied tag_ids"


@allure.epic("API")
@allure.feature("Expenses — POST /api/expenses response")
class TestPostExpenseResponse:
    def test_response_includes_frequent_categories(self, client, db):  # noqa: ARG002
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "fc-test-1",
                "amount": "50.00",
                "category_id": 1,
                "date": "2026-05-01",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "frequent_categories" in data
        assert isinstance(data["frequent_categories"], list)

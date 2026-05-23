"""Tests for review-page UX spec changes.

Covers:
- confirm_rules_bulk: confidence bumped to 4 on rule + items + expenses
- build_rules_feed with doubtful_only=True: only confidence < 4 rows, has_more uses doubtful count
- frequent_categories_sync: receipt-backed expenses now included
"""

import allure

from dinary.api.controllers.rules import build_rules_feed, confirm_rules_bulk
from dinary.api.controllers.catalog import frequent_categories_sync
from dinary.db import storage

from _api_helpers import db  # noqa: F401


def _seed_doubtful_rule(con, item_name="hleb", confidence=3):
    con.execute("INSERT OR IGNORE INTO shop_chains (id, name) VALUES (1, 'Lidl')")
    con.execute(
        "INSERT OR IGNORE INTO stores (id, name, chain_id, pib) VALUES (1, 'Lidl', 1, '100')"
    )
    con.execute(
        "INSERT OR IGNORE INTO receipts (id, client_receipt_id, url, store_id)"
        " VALUES (1, 'r1', 'https://x', 1)"
    )
    con.execute(
        "INSERT INTO classification_rules"
        " (store_id, item_name_normalized, category_id, confidence_level, source)"
        f" VALUES (1, '{item_name}', 1, {confidence}, 'llm')"
    )
    con.execute(
        "INSERT INTO expenses (id, datetime, amount, amount_original, currency_original,"
        "                      category_id, confidence_level, receipt_id, store_id)"
        " VALUES (1, '2026-05-01T10:00:00', 100.0, 100.0, 'RSD', 1, 3, 1, 1)"
    )
    con.execute(
        "INSERT INTO receipt_items"
        " (id, receipt_id, name_raw, name_normalized, total_price, quantity, unit_price, expense_id)"
        f" VALUES (1, 1, '{item_name} raw', '{item_name}', 100.0, 1, 100.0, 1)"
    )


@allure.epic("API")
@allure.feature("Review Page UX — confirm_rules_bulk")
class TestConfirmRulesBulk:
    def test_sets_confidence_to_4_on_rule(self, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            _seed_doubtful_rule(con)
            rule_id = con.execute(
                "SELECT id FROM classification_rules WHERE item_name_normalized='hleb'"
            ).fetchone()[0]
        finally:
            con.close()

        con = storage.get_connection()
        try:
            confirm_rules_bulk(con, [rule_id])
            row = con.execute(
                "SELECT confidence_level, source FROM classification_rules WHERE id=?",
                [rule_id],
            ).fetchone()
        finally:
            con.close()

        assert row[0] == 4
        assert row[1] == "user_correction"

    def test_sets_confidence_to_4_on_receipt_items(self, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            _seed_doubtful_rule(con)
            rule_id = con.execute(
                "SELECT id FROM classification_rules WHERE item_name_normalized='hleb'"
            ).fetchone()[0]
        finally:
            con.close()

        con = storage.get_connection()
        try:
            confirm_rules_bulk(con, [rule_id])
            level = con.execute(
                "SELECT confidence_level FROM receipt_items WHERE name_normalized='hleb'"
            ).fetchone()[0]
        finally:
            con.close()

        assert level == 4

    def test_sets_confidence_to_4_on_expenses(self, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            _seed_doubtful_rule(con)
            rule_id = con.execute(
                "SELECT id FROM classification_rules WHERE item_name_normalized='hleb'"
            ).fetchone()[0]
        finally:
            con.close()

        con = storage.get_connection()
        try:
            confirm_rules_bulk(con, [rule_id])
            level = con.execute("SELECT confidence_level FROM expenses WHERE id=1").fetchone()[0]
        finally:
            con.close()

        assert level == 4

    def test_returns_count_of_confirmed(self, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            _seed_doubtful_rule(con)
            rule_id = con.execute(
                "SELECT id FROM classification_rules WHERE item_name_normalized='hleb'"
            ).fetchone()[0]
            result = confirm_rules_bulk(con, [rule_id])
        finally:
            con.close()

        assert result == 1

    def test_empty_list_returns_zero(self, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            result = confirm_rules_bulk(con, [])
        finally:
            con.close()

        assert result == 0

    def test_via_http_endpoint(self, client, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            _seed_doubtful_rule(con)
            rule_id = con.execute(
                "SELECT id FROM classification_rules WHERE item_name_normalized='hleb'"
            ).fetchone()[0]
        finally:
            con.close()

        resp = client.post("/api/rules/confirm-all", json={"rule_ids": [rule_id]})
        assert resp.status_code == 200
        assert resp.json()["confirmed"] == 1


@allure.epic("API")
@allure.feature("Review Page UX — doubtful_only feed filter")
class TestRulesFeedDoubtfulOnly:
    def _seed_both(self, con):
        con.execute("INSERT OR IGNORE INTO shop_chains (id, name) VALUES (1, 'Lidl')")
        con.execute("INSERT INTO stores (id, name, chain_id, pib) VALUES (1, 'Lidl', 1, '100')")
        con.execute(
            "INSERT INTO receipts (id, client_receipt_id, url, store_id)"
            " VALUES (1, 'r1', 'https://x', 1)"
        )
        con.execute(
            "INSERT INTO receipts (id, client_receipt_id, url, store_id)"
            " VALUES (2, 'r2', 'https://y', 1)"
        )
        con.execute(
            "INSERT INTO classification_rules"
            " (id, store_id, item_name_normalized, category_id, confidence_level, source)"
            " VALUES (10, 1, 'doubtful_item', 1, 3, 'llm')"
        )
        con.execute(
            "INSERT INTO classification_rules"
            " (id, store_id, item_name_normalized, category_id, confidence_level, source)"
            " VALUES (11, 1, 'certain_item', 1, 4, 'llm')"
        )
        con.execute(
            "INSERT INTO expenses (id, datetime, amount, amount_original, currency_original,"
            "                      category_id, confidence_level, receipt_id, store_id)"
            " VALUES (10, '2026-05-01T10:00:00', 50.0, 50.0, 'RSD', 1, 3, 1, 1)"
        )
        con.execute(
            "INSERT INTO expenses (id, datetime, amount, amount_original, currency_original,"
            "                      category_id, confidence_level, receipt_id, store_id)"
            " VALUES (11, '2026-05-02T10:00:00', 80.0, 80.0, 'RSD', 1, 4, 2, 1)"
        )
        con.execute(
            "INSERT INTO receipt_items"
            " (id, receipt_id, name_raw, name_normalized, total_price, quantity, unit_price, expense_id)"
            " VALUES (10, 1, 'doubtful_item raw', 'doubtful_item', 50.0, 1, 50.0, 10)"
        )
        con.execute(
            "INSERT INTO receipt_items"
            " (id, receipt_id, name_raw, name_normalized, total_price, quantity, unit_price, expense_id)"
            " VALUES (11, 2, 'certain_item raw', 'certain_item', 80.0, 1, 80.0, 11)"
        )

    def test_doubtful_only_true_excludes_certain_rules(self, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            self._seed_both(con)
            con.row_factory = __import__("sqlite3").Row
            result = build_rules_feed(con, page=1, page_size=20, doubtful_only=True)
        finally:
            con.close()

        ids = [r["id"] for r in result["items"]]
        assert 10 in ids
        assert 11 not in ids

    def test_doubtful_only_false_includes_all_rules(self, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            self._seed_both(con)
            con.row_factory = __import__("sqlite3").Row
            result = build_rules_feed(con, page=1, page_size=20, doubtful_only=False)
        finally:
            con.close()

        ids = [r["id"] for r in result["items"]]
        assert 10 in ids
        assert 11 in ids

    def test_has_more_uses_doubtful_count_when_doubtful_only(self, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            self._seed_both(con)
            con.row_factory = __import__("sqlite3").Row
            result = build_rules_feed(con, page=1, page_size=1, doubtful_only=True)
        finally:
            con.close()

        assert result["has_more"] is False

    def test_endpoint_defaults_to_doubtful_only_true(self, client, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            self._seed_both(con)
        finally:
            con.close()

        resp = client.get("/api/rules/feed")
        data = resp.json()
        ids = [r["id"] for r in data["items"]]
        assert 10 in ids
        assert 11 not in ids


@allure.epic("API")
@allure.feature("Review Page UX — frequent_categories includes receipt expenses")
class TestFrequentCategoriesManualOnly:
    def test_receipt_backed_expenses_excluded(self, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO receipts (id, client_receipt_id, url) VALUES (1, 'r1', 'https://x')"
            )
            con.execute(
                "INSERT INTO expenses (client_expense_id, datetime, amount, amount_original,"
                " currency_original, category_id, receipt_id)"
                " VALUES ('re1', datetime('now'), 10.0, 10.0, 'RSD', 1, 1)"
            )
            result = frequent_categories_sync(con)
        finally:
            con.close()

        assert result == [], "receipt-backed expenses must not appear in frequent categories"

    def test_only_manual_expenses_counted_when_mixed(self, db):  # noqa: ARG002
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO receipts (id, client_receipt_id, url) VALUES (1, 'r1', 'https://x')"
            )
            for i in range(3):
                con.execute(
                    "INSERT INTO expenses (client_expense_id, datetime, amount, amount_original,"
                    " currency_original, category_id, receipt_id)"
                    f" VALUES ('re{i}', datetime('now'), 10.0, 10.0, 'RSD', 1, 1)"
                )
            con.execute(
                "INSERT INTO expenses (client_expense_id, datetime, amount, amount_original,"
                " currency_original, category_id)"
                " VALUES ('man1', datetime('now'), 10.0, 10.0, 'RSD', 2)"
            )
            result = frequent_categories_sync(con)
        finally:
            con.close()

        assert len(result) == 1
        assert result[0].id == 2, "only the manually-entered category should appear"

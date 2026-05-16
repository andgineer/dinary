"""Receipt review API tests."""

import allure
import pytest

from dinary.services import ledger_repo

from _api_helpers import db  # noqa: F401


def _seed_review_data(conn):
    # category_groups id=1 and categories id=1 are seeded by the db fixture already
    conn.execute("INSERT INTO stores (id, chain_name, pib) VALUES (1, 'Lidl', '100')")
    conn.execute(
        "INSERT INTO receipts (id, client_receipt_id, url, store_id) VALUES (1, 'r1', 'https://x', 1)"
    )
    conn.execute(
        "INSERT INTO classification_rules"
        " (store_id, item_name_normalized, category_id, confidence_level, source)"
        " VALUES (1, 'hleb', 1, 3, 'llm')"
    )
    conn.execute(
        "INSERT INTO expenses (id, datetime, amount, amount_original, currency_original, category_id,"
        "                      confidence_level, receipt_id, store_id)"
        " VALUES (42, '2026-05-01T10:00:00', 120.0, 120.0, 'RSD', 1, 3, 1, 1)"
    )
    conn.execute(
        "INSERT INTO receipt_items"
        " (id, receipt_id, name_raw, name_normalized, total_price, quantity, unit_price, expense_id)"
        " VALUES (1, 1, 'hleb raw', 'hleb', 120.0, 1, 120.0, 42)"
    )


def _seed_certain_expense(conn):
    conn.execute("INSERT INTO stores (id, chain_name, pib) VALUES (1, 'Lidl', '100')")
    conn.execute(
        "INSERT INTO receipts (id, client_receipt_id, url, store_id)"
        " VALUES (1, 'r1', 'https://x', 1)"
    )
    conn.execute(
        "INSERT INTO expenses (id, datetime, amount, amount_original, currency_original,"
        "                      category_id, confidence_level, receipt_id, store_id)"
        " VALUES (10, '2026-05-01T10:00:00', 200.0, 200.0, 'RSD', 1, 4, 1, 1)"
    )


@allure.epic("API")
@allure.feature("Receipt Review")
class TestReviewFeed:
    def test_empty_feed(self, client, db):  # noqa: ARG002
        resp = client.get("/api/receipts/review/feed")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["doubtful_count"] == 0

    def test_doubtful_item_in_block1(self, client, db):  # noqa: ARG002
        conn = ledger_repo.get_connection()
        try:
            _seed_review_data(conn)
        finally:
            conn.close()

        resp = client.get("/api/receipts/review/feed")
        data = resp.json()
        assert data["doubtful_count"] == 1
        assert len(data["items"]) >= 1
        doubtful = [i for i in data["items"] if i["is_doubtful"]]
        assert len(doubtful) == 1
        d = doubtful[0]
        assert d["name"] == "hleb"
        assert d["store"] == "Lidl"
        assert d["total"] == 120.0
        assert d["count"] == 1
        assert d["currency"] == "RSD"
        assert d["confidence_level"] == 3
        assert d["current_category_id"] == 1
        assert d["expense_id"] == 42
        assert "id" in d

    def test_pagination(self, client, db):  # noqa: ARG002
        resp = client.get("/api/receipts/review/feed?page=1&page_size=5")
        assert resp.status_code == 200

    def test_page_size_limit(self, client, db):  # noqa: ARG002
        resp = client.get("/api/receipts/review/feed?page_size=101")
        assert resp.status_code == 422


@allure.epic("API")
@allure.feature("Receipt Review")
class TestReviewFeedBlock2:
    def test_certain_expense_in_block2(self, client, db):  # noqa: ARG002
        conn = ledger_repo.get_connection()
        try:
            _seed_certain_expense(conn)
        finally:
            conn.close()

        resp = client.get("/api/receipts/review/feed")
        data = resp.json()
        certain = [i for i in data["items"] if not i["is_doubtful"]]
        assert len(certain) == 1
        c = certain[0]
        assert c["total"] == 200.0
        assert c["currency"] == "RSD"
        assert c["store"] == "Lidl"
        assert c["confidence_level"] == 4
        assert "id" in c
        assert "datetime" in c

    def test_block2_not_included_in_doubtful_count(self, client, db):  # noqa: ARG002
        conn = ledger_repo.get_connection()
        try:
            _seed_certain_expense(conn)
        finally:
            conn.close()

        resp = client.get("/api/receipts/review/feed")
        assert resp.json()["doubtful_count"] == 0

    def test_certain_item_includes_name_from_receipt_items(self, client, db):  # noqa: ARG002
        conn = ledger_repo.get_connection()
        try:
            _seed_certain_expense(conn)
            conn.execute(
                "INSERT INTO receipt_items"
                " (receipt_id, name_raw, name_normalized, total_price, quantity, unit_price, expense_id)"
                " VALUES (1, 'mleko raw', 'mleko', 200.0, 1, 200.0, 10)"
            )
        finally:
            conn.close()

        resp = client.get("/api/receipts/review/feed")
        certain = [i for i in resp.json()["items"] if not i["is_doubtful"]]
        assert len(certain) == 1
        assert certain[0]["name"] == "mleko"

    def test_certain_item_name_is_null_when_no_receipt_items(self, client, db):  # noqa: ARG002
        conn = ledger_repo.get_connection()
        try:
            _seed_certain_expense(conn)
        finally:
            conn.close()

        resp = client.get("/api/receipts/review/feed")
        certain = [i for i in resp.json()["items"] if not i["is_doubtful"]]
        assert len(certain) == 1
        assert certain[0]["name"] is None


@allure.epic("API")
@allure.feature("Receipt Review")
class TestReviewCounts:
    def test_counts_empty(self, client, db):  # noqa: ARG002
        resp = client.get("/api/receipts/review/counts")
        assert resp.status_code == 200
        assert resp.json()["doubtful_rules"] == 0

    def test_counts_with_doubtful(self, client, db):  # noqa: ARG002
        conn = ledger_repo.get_connection()
        try:
            _seed_review_data(conn)
        finally:
            conn.close()

        resp = client.get("/api/receipts/review/counts")
        assert resp.json()["doubtful_rules"] == 1

    def test_orphaned_rule_not_counted(self, client, db):  # noqa: ARG002
        """A rule with no matching receipt_items must not inflate the badge count."""
        conn = ledger_repo.get_connection()
        try:
            # Insert a rule with conf < 4 but NO receipt_items referencing it
            conn.execute("INSERT INTO stores (id, chain_name, pib) VALUES (1, 'Lidl', '100')")
            conn.execute(
                "INSERT INTO classification_rules"
                " (store_id, item_name_normalized, category_id, confidence_level, source)"
                " VALUES (1, 'orphan-item', 1, 2, 'llm')"
            )
        finally:
            conn.close()

        resp = client.get("/api/receipts/review/counts")
        assert resp.json()["doubtful_rules"] == 0


@allure.epic("API")
@allure.feature("Receipt Review")
class TestCategoryCorrection:
    def _seed_correction(self, conn):
        conn.execute("INSERT INTO stores (id, chain_name, pib) VALUES (1, 'Lidl', '100')")
        conn.execute(
            "INSERT INTO receipts (id, client_receipt_id, url, store_id)"
            " VALUES (1, 'r1', 'https://x', 1)"
        )
        conn.execute(
            "INSERT INTO receipt_items"
            " (id, receipt_id, name_raw, name_normalized, total_price, quantity, unit_price,"
            "  category_id, confidence_level)"
            " VALUES (1, 1, 'hleb raw', 'hleb', 120.0, 1, 120.0, 1, 3)"
        )
        conn.execute(
            "INSERT INTO expenses (id, datetime, amount, amount_original, currency_original,"
            "                      category_id, confidence_level, receipt_id, store_id)"
            " VALUES (1, '2026-05-01T10:00:00', 120.0, 120.0, 'RSD', 1, 3, 1, 1)"
        )
        conn.execute("UPDATE receipt_items SET expense_id = 1 WHERE id = 1")

    def test_correction_sets_conf4(self, client, db):  # noqa: ARG002
        conn = ledger_repo.get_connection()
        try:
            self._seed_correction(conn)
        finally:
            conn.close()

        resp = client.patch("/api/expenses/1/category", json={"category_id": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert data["corrected_expense_id"] == 1

        conn = ledger_repo.get_connection()
        try:
            exp = conn.execute(
                "SELECT category_id, confidence_level FROM expenses WHERE id = 1"
            ).fetchone()
            item = conn.execute(
                "SELECT category_id, confidence_level FROM receipt_items WHERE id = 1"
            ).fetchone()
        finally:
            conn.close()

        assert exp[0] == 2
        assert exp[1] == 4
        assert item[0] == 2
        assert item[1] == 4

    def test_correction_creates_rule(self, client, db):  # noqa: ARG002
        conn = ledger_repo.get_connection()
        try:
            self._seed_correction(conn)
        finally:
            conn.close()

        client.patch("/api/expenses/1/category", json={"category_id": 2})

        conn = ledger_repo.get_connection()
        try:
            rule = conn.execute(
                "SELECT category_id, confidence_level, source"
                " FROM classification_rules WHERE item_name_normalized = 'hleb'"
            ).fetchone()
        finally:
            conn.close()

        assert rule is not None
        assert rule[0] == 2
        assert rule[1] == 4
        assert rule[2] == "user_correction"

    def test_correction_unknown_expense_returns_404(self, client, db):  # noqa: ARG002
        resp = client.patch("/api/expenses/9999/category", json={"category_id": 1})
        assert resp.status_code == 404

    def test_correction_inactive_category_returns_422(self, client, db):  # noqa: ARG002
        conn = ledger_repo.get_connection()
        try:
            self._seed_correction(conn)
        finally:
            conn.close()

        resp = client.patch("/api/expenses/1/category", json={"category_id": 3})
        assert resp.status_code == 422

    def test_correction_on_non_receipt_expense(self, client, db):  # noqa: ARG002
        """Correcting a manual (non-receipt) expense updates category only — no rules, no items."""
        conn = ledger_repo.get_connection()
        try:
            conn.execute(
                "INSERT INTO expenses"
                " (datetime, amount, amount_original, currency_original, category_id, confidence_level)"
                " VALUES ('2026-05-01T10:00:00', 150.0, 150.0, 'RSD', 1, 3)"
            )
            expense_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        finally:
            conn.close()

        resp = client.patch(f"/api/expenses/{expense_id}/category", json={"category_id": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert data["corrected_expense_id"] == expense_id
        assert data["batch_updated_count"] == 0

        conn = ledger_repo.get_connection()
        try:
            exp = conn.execute(
                "SELECT category_id, confidence_level FROM expenses WHERE id = ?", [expense_id]
            ).fetchone()
            rule_count = conn.execute("SELECT COUNT(*) FROM classification_rules").fetchone()[0]
        finally:
            conn.close()

        assert exp[0] == 2
        assert exp[1] == 4
        assert rule_count == 0


@allure.epic("API")
@allure.feature("Receipt Review")
class TestBatchPropagation:
    def _seed(self, conn):
        conn.execute("INSERT INTO stores (id, chain_name, pib) VALUES (1, 'Lidl', '100')")
        conn.execute(
            "INSERT INTO receipts (id, client_receipt_id, url, store_id)"
            " VALUES (1, 'r1', 'https://x', 1)"
        )
        conn.execute(
            "INSERT INTO expenses (id, datetime, amount, amount_original, currency_original,"
            "                      category_id, confidence_level, receipt_id, store_id)"
            " VALUES (1, '2026-05-01T10:00:00', 100.0, 100.0, 'RSD', 1, 3, 1, 1)"
        )
        conn.execute(
            "INSERT INTO receipt_items"
            " (id, receipt_id, name_raw, name_normalized, total_price, quantity, unit_price,"
            "  category_id, confidence_level)"
            " VALUES (1, 1, 'hleb raw', 'hleb', 100.0, 1, 100.0, 1, 3)"
        )
        conn.execute("UPDATE receipt_items SET expense_id = 1 WHERE id = 1")

        conn.execute(
            "INSERT INTO receipts (id, client_receipt_id, url, store_id)"
            " VALUES (2, 'r2', 'https://y', 1)"
        )
        conn.execute(
            "INSERT INTO expenses (id, datetime, amount, amount_original, currency_original,"
            "                      category_id, confidence_level, receipt_id, store_id)"
            " VALUES (2, '2026-05-02T10:00:00', 80.0, 80.0, 'RSD', 1, 3, 2, 1)"
        )
        conn.execute(
            "INSERT INTO receipt_items"
            " (id, receipt_id, name_raw, name_normalized, total_price, quantity, unit_price,"
            "  category_id, confidence_level)"
            " VALUES (2, 2, 'hleb raw', 'hleb', 80.0, 1, 80.0, 1, 3)"
        )
        conn.execute("UPDATE receipt_items SET expense_id = 2 WHERE id = 2")

    def test_correcting_one_expense_updates_same_item_in_other_receipts(
        self,
        client,
        db,  # noqa: ARG002
    ):
        conn = ledger_repo.get_connection()
        try:
            self._seed(conn)
        finally:
            conn.close()

        resp = client.patch("/api/expenses/1/category", json={"category_id": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert data["batch_updated_count"] == 1

        conn = ledger_repo.get_connection()
        try:
            # Direct expense corrected
            exp1 = conn.execute(
                "SELECT category_id, confidence_level FROM expenses WHERE id = 1"
            ).fetchone()
            # Batch: expense2 had only "hleb" → all items moved → category updated
            exp2 = conn.execute(
                "SELECT category_id, confidence_level FROM expenses WHERE id = 2"
            ).fetchone()
            item2 = conn.execute(
                "SELECT category_id, confidence_level FROM receipt_items WHERE id = 2"
            ).fetchone()
        finally:
            conn.close()

        assert exp1[0] == 2
        assert exp2[0] == 2
        assert exp2[1] == 4
        assert item2[0] == 2
        assert item2[1] == 4

    def test_batch_creates_rule_for_corrected_item(
        self,
        client,
        db,  # noqa: ARG002
    ):
        conn = ledger_repo.get_connection()
        try:
            self._seed(conn)
        finally:
            conn.close()

        client.patch("/api/expenses/1/category", json={"category_id": 2})

        conn = ledger_repo.get_connection()
        try:
            rule = conn.execute(
                "SELECT category_id, confidence_level, source"
                " FROM classification_rules WHERE item_name_normalized = 'hleb'"
            ).fetchone()
        finally:
            conn.close()

        assert rule is not None
        assert rule[0] == 2
        assert rule[1] == 4
        assert rule[2] == "user_correction"


@allure.epic("API")
@allure.feature("Receipt Review")
class TestBatchPropagationNullStore:
    """Batch correction must use NULL-safe store matching (IS instead of =)."""

    def _seed(self, conn):
        # Receipt 1: store_id=NULL (unresolved)
        conn.execute(
            "INSERT INTO receipts (id, client_receipt_id, url) VALUES (10, 'null-r1', 'https://x')"
        )
        conn.execute(
            "INSERT INTO expenses (id, datetime, amount, amount_original, currency_original,"
            "                      category_id, confidence_level, receipt_id)"
            " VALUES (10, '2026-05-01T10:00:00', 100.0, 100.0, 'RSD', 1, 3, 10)"
        )
        conn.execute(
            "INSERT INTO receipt_items"
            " (id, receipt_id, name_raw, name_normalized, total_price, quantity, unit_price,"
            "  category_id, confidence_level)"
            " VALUES (10, 10, 'hleb raw', 'hleb', 100.0, 1, 100.0, 1, 3)"
        )
        conn.execute("UPDATE receipt_items SET expense_id = 10 WHERE id = 10")

        # Receipt 2: store_id=NULL (also unresolved) — should be batch-updated
        conn.execute(
            "INSERT INTO receipts (id, client_receipt_id, url) VALUES (11, 'null-r2', 'https://y')"
        )
        conn.execute(
            "INSERT INTO expenses (id, datetime, amount, amount_original, currency_original,"
            "                      category_id, confidence_level, receipt_id)"
            " VALUES (11, '2026-05-02T10:00:00', 80.0, 80.0, 'RSD', 1, 3, 11)"
        )
        conn.execute(
            "INSERT INTO receipt_items"
            " (id, receipt_id, name_raw, name_normalized, total_price, quantity, unit_price,"
            "  category_id, confidence_level)"
            " VALUES (11, 11, 'hleb raw', 'hleb', 80.0, 1, 80.0, 1, 3)"
        )
        conn.execute("UPDATE receipt_items SET expense_id = 11 WHERE id = 11")

        # Receipt 3: store_id=1 (known store) — must NOT be batch-updated
        conn.execute("INSERT INTO stores (id, chain_name, pib) VALUES (1, 'Lidl', '100')")
        conn.execute(
            "INSERT INTO receipts (id, client_receipt_id, url, store_id)"
            " VALUES (12, 'lidl-r3', 'https://z', 1)"
        )
        conn.execute(
            "INSERT INTO expenses (id, datetime, amount, amount_original, currency_original,"
            "                      category_id, confidence_level, receipt_id, store_id)"
            " VALUES (12, '2026-05-03T10:00:00', 60.0, 60.0, 'RSD', 1, 3, 12, 1)"
        )
        conn.execute(
            "INSERT INTO receipt_items"
            " (id, receipt_id, name_raw, name_normalized, total_price, quantity, unit_price,"
            "  category_id, confidence_level)"
            " VALUES (12, 12, 'hleb raw', 'hleb', 60.0, 1, 60.0, 1, 3)"
        )
        conn.execute("UPDATE receipt_items SET expense_id = 12 WHERE id = 12")

    def test_null_store_batch_does_not_spill_into_known_store_receipts(
        self,
        client,
        db,  # noqa: ARG002
    ):
        """Correcting an unresolved-store expense only propagates to other unresolved-store receipts."""
        conn = ledger_repo.get_connection()
        try:
            self._seed(conn)
        finally:
            conn.close()

        resp = client.patch("/api/expenses/10/category", json={"category_id": 2})
        assert resp.status_code == 200
        assert resp.json()["batch_updated_count"] == 1  # only the null-store receipt 11

        conn = ledger_repo.get_connection()
        try:
            exp11 = conn.execute("SELECT category_id FROM expenses WHERE id = 11").fetchone()
            exp12 = conn.execute("SELECT category_id FROM expenses WHERE id = 12").fetchone()
        finally:
            conn.close()

        assert exp11[0] == 2, "null-store receipt 11 must be batch-updated"
        assert exp12[0] == 1, "known-store Lidl receipt 12 must NOT be batch-updated"

    def test_null_store_batch_propagates_to_other_null_store_receipts(
        self,
        client,
        db,  # noqa: ARG002
    ):
        """Correcting an unresolved-store expense propagates to all null-store receipts."""
        conn = ledger_repo.get_connection()
        try:
            self._seed(conn)
        finally:
            conn.close()

        resp = client.patch("/api/expenses/10/category", json={"category_id": 2})
        assert resp.status_code == 200

        conn = ledger_repo.get_connection()
        try:
            item11 = conn.execute(
                "SELECT category_id, confidence_level FROM receipt_items WHERE id = 11"
            ).fetchone()
        finally:
            conn.close()

        assert item11[0] == 2
        assert item11[1] == 4


@allure.epic("API")
@allure.feature("Receipt Review")
class TestExpenseSplitMerge:
    def _seed(self, conn):
        conn.execute("INSERT INTO stores (id, chain_name, pib) VALUES (1, 'Lidl', '100')")
        # receipt1: two items ("hleb" + "mleko") → single expense
        conn.execute(
            "INSERT INTO receipts (id, client_receipt_id, url, store_id)"
            " VALUES (1, 'r1', 'https://x', 1)"
        )
        conn.execute(
            "INSERT INTO expenses (id, datetime, amount, amount_original, currency_original,"
            "                      category_id, confidence_level, receipt_id, store_id)"
            " VALUES (1, '2026-05-01T10:00:00', 150.0, 150.0, 'RSD', 1, 3, 1, 1)"
        )
        conn.execute(
            "INSERT INTO receipt_items"
            " (id, receipt_id, name_raw, name_normalized, total_price, quantity, unit_price,"
            "  category_id, confidence_level)"
            " VALUES (1, 1, 'hleb raw', 'hleb', 100.0, 1, 100.0, 1, 3)"
        )
        conn.execute("UPDATE receipt_items SET expense_id = 1 WHERE id = 1")
        conn.execute(
            "INSERT INTO receipt_items"
            " (id, receipt_id, name_raw, name_normalized, total_price, quantity, unit_price,"
            "  category_id, confidence_level)"
            " VALUES (2, 1, 'mleko raw', 'mleko', 50.0, 1, 50.0, 1, 3)"
        )
        conn.execute("UPDATE receipt_items SET expense_id = 1 WHERE id = 2")

        # receipt2: only "hleb" → separate expense
        conn.execute(
            "INSERT INTO receipts (id, client_receipt_id, url, store_id)"
            " VALUES (2, 'r2', 'https://y', 1)"
        )
        conn.execute(
            "INSERT INTO expenses (id, datetime, amount, amount_original, currency_original,"
            "                      category_id, confidence_level, receipt_id, store_id)"
            " VALUES (2, '2026-05-02T10:00:00', 80.0, 80.0, 'RSD', 1, 3, 2, 1)"
        )
        conn.execute(
            "INSERT INTO receipt_items"
            " (id, receipt_id, name_raw, name_normalized, total_price, quantity, unit_price,"
            "  category_id, confidence_level)"
            " VALUES (3, 2, 'hleb raw', 'hleb', 80.0, 1, 80.0, 1, 3)"
        )
        conn.execute("UPDATE receipt_items SET expense_id = 2 WHERE id = 3")

    def test_split_subtracts_moved_amount_from_source_expense(
        self,
        client,
        db,  # noqa: ARG002
    ):
        conn = ledger_repo.get_connection()
        try:
            self._seed(conn)
        finally:
            conn.close()

        # Correcting expense2 ("hleb" only) → cat2 triggers batch on expense1 ("hleb"+"mleko")
        resp = client.patch("/api/expenses/2/category", json={"category_id": 2})
        assert resp.status_code == 200
        assert resp.json()["batch_updated_count"] == 1

        conn = ledger_repo.get_connection()
        try:
            exp1 = conn.execute("SELECT amount, category_id FROM expenses WHERE id = 1").fetchone()
            # expense1 keeps only "mleko" (50.0); "hleb" (100.0) is split out
            assert exp1[0] == pytest.approx(50.0)
            assert exp1[1] == 1  # still cat1 (mleko)

            # A new expense must exist for the split-out "hleb" on receipt1
            new_exp = conn.execute(
                "SELECT id, amount, category_id FROM expenses"
                " WHERE receipt_id = 1 AND category_id = 2 AND id != 2"
            ).fetchone()
            assert new_exp is not None
            assert new_exp[1] == pytest.approx(100.0)

            # item1 ("hleb" in receipt1) must point to the new expense
            item1 = conn.execute(
                "SELECT expense_id, category_id, confidence_level FROM receipt_items WHERE id = 1"
            ).fetchone()
            assert item1[0] == new_exp[0]  # expense_id → new split expense
            assert item1[1] == 2
            assert item1[2] == 4

            # item2 ("mleko") is unchanged
            item2 = conn.execute(
                "SELECT category_id, confidence_level, expense_id FROM receipt_items WHERE id = 2"
            ).fetchone()
            assert item2[0] == 1
            assert item2[2] == 1
        finally:
            conn.close()

    def test_all_items_moving_reclassifies_expense_without_split(
        self,
        client,
        db,  # noqa: ARG002
    ):
        conn = ledger_repo.get_connection()
        try:
            self._seed(conn)
        finally:
            conn.close()

        # receipt1 has "hleb"(id=1) and "mleko"(id=2); correct "mleko"-only scenario:
        # For an all-items-moving test, we need an expense where ALL items are batch-matched.
        # Correct expense1 (hleb+mleko) to cat2 → no batch (expense2's "hleb" gets batch-updated)
        # → expense2 has only "hleb", so all items move → just update its category
        resp = client.patch("/api/expenses/1/category", json={"category_id": 2})
        assert resp.status_code == 200

        conn = ledger_repo.get_connection()
        try:
            exp2 = conn.execute("SELECT amount, category_id FROM expenses WHERE id = 2").fetchone()
            # expense2 had only "hleb"; all items moved → category updated in place
            assert exp2[1] == 2
            assert exp2[0] == pytest.approx(80.0)  # amount unchanged (no split)

            # No extra expense rows for receipt2
            extra = conn.execute("SELECT COUNT(*) FROM expenses WHERE receipt_id = 2").fetchone()[0]
            assert extra == 1
        finally:
            conn.close()

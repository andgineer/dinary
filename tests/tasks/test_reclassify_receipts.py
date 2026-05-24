"""Tests for the reclassify-receipts operator task (requeue_receipts)."""

import allure

from dinary.db.receipts import requeue_receipts
from dinary.db import storage


def _seed(conn):
    """Seed two receipts with classified items and expenses. Returns (r1_id, r2_id)."""
    conn.execute(
        "INSERT INTO category_groups (id, name, sort_order, is_active) VALUES (1, 'Еда', 1, 1)"
    )
    conn.execute(
        "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'продукты', 1, 1)"
    )
    conn.execute("INSERT OR IGNORE INTO shop_chains (name) VALUES ('Lidl')")
    chain_id_Lidl = conn.execute("SELECT id FROM shop_chains WHERE name='Lidl'").fetchone()[0]
    conn.execute(
        "INSERT INTO stores (name, chain_id, pib) VALUES ('Lidl', "
        + str(chain_id_Lidl)
        + ", '100')"
    )
    store_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO receipts (client_receipt_id, url, store_id, parsed_at)"
        " VALUES ('r1', 'https://x/1', ?, '2026-05-01T10:00:00')",
        [store_id],
    )
    r1 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO receipts (client_receipt_id, url, store_id, parsed_at)"
        " VALUES ('r2', 'https://x/2', ?, '2026-05-01T11:00:00')",
        [store_id],
    )
    r2 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # Expenses — category_id=1 is seeded by the migration fixtures
    conn.execute(
        "INSERT INTO expenses (datetime, amount, amount_original, currency_original,"
        "                      category_id, confidence_level, receipt_id, store_id)"
        " VALUES ('2026-05-01T10:00:00', 100, 100, 'RSD', 1, 4, ?, ?)",
        [r1, store_id],
    )
    e1 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO expenses (datetime, amount, amount_original, currency_original,"
        "                      category_id, confidence_level, receipt_id, store_id)"
        " VALUES ('2026-05-01T11:00:00', 200, 200, 'RSD', 1, 4, ?, ?)",
        [r2, store_id],
    )
    e2 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO receipt_items"
        " (receipt_id, name_raw, name_normalized, total_price, quantity, unit_price, expense_id)"
        " VALUES (?, 'hleb raw', 'hleb', 100, 1, 100, ?)",
        [r1, e1],
    )
    conn.execute(
        "INSERT INTO receipt_items"
        " (receipt_id, name_raw, name_normalized, total_price, quantity, unit_price, expense_id)"
        " VALUES (?, 'hleb raw', 'hleb', 200, 1, 200, ?)",
        [r2, e2],
    )
    conn.execute(
        "INSERT INTO classification_rules"
        " (store_id, item_name_normalized, category_id, confidence_level, source)"
        " VALUES (?, 'hleb', 1, 4, 'user_correction')",
        [store_id],
    )
    # Generic rule — should survive store-scoped clear
    conn.execute(
        "INSERT INTO classification_rules"
        " (store_id, item_name_normalized, category_id, confidence_level, source)"
        " VALUES (NULL, 'mleko', 1, 3, 'llm')"
    )
    return int(r1), int(r2)


@allure.epic("Tasks")
@allure.feature("reclassify-receipts")
class TestRequeuReceipts:
    def test_resets_classification_and_queues_job(self, db):  # noqa: ARG002
        conn = storage.get_connection()
        try:
            r1, _ = _seed(conn)
            conn.execute("BEGIN IMMEDIATE")
            requeue_receipts(conn, [r1])
            conn.execute("COMMIT")

            # Expenses for r1 deleted
            exp_count = conn.execute(
                "SELECT COUNT(*) FROM expenses WHERE receipt_id = ?", [r1]
            ).fetchone()[0]
            assert exp_count == 0

            # Items reset
            item = conn.execute(
                "SELECT expense_id FROM receipt_items WHERE receipt_id = ?",
                [r1],
            ).fetchone()
            assert item[0] is None

            # Job queued
            job = conn.execute(
                "SELECT status FROM receipt_classification_jobs WHERE receipt_id = ?",
                [r1],
            ).fetchone()
            assert job is not None
            assert job[0] == "pending"
        finally:
            conn.close()

    def test_idempotent_when_run_twice(self, db):  # noqa: ARG002
        conn = storage.get_connection()
        try:
            r1, _ = _seed(conn)
            conn.execute("BEGIN IMMEDIATE")
            requeue_receipts(conn, [r1])
            conn.execute("COMMIT")
            conn.execute("BEGIN IMMEDIATE")
            requeue_receipts(conn, [r1])
            conn.execute("COMMIT")

            job_count = conn.execute(
                "SELECT COUNT(*) FROM receipt_classification_jobs WHERE receipt_id = ?",
                [r1],
            ).fetchone()[0]
            assert job_count == 1
        finally:
            conn.close()

    def test_scoped_to_specified_receipts(self, db):  # noqa: ARG002
        conn = storage.get_connection()
        try:
            r1, r2 = _seed(conn)
            conn.execute("BEGIN IMMEDIATE")
            requeue_receipts(conn, [r1])
            conn.execute("COMMIT")

            # r2 expenses untouched
            exp2 = conn.execute(
                "SELECT COUNT(*) FROM expenses WHERE receipt_id = ?", [r2]
            ).fetchone()[0]
            assert exp2 == 1

            # r2 items untouched
            item2 = conn.execute(
                "SELECT expense_id FROM receipt_items WHERE receipt_id = ?", [r2]
            ).fetchone()
            assert item2[0] is not None
        finally:
            conn.close()

    def test_clear_rules_removes_store_scoped_and_generic_rules(self, db):  # noqa: ARG002
        conn = storage.get_connection()
        try:
            r1, _ = _seed(conn)
            lidl_id = conn.execute("SELECT store_id FROM receipts WHERE id = ?", [r1]).fetchone()[0]
            # Add a DIFFERENT store and a hleb rule for it — should survive
            conn.execute("INSERT OR IGNORE INTO shop_chains (name) VALUES ('Maxi')")
            chain_id_Maxi = conn.execute("SELECT id FROM shop_chains WHERE name='Maxi'").fetchone()[
                0
            ]
            conn.execute(
                "INSERT INTO stores (name, chain_id, pib) VALUES ('Maxi', "
                + str(chain_id_Maxi)
                + ", '200')"
            )
            maxi_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO classification_rules"
                " (store_id, item_name_normalized, category_id, confidence_level, source)"
                " VALUES (?, 'hleb', 1, 3, 'llm')",
                [maxi_id],
            )
            conn.execute("BEGIN IMMEDIATE")
            requeue_receipts(conn, [r1], clear_rules=True)
            conn.execute("COMMIT")

            # Lidl-scoped hleb rule deleted
            lidl_rule = conn.execute(
                "SELECT id FROM classification_rules"
                " WHERE store_id = ? AND item_name_normalized = 'hleb'",
                [lidl_id],
            ).fetchone()
            assert lidl_rule is None

            # Maxi-scoped hleb rule survives (different store)
            maxi_rule = conn.execute(
                "SELECT id FROM classification_rules"
                " WHERE store_id = ? AND item_name_normalized = 'hleb'",
                [maxi_id],
            ).fetchone()
            assert maxi_rule is not None

            # mleko generic rule survives (item not in r1)
            mleko_rule = conn.execute(
                "SELECT id FROM classification_rules WHERE item_name_normalized = 'mleko'"
            ).fetchone()
            assert mleko_rule is not None
        finally:
            conn.close()

    def test_clear_rules_removes_generic_rule_for_item_in_receipt(self, db):  # noqa: ARG002
        """clear_rules=True must also delete generic (store_id IS NULL) rules for items in the target receipts."""
        conn = storage.get_connection()
        try:
            r1, _ = _seed(conn)
            # Add a generic "hleb" rule (same item that IS in r1)
            conn.execute(
                "INSERT INTO classification_rules"
                " (store_id, item_name_normalized, category_id, confidence_level, source)"
                " VALUES (NULL, 'hleb', 1, 3, 'llm')"
            )
            conn.execute("BEGIN IMMEDIATE")
            requeue_receipts(conn, [r1], clear_rules=True)
            conn.execute("COMMIT")

            # Generic hleb rule must be deleted (hleb is in r1)
            generic_hleb = conn.execute(
                "SELECT id FROM classification_rules"
                " WHERE store_id IS NULL AND item_name_normalized = 'hleb'"
            ).fetchone()
            assert generic_hleb is None, "generic hleb rule must be deleted when r1 is cleared"

            # Generic mleko rule must survive (mleko is NOT in r1)
            generic_mleko = conn.execute(
                "SELECT id FROM classification_rules WHERE item_name_normalized = 'mleko'"
            ).fetchone()
            assert generic_mleko is not None
        finally:
            conn.close()

    def test_empty_ids_is_noop(self, db):  # noqa: ARG002
        conn = storage.get_connection()
        try:
            _seed(conn)
            conn.execute("BEGIN IMMEDIATE")
            requeue_receipts(conn, [])
            conn.execute("COMMIT")
            # Nothing changed
            exp_count = conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
            assert exp_count == 2
        finally:
            conn.close()

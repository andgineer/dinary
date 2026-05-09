"""Tests for the receipt classification drain cycle."""

import asyncio
import shutil
import sqlite3
import unittest.mock
from unittest.mock import AsyncMock, MagicMock

import allure
import pytest

from dinary.background.receipt_classification_task import _classify_and_persist
from dinary.services import db_migrations, ledger_repo
from dinary.services.llm_client import AllProvidersExhausted, ClassificationResult, ProviderPool
from dinary.services.receipt_repo import (
    ReceiptJobRow,
    claim_next_job,
    get_receipt_items,
    insert_job,
)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    dst = tmp_path / "dinary.db"
    blank_src = tmp_path / "blank.db"

    def _migration_connect(self, dburi):
        con = sqlite3.connect(str(self.uri.database), isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=5000")
        return con

    with unittest.mock.patch.object(db_migrations.SQLiteBackend, "connect", _migration_connect):
        db_migrations.migrate_db(blank_src)

    shutil.copy(blank_src, dst)
    monkeypatch.setattr(ledger_repo, "DB_PATH", dst)
    monkeypatch.setattr(ledger_repo, "DATA_DIR", tmp_path)

    c = ledger_repo.get_connection()
    yield c
    c.close()


def _seed_catalog(conn):
    conn.execute(
        "INSERT INTO category_groups (id, name, sort_order, is_active) VALUES (1, 'Еда', 1, 1)"
    )
    conn.execute(
        "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'продукты', 1, 1)"
    )
    conn.execute("INSERT INTO categories (id, name, group_id, is_active) VALUES (2, 'хоз', 1, 1)")


def _seed_receipt(conn, name_raw="hleb"):
    conn.execute(
        "INSERT INTO receipts (client_receipt_id, url, parsed_at)"
        " VALUES ('r1', 'https://x', '2026-05-01T10:00:00')"
    )
    receipt_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.execute(
        "INSERT INTO receipt_items (receipt_id, name_raw, total_price, quantity, unit_price)"
        " VALUES (?, ?, 120.0, 1, 120.0)",
        [receipt_id, name_raw],
    )
    insert_job(conn, receipt_id)
    return receipt_id


def _make_job(receipt_id: int, claim_token: str = "tok") -> ReceiptJobRow:
    return ReceiptJobRow(
        receipt_id=receipt_id,
        url="https://x",
        store_name_raw="",
        store_pib_raw="",
        invoice_number="INV-1",
        parsed_at="2026-05-01T10:00:00",
        used_journal_fallback=False,
        claim_token=claim_token,
    )


def _mock_pool(cat_id=1, conf=3, exhausted=False):
    pool = MagicMock(spec=ProviderPool)
    if exhausted:
        pool.classify_receipt = AsyncMock(side_effect=AllProvidersExhausted)
    else:
        pool.classify_receipt = AsyncMock(
            return_value=(
                [
                    ClassificationResult(
                        item_name_normalized="hleb", category_id=cat_id, confidence_level=conf
                    )
                ],
                False,
            )
        )
    return pool


def _categories(conn):
    rows = conn.execute(
        "SELECT c.id, cg.name, c.name FROM categories c"
        " LEFT JOIN category_groups cg ON cg.id = c.group_id WHERE c.is_active = 1"
    ).fetchall()
    return {int(r[0]): f"{r[1]}: {r[2]}" if r[1] else str(r[2]) for r in rows}


@allure.epic("Services")
@allure.feature("Receipt classification drain")
class TestClassifyAndPersist:
    def test_rule_hit_creates_expense_without_llm(self, conn):
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)
        conn.execute(
            "INSERT INTO classification_rules"
            " (item_name_normalized, category_id, confidence_level, source)"
            " VALUES ('hleb', 1, 4, 'llm')"
        )
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)
        pool = _mock_pool()

        asyncio.run(_classify_and_persist(conn, pool, job, items, None))

        pool.classify_receipt.assert_not_called()
        exp = conn.execute(
            "SELECT amount, category_id, confidence_level FROM expenses WHERE receipt_id = ?",
            [receipt_id],
        ).fetchone()
        assert exp is not None
        assert exp[0] == 120.0
        assert exp[1] == 1
        assert exp[2] == 4

    def test_llm_result_creates_expense(self, conn):
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)

        asyncio.run(
            _classify_and_persist(conn, pool := _mock_pool(cat_id=1, conf=3), job, items, None)
        )  # noqa: E731

        pool.classify_receipt.assert_called_once()
        exp = conn.execute(
            "SELECT amount, category_id, confidence_level FROM expenses WHERE receipt_id = ?",
            [receipt_id],
        ).fetchone()
        assert exp is not None
        assert exp[0] == 120.0
        assert exp[1] == 1
        assert exp[2] == 3

    def test_level1_item_skipped_no_expense(self, conn):
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)
        pool = _mock_pool(cat_id=None, conf=1)
        pool.classify_receipt = AsyncMock(
            return_value=(
                [
                    ClassificationResult(
                        item_name_normalized="hleb", category_id=None, confidence_level=1
                    )
                ],
                False,
            )
        )

        asyncio.run(_classify_and_persist(conn, pool, job, items, None))

        exp_count = conn.execute(
            "SELECT COUNT(*) FROM expenses WHERE receipt_id = ?", [receipt_id]
        ).fetchone()[0]
        assert exp_count == 0

    def test_complete_job_inside_transaction(self, conn):
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)

        asyncio.run(_classify_and_persist(conn, _mock_pool(), job, items, None))

        # Job must be deleted (complete_job was called inside the transaction)
        job_row = conn.execute(
            "SELECT status FROM receipt_classification_jobs WHERE receipt_id = ?",
            [receipt_id],
        ).fetchone()
        assert job_row is None

    def test_idempotency_guard_skips_on_existing_expenses(self, conn):
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)

        # Pre-insert an expense (simulating a prior completed run)
        conn.execute(
            "INSERT INTO expenses"
            " (datetime, amount, amount_original, currency_original, category_id, confidence_level, receipt_id)"
            " VALUES ('2026-05-01T10:00:00', 120.0, 120.0, 'RSD', 1, 3, ?)",
            [receipt_id],
        )

        pool = _mock_pool()
        job = _make_job(receipt_id)
        items = get_receipt_items(conn, receipt_id)

        asyncio.run(_classify_and_persist(conn, pool, job, items, None))

        pool.classify_receipt.assert_not_called()
        exp_count = conn.execute(
            "SELECT COUNT(*) FROM expenses WHERE receipt_id = ?", [receipt_id]
        ).fetchone()[0]
        assert exp_count == 1

    def test_journal_penalty_reduces_confidence(self, conn):
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)
        conn.execute("UPDATE receipts SET used_journal_fallback = 1 WHERE id = ?", [receipt_id])
        job = ReceiptJobRow(
            receipt_id=receipt_id,
            url="https://x",
            store_name_raw="",
            store_pib_raw="",
            invoice_number="INV-1",
            parsed_at="2026-05-01",
            used_journal_fallback=True,
            claim_token="tok",
        )
        conn.execute(
            "UPDATE receipt_classification_jobs SET status='in_progress', claim_token='tok'"
            " WHERE receipt_id = ?",
            [receipt_id],
        )
        items = get_receipt_items(conn, receipt_id)

        asyncio.run(_classify_and_persist(conn, _mock_pool(conf=3), job, items, None))

        exp = conn.execute(
            "SELECT confidence_level FROM expenses WHERE receipt_id = ?", [receipt_id]
        ).fetchone()
        assert exp[0] == 2  # 3 - 1 journal penalty

    def test_llm_rule_created_for_high_confidence(self, conn):
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)

        asyncio.run(_classify_and_persist(conn, _mock_pool(conf=3), job, items, None))

        rule = conn.execute(
            "SELECT category_id, confidence_level, source FROM classification_rules"
            " WHERE item_name_normalized = 'hleb'"
        ).fetchone()
        assert rule is not None
        assert rule[0] == 1
        assert rule[1] == 3
        assert rule[2] == "llm"

    def test_conf1_rule_hit_still_calls_llm(self, conn):
        """A stored rule with conf=1 is treated as a miss so the LLM can reclassify."""
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)
        conn.execute(
            "INSERT INTO classification_rules"
            " (item_name_normalized, category_id, confidence_level, source)"
            " VALUES ('hleb', 1, 1, 'llm')"
        )
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)
        pool = _mock_pool(cat_id=1, conf=3)

        asyncio.run(_classify_and_persist(conn, pool, job, items, None))

        pool.classify_receipt.assert_called_once()
        exp = conn.execute(
            "SELECT confidence_level FROM expenses WHERE receipt_id = ?", [receipt_id]
        ).fetchone()
        assert exp is not None
        assert exp[0] == 3  # LLM result used, not stale conf=1 rule

    def test_no_rule_created_for_conf1(self, conn):
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)
        conn.execute(
            "INSERT INTO classification_rules"
            " (item_name_normalized, category_id, confidence_level, source)"
            " VALUES ('hleb', 1, 1, 'llm')"
        )
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)

        pool = MagicMock(spec=ProviderPool)
        pool.classify_receipt = AsyncMock(
            return_value=(
                [
                    ClassificationResult(
                        item_name_normalized="hleb", category_id=1, confidence_level=1
                    )
                ],
                False,
            )
        )

        asyncio.run(_classify_and_persist(conn, pool, job, items, None))

        # conf=1 → item skipped, no new expense
        exp_count = conn.execute(
            "SELECT COUNT(*) FROM expenses WHERE receipt_id = ?", [receipt_id]
        ).fetchone()[0]
        assert exp_count == 0

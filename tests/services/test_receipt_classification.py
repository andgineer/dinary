"""Tests for the receipt classification drain cycle."""

import asyncio
import shutil
import sqlite3
import unittest.mock
from unittest.mock import patch

import allure
import pytest

from dinary.adapters.llm_client import ClassificationResult
from dinary.adapters.llmbroker import LLMBroker, NullStorage
from dinary.background.classification.task import _classify_and_persist
from dinary.db import db_migrations, storage
from dinary.db.receipts import (
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
    monkeypatch.setattr(storage, "DB_PATH", dst)
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)

    c = storage.get_connection()
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


def _broker() -> LLMBroker:
    return LLMBroker(NullStorage())


def _classify_patch(results: list[ClassificationResult], used_fallback: bool = False):
    return patch(
        "dinary.background.classification.task.classify_receipt",
        return_value=(results, used_fallback),
    )


def _hleb_result(cat_id=1, conf=3) -> ClassificationResult:
    return ClassificationResult(
        item_name_normalized="hleb", category_id=cat_id, confidence_level=conf
    )


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
        conn.close()

        with _classify_patch([_hleb_result(conf=4)]) as mock_classify:
            asyncio.run(_classify_and_persist(_broker(), job, items, None))
            mock_classify.assert_not_called()

        conn2 = storage.get_connection()
        try:
            exp = conn2.execute(
                "SELECT amount, category_id, confidence_level FROM expenses WHERE receipt_id = ?",
                [receipt_id],
            ).fetchone()
        finally:
            conn2.close()

        assert exp is not None
        assert exp[0] == 120.0
        assert exp[1] == 1
        assert exp[2] == 4

    def test_llm_result_creates_expense(self, conn):
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)
        conn.close()

        with _classify_patch([_hleb_result(cat_id=1, conf=3)]) as mock_classify:
            asyncio.run(_classify_and_persist(_broker(), job, items, None))
            mock_classify.assert_called_once()

        conn2 = storage.get_connection()
        try:
            exp = conn2.execute(
                "SELECT amount, category_id, confidence_level FROM expenses WHERE receipt_id = ?",
                [receipt_id],
            ).fetchone()
        finally:
            conn2.close()

        assert exp is not None
        assert exp[0] == 120.0
        assert exp[1] == 1
        assert exp[2] == 3

    def test_level1_item_skipped_no_expense(self, conn):
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)
        conn.close()

        with _classify_patch(
            [
                ClassificationResult(
                    item_name_normalized="hleb", category_id=None, confidence_level=1
                )
            ]
        ):
            asyncio.run(_classify_and_persist(_broker(), job, items, None))

        conn2 = storage.get_connection()
        try:
            exp_count = conn2.execute(
                "SELECT COUNT(*) FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()[0]
        finally:
            conn2.close()

        assert exp_count == 0

    def test_complete_job_inside_transaction(self, conn):
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)
        conn.close()

        with _classify_patch([_hleb_result()]):
            asyncio.run(_classify_and_persist(_broker(), job, items, None))

        conn2 = storage.get_connection()
        try:
            job_row = conn2.execute(
                "SELECT status FROM receipt_classification_jobs WHERE receipt_id = ?",
                [receipt_id],
            ).fetchone()
        finally:
            conn2.close()

        assert job_row is None

    def test_idempotency_guard_skips_on_existing_expenses(self, conn):
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)

        conn.execute(
            "INSERT INTO expenses"
            " (datetime, amount, amount_original, currency_original, category_id, confidence_level, receipt_id)"
            " VALUES ('2026-05-01T10:00:00', 120.0, 120.0, 'RSD', 1, 3, ?)",
            [receipt_id],
        )

        job = _make_job(receipt_id)
        items = get_receipt_items(conn, receipt_id)
        conn.close()

        with _classify_patch([_hleb_result()]) as mock_classify:
            asyncio.run(_classify_and_persist(_broker(), job, items, None))
            mock_classify.assert_not_called()

        conn2 = storage.get_connection()
        try:
            exp_count = conn2.execute(
                "SELECT COUNT(*) FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()[0]
        finally:
            conn2.close()

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
        conn.close()

        with _classify_patch([_hleb_result(conf=3)]):
            asyncio.run(_classify_and_persist(_broker(), job, items, None))

        conn2 = storage.get_connection()
        try:
            exp = conn2.execute(
                "SELECT confidence_level FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()
        finally:
            conn2.close()

        assert exp[0] == 2  # 3 - 1 journal penalty

    def test_llm_rule_created_for_high_confidence(self, conn):
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)
        conn.close()

        with _classify_patch([_hleb_result(conf=3)]):
            asyncio.run(_classify_and_persist(_broker(), job, items, None))

        conn2 = storage.get_connection()
        try:
            rule = conn2.execute(
                "SELECT category_id, confidence_level, source FROM classification_rules"
                " WHERE item_name_normalized = 'hleb'"
            ).fetchone()
        finally:
            conn2.close()

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
        conn.close()

        with _classify_patch([_hleb_result(cat_id=1, conf=3)]) as mock_classify:
            asyncio.run(_classify_and_persist(_broker(), job, items, None))
            mock_classify.assert_called_once()

        conn2 = storage.get_connection()
        try:
            exp = conn2.execute(
                "SELECT confidence_level FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()
        finally:
            conn2.close()

        assert exp is not None
        assert exp[0] == 3

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
        conn.close()

        with _classify_patch(
            [ClassificationResult(item_name_normalized="hleb", category_id=1, confidence_level=1)]
        ):
            asyncio.run(_classify_and_persist(_broker(), job, items, None))

        conn2 = storage.get_connection()
        try:
            exp_count = conn2.execute(
                "SELECT COUNT(*) FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()[0]
        finally:
            conn2.close()

        assert exp_count == 0

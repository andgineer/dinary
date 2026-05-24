"""Tests for the receipt classification drain loop."""

import asyncio
import contextlib
import json
import logging
import shutil
import sqlite3
import time
import unittest.mock
from unittest.mock import patch

import allure
import pytest

import dinary.background.classification.task as drain_mod
from dinary.background.classification.receipt_classifier import ClassificationResult
from conftest import NullStorage
from dinary.adapters.llmbroker import LLMBroker
from dinary.adapters.serbian_receipt_parser import ParsedReceipt, ReceiptItem
from dinary.background.classification.task import (
    _drain_all_pending,
    _process_job,
    _save_parsed,
    notify_new_receipt,
    receipt_classification_task,
)
from dinary.config import settings
from dinary.db import db_migrations, storage
from dinary.db.receipts import ReceiptJobRow


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fixtures for process-job integration tests
# ---------------------------------------------------------------------------


def _migration_connect(self, dburi):
    con = sqlite3.connect(str(self.uri.database), isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    return con


@pytest.fixture
def drain_db(tmp_path, monkeypatch):
    blank = tmp_path / "blank.db"
    with unittest.mock.patch.object(db_migrations.SQLiteBackend, "connect", _migration_connect):
        db_migrations.migrate_db(blank)
    dst = tmp_path / "dinary.db"
    shutil.copy(blank, dst)
    monkeypatch.setattr(storage, "DB_PATH", dst)
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "accounting_currency", "RSD")
    yield dst


def _seed_drain_db(conn):
    """Insert the minimum catalog rows required by the drain."""
    conn.execute(
        "INSERT INTO category_groups (id, name, sort_order, is_active) VALUES (1, 'Еда', 1, 1)"
    )
    conn.execute(
        "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'продукты', 1, 1)"
    )


# ---------------------------------------------------------------------------
# Process-job integration tests
# ---------------------------------------------------------------------------


@allure.epic("Background Tasks")
@allure.feature("Receipt drain — process job")
class TestProcessJobEdgeCases:
    def test_parsed_with_no_items_logs_warning_and_completes_job(
        self,
        drain_db,
        caplog,  # noqa: ARG002
    ):
        """Drain logs a warning and removes the job when parsed but receipt_items is empty."""
        conn = storage.get_connection()
        try:
            _seed_drain_db(conn)
            conn.execute(
                "INSERT INTO receipts (client_receipt_id, url, parsed_at)"
                " VALUES ('no-items', 'https://x', '2026-05-01 10:00:00')"
            )
            receipt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO receipt_classification_jobs (receipt_id) VALUES (?)", [receipt_id]
            )
        finally:
            conn.close()

        job = ReceiptJobRow(
            receipt_id=receipt_id,
            url="https://x",
            store_name_raw="",
            store_pib_raw="",
            invoice_number="",
            parsed_at="2026-05-01 10:00:00",
            used_journal_fallback=False,
            claim_token="tok",
        )
        with caplog.at_level(logging.WARNING, logger="dinary.background.classification.task"):
            asyncio.run(_process_job(job, _make_broker()))

        assert any("no items" in r.message.lower() for r in caplog.records)

        conn = storage.get_connection()
        try:
            remaining = conn.execute(
                "SELECT status FROM receipt_classification_jobs WHERE receipt_id = ?",
                [receipt_id],
            ).fetchone()
            exp_count = conn.execute(
                "SELECT COUNT(*) FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()[0]
        finally:
            conn.close()

        assert remaining is None, "job should be deleted after no-items completion"
        assert exp_count == 0

    def test_failover_penalty_reduces_confidence(self, drain_db):  # noqa: ARG002
        """When the pool returns used_failover=True, expense confidence is reduced by 1."""
        parsed = ParsedReceipt(
            store_name="Lidl",
            store_pib="100",
            total_amount=100.0,
            invoice_number="INV-1",
            items=[
                ReceiptItem(
                    name_raw="HLEB",
                    unit_price=100.0,
                    quantity=1.0,
                    total_price=100.0,
                    tax_label="E",
                )
            ],
            items_total=100.0,
            total_ok=True,
            used_journal_fallback=False,
        )
        conn = storage.get_connection()
        try:
            _seed_drain_db(conn)
            conn.execute("INSERT OR IGNORE INTO shop_chains (name) VALUES ('Lidl')")
            chain_id_Lidl = conn.execute("SELECT id FROM shop_chains WHERE name='Lidl'").fetchone()[
                0
            ]
            conn.execute(
                "INSERT INTO stores (name, chain_id, pib) VALUES ('Lidl', "
                + str(chain_id_Lidl)
                + ", '100')"
            )
            conn.execute(
                "INSERT INTO receipts (client_receipt_id, url) VALUES ('fp-r1', 'https://x')"
            )
            receipt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO receipt_classification_jobs (receipt_id) VALUES (?)", [receipt_id]
            )
        finally:
            conn.close()

        job = ReceiptJobRow(
            receipt_id=receipt_id,
            url="https://x",
            store_name_raw="",
            store_pib_raw="",
            invoice_number="INV-1",
            parsed_at=None,
            used_journal_fallback=False,
            claim_token="tok",
        )

        with (
            patch(
                "dinary.background.classification.task.parse_receipt",
                return_value=parsed,
            ),
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=(
                    [
                        ClassificationResult(
                            item_name_normalized="hleb", category_id=1, confidence_level=3
                        )
                    ],
                    True,  # used_failover=True → penalty −1 → final conf = 2
                ),
            ),
        ):
            asyncio.run(_process_job(job, _make_broker()))

        conn = storage.get_connection()
        try:
            exp = conn.execute(
                "SELECT confidence_level FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()
        finally:
            conn.close()

        assert exp is not None
        assert exp[0] == 2, f"expected conf=2 (3 - 1 failover penalty), got {exp[0]}"

    def test_expense_datetime_matches_receipt_created_at(self, drain_db):  # noqa: ARG002
        """Expenses use the receipt's created_at as their datetime, not classification time."""
        parsed = ParsedReceipt(
            store_name="",
            store_pib="",
            total_amount=50.0,
            invoice_number="INV-2",
            items=[
                ReceiptItem(
                    name_raw="MLEKO", unit_price=50.0, quantity=1.0, total_price=50.0, tax_label="E"
                )
            ],
            items_total=50.0,
            total_ok=True,
            used_journal_fallback=False,
        )
        fixed_created_at = "2026-01-15 08:30:00+00:00"
        conn = storage.get_connection()
        try:
            _seed_drain_db(conn)
            conn.execute(
                "INSERT INTO receipts (client_receipt_id, url, created_at)"
                " VALUES ('dt-r1', 'https://x', ?)",
                [fixed_created_at],
            )
            receipt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO receipt_classification_jobs (receipt_id) VALUES (?)", [receipt_id]
            )
        finally:
            conn.close()

        job = ReceiptJobRow(
            receipt_id=receipt_id,
            url="https://x",
            store_name_raw="",
            store_pib_raw="",
            invoice_number="INV-2",
            parsed_at=None,
            used_journal_fallback=False,
            claim_token="tok",
        )

        with (
            patch(
                "dinary.background.classification.task.parse_receipt",
                return_value=parsed,
            ),
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=(
                    [
                        ClassificationResult(
                            item_name_normalized="mleko", category_id=1, confidence_level=4
                        )
                    ],
                    False,
                ),
            ),
        ):
            asyncio.run(_process_job(job, _make_broker()))

        conn = storage.get_connection()
        try:
            exp = conn.execute(
                "SELECT datetime FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()
        finally:
            conn.close()

        assert exp is not None
        assert str(exp[0]).startswith("2026-01-15 09:30")

    def test_fallback_metadata_cleared_on_successful_parse(self, drain_db):  # noqa: ARG002
        """_save_parsed clears fallback metadata when /specifications succeeds."""
        conn = storage.get_connection()
        try:
            conn.execute(
                "INSERT INTO receipts (client_receipt_id, url) VALUES ('fb-r1', 'https://x')"
            )
            receipt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO app_metadata (key, value)"
                " VALUES ('receipt_fetch_fallback_last', '2026-05-01 | invoice: X | reason: timeout')"
            )
            conn.execute(
                "INSERT INTO app_metadata (key, value) VALUES ('receipt_fetch_fallback_count', '3')"
            )
        finally:
            conn.close()

        parsed = ParsedReceipt(
            store_name="Maxi",
            store_pib="200",
            total_amount=80.0,
            invoice_number="INV-OK",
            items=[
                ReceiptItem(
                    name_raw="HLEB", unit_price=80.0, quantity=1.0, total_price=80.0, tax_label="E"
                )
            ],
            items_total=80.0,
            total_ok=True,
            used_journal_fallback=False,
        )
        _save_parsed(receipt_id, parsed)

        conn = storage.get_connection()
        try:
            last = conn.execute(
                "SELECT value FROM app_metadata WHERE key = 'receipt_fetch_fallback_last'"
            ).fetchone()
            count = conn.execute(
                "SELECT value FROM app_metadata WHERE key = 'receipt_fetch_fallback_count'"
            ).fetchone()
        finally:
            conn.close()

        assert last is None, "fallback_last should be cleared after successful parse"
        assert count is not None and count[0] == "3", (
            "fallback_count persists as a cumulative audit counter"
        )

    def test_expense_datetime_uses_purchase_datetime_when_set(self, drain_db):  # noqa: ARG002
        """Expenses use purchase_datetime from the receipt when present, not created_at."""
        purchase_dt = "2026-01-10T09:15:00+01:00"
        parsed = ParsedReceipt(
            store_name="",
            store_pib="",
            total_amount=60.0,
            invoice_number="INV-PD",
            items=[
                ReceiptItem(
                    name_raw="KAFA", unit_price=60.0, quantity=1.0, total_price=60.0, tax_label="E"
                )
            ],
            items_total=60.0,
            total_ok=True,
            used_journal_fallback=False,
            purchase_datetime=purchase_dt,
        )
        conn = storage.get_connection()
        try:
            _seed_drain_db(conn)
            conn.execute(
                "INSERT INTO receipts (client_receipt_id, url, created_at)"
                " VALUES ('pd-r1', 'https://x', '2026-05-01 12:00:00')"
            )
            receipt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO receipt_classification_jobs (receipt_id) VALUES (?)", [receipt_id]
            )
        finally:
            conn.close()

        job = ReceiptJobRow(
            receipt_id=receipt_id,
            url="https://x",
            store_name_raw="",
            store_pib_raw="",
            invoice_number="INV-PD",
            parsed_at=None,
            used_journal_fallback=False,
            claim_token="tok",
        )

        with (
            patch(
                "dinary.background.classification.task.parse_receipt",
                return_value=parsed,
            ),
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=(
                    [
                        ClassificationResult(
                            item_name_normalized="kafa", category_id=1, confidence_level=4
                        )
                    ],
                    False,
                ),
            ),
        ):
            asyncio.run(_process_job(job, _make_broker()))

        conn = storage.get_connection()
        try:
            exp_dt = conn.execute(
                "SELECT datetime FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()
        finally:
            conn.close()

        assert exp_dt is not None
        # Expense datetime must reflect the purchase time, not the submission time
        assert str(exp_dt[0]).startswith("2026-01-10"), (
            f"expected purchase date 2026-01-10, got {exp_dt[0]}"
        )

    def test_conf1_items_do_not_create_rules(self, drain_db):  # noqa: ARG002
        """Items penalised to conf=1 do not store a rule; the LLM is called again next pass."""
        parsed = ParsedReceipt(
            store_name="Lidl",
            store_pib="100",
            total_amount=50.0,
            invoice_number="INV-C1",
            items=[
                ReceiptItem(
                    name_raw="NEPOZNATO",
                    unit_price=50.0,
                    quantity=1.0,
                    total_price=50.0,
                    tax_label="E",
                )
            ],
            items_total=50.0,
            total_ok=True,
            used_journal_fallback=True,  # journal penalty: −1
        )
        conn = storage.get_connection()
        try:
            _seed_drain_db(conn)
            conn.execute("INSERT OR IGNORE INTO shop_chains (name) VALUES ('Lidl')")
            chain_id_Lidl = conn.execute("SELECT id FROM shop_chains WHERE name='Lidl'").fetchone()[
                0
            ]
            conn.execute(
                "INSERT INTO stores (name, chain_id, pib) VALUES ('Lidl', "
                + str(chain_id_Lidl)
                + ", '100')"
            )
            conn.execute(
                "INSERT INTO receipts (client_receipt_id, url) VALUES ('c1-r1', 'https://x')"
            )
            receipt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO receipt_classification_jobs (receipt_id) VALUES (?)", [receipt_id]
            )
        finally:
            conn.close()

        job = ReceiptJobRow(
            receipt_id=receipt_id,
            url="https://x",
            store_name_raw="Lidl",
            store_pib_raw="100",
            invoice_number="INV-C1",
            parsed_at=None,
            used_journal_fallback=True,
            claim_token="tok",
        )

        # LLM returns cat=1 conf=2; journal penalty (−1) → final conf=1
        with (
            patch("dinary.background.classification.task.parse_receipt", return_value=parsed),
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=(
                    [
                        ClassificationResult(
                            item_name_normalized="nepoznato", category_id=1, confidence_level=2
                        )
                    ],
                    False,
                ),
            ),
        ):
            asyncio.run(_process_job(job, _make_broker()))

        conn = storage.get_connection()
        try:
            exp_count = conn.execute(
                "SELECT COUNT(*) FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()[0]
            rule = conn.execute(
                "SELECT category_id, confidence_level FROM classification_rules"
                " WHERE item_name_normalized = 'nepoznato'"
            ).fetchone()
        finally:
            conn.close()

        assert exp_count == 0, "conf=1 items must not generate expenses"
        assert rule is None, "conf=1 items must not store a rule (plan step 9: confidence 2-4 only)"


# ---------------------------------------------------------------------------
# Sheet logging integration
# ---------------------------------------------------------------------------


@allure.epic("Background Tasks")
@allure.feature("Receipt drain — sheet logging")
class TestReceiptSheetLogging:
    """Expenses created by the receipt drain must carry a UUID and be enqueued
    for sheet logging so the drain loop can append them to Google Sheets."""

    def test_expense_has_client_expense_id(self, drain_db):  # noqa: ARG002
        """Each receipt expense gets a non-null UUID client_expense_id."""
        parsed = ParsedReceipt(
            store_name="",
            store_pib="",
            total_amount=100.0,
            invoice_number="INV-LOG-1",
            items=[
                ReceiptItem(
                    name_raw="HLEB",
                    unit_price=100.0,
                    quantity=1.0,
                    total_price=100.0,
                    tax_label="E",
                )
            ],
            items_total=100.0,
            total_ok=True,
            used_journal_fallback=False,
        )
        conn = storage.get_connection()
        try:
            _seed_drain_db(conn)
            conn.execute(
                "INSERT INTO receipts (client_receipt_id, url) VALUES ('log-r1', 'https://x')"
            )
            receipt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO receipt_classification_jobs (receipt_id) VALUES (?)", [receipt_id]
            )
        finally:
            conn.close()

        job = ReceiptJobRow(
            receipt_id=receipt_id,
            url="https://x",
            store_name_raw="",
            store_pib_raw="",
            invoice_number="INV-LOG-1",
            parsed_at=None,
            used_journal_fallback=False,
            claim_token="tok",
        )

        with (
            patch(
                "dinary.background.classification.task.parse_receipt",
                return_value=parsed,
            ),
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=(
                    [ClassificationResult("hleb", category_id=1, confidence_level=3)],
                    False,
                ),
            ),
        ):
            asyncio.run(_process_job(job, _make_broker()))

        conn = storage.get_connection()
        try:
            row = conn.execute(
                "SELECT client_expense_id FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()
        finally:
            conn.close()

        assert row is not None
        assert row[0] is not None, "receipt expense must have a client_expense_id"
        assert len(row[0]) == 36, "client_expense_id must be a UUID (36 chars with hyphens)"

    def test_expense_enqueued_for_sheet_logging(self, drain_db):  # noqa: ARG002
        """Each receipt expense is inserted into sheet_logging_jobs as pending."""
        parsed = ParsedReceipt(
            store_name="",
            store_pib="",
            total_amount=200.0,
            invoice_number="INV-LOG-2",
            items=[
                ReceiptItem(
                    name_raw="MLEKO",
                    unit_price=100.0,
                    quantity=1.0,
                    total_price=100.0,
                    tax_label="E",
                ),
                ReceiptItem(
                    name_raw="SIR", unit_price=100.0, quantity=1.0, total_price=100.0, tax_label="E"
                ),
            ],
            items_total=200.0,
            total_ok=True,
            used_journal_fallback=False,
        )
        conn = storage.get_connection()
        try:
            _seed_drain_db(conn)
            conn.execute(
                "INSERT INTO receipts (client_receipt_id, url) VALUES ('log-r2', 'https://x')"
            )
            receipt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO receipt_classification_jobs (receipt_id) VALUES (?)", [receipt_id]
            )
        finally:
            conn.close()

        job = ReceiptJobRow(
            receipt_id=receipt_id,
            url="https://x",
            store_name_raw="",
            store_pib_raw="",
            invoice_number="INV-LOG-2",
            parsed_at=None,
            used_journal_fallback=False,
            claim_token="tok",
        )

        # Two items → two individual expenses (one per item)
        with (
            patch(
                "dinary.background.classification.task.parse_receipt",
                return_value=parsed,
            ),
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=(
                    [
                        ClassificationResult("mleko", category_id=1, confidence_level=3),
                        ClassificationResult("sir", category_id=1, confidence_level=3),
                    ],
                    False,
                ),
            ),
        ):
            asyncio.run(_process_job(job, _make_broker()))

        conn = storage.get_connection()
        try:
            exp_ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM expenses WHERE receipt_id = ?", [receipt_id]
                ).fetchall()
            ]
            queued = (
                [
                    r[0]
                    for r in conn.execute(
                        "SELECT expense_id FROM sheet_logging_jobs WHERE expense_id IN ({})".format(
                            ",".join("?" * len(exp_ids))
                        ),
                        exp_ids,
                    ).fetchall()
                ]
                if exp_ids
                else []
            )
        finally:
            conn.close()

        assert len(exp_ids) == 2, "two items → two individual expenses (one per item)"
        assert set(queued) == set(exp_ids), "every receipt expense must be queued for sheet logging"


# ---------------------------------------------------------------------------
# Per-item expense creation tests
# ---------------------------------------------------------------------------


def _make_broker() -> LLMBroker:
    """Return a minimal broker instance (NullStorage, never called in unit tests)."""
    return LLMBroker(NullStorage())


def _seed_drain_db_with_tags(conn):
    """Seed minimal catalog + two active tags."""
    _seed_drain_db(conn)
    conn.execute("INSERT INTO tags (id, name, is_active) VALUES (1, 'собака', 1)")
    conn.execute("INSERT INTO tags (id, name, is_active) VALUES (2, 'аня', 1)")


@allure.epic("Background Tasks")
@allure.feature("Receipt drain — per-item expenses")
class TestPerItemExpenses:
    def _setup_receipt(self, conn, client_receipt_id="pi-r1", created_at=None):
        _seed_drain_db(conn)
        if created_at:
            conn.execute(
                "INSERT INTO receipts (client_receipt_id, url, created_at)"
                " VALUES (?, 'https://x', ?)",
                [client_receipt_id, created_at],
            )
        else:
            conn.execute(
                "INSERT INTO receipts (client_receipt_id, url) VALUES (?, 'https://x')",
                [client_receipt_id],
            )
        receipt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO receipt_classification_jobs (receipt_id) VALUES (?)", [receipt_id]
        )
        return receipt_id

    def test_three_items_two_categories_creates_three_expenses(self, drain_db):  # noqa: ARG002
        """Each receipt item gets its own expense row even when two share the same category."""
        parsed = ParsedReceipt(
            store_name="",
            store_pib="",
            total_amount=300.0,
            invoice_number="INV-PI-1",
            items=[
                ReceiptItem(
                    name_raw="A", unit_price=100.0, quantity=1.0, total_price=100.0, tax_label="E"
                ),
                ReceiptItem(
                    name_raw="B", unit_price=120.0, quantity=1.0, total_price=120.0, tax_label="E"
                ),
                ReceiptItem(
                    name_raw="C", unit_price=80.0, quantity=1.0, total_price=80.0, tax_label="E"
                ),
            ],
            items_total=300.0,
            total_ok=True,
            used_journal_fallback=False,
        )
        conn = storage.get_connection()
        try:
            receipt_id = self._setup_receipt(conn)
        finally:
            conn.close()

        job = ReceiptJobRow(
            receipt_id=receipt_id,
            url="https://x",
            store_name_raw="",
            store_pib_raw="",
            invoice_number="INV-PI-1",
            parsed_at=None,
            used_journal_fallback=False,
            claim_token="tok",
        )

        with (
            patch("dinary.background.classification.task.parse_receipt", return_value=parsed),
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=(
                    [
                        ClassificationResult("a", category_id=1, confidence_level=3),
                        ClassificationResult("b", category_id=1, confidence_level=3),
                        ClassificationResult("c", category_id=1, confidence_level=3),
                    ],
                    False,
                ),
            ),
        ):
            asyncio.run(_process_job(job, _make_broker()))

        conn = storage.get_connection()
        try:
            rows = conn.execute(
                "SELECT amount FROM expenses WHERE receipt_id = ? ORDER BY amount",
                [receipt_id],
            ).fetchall()
        finally:
            conn.close()

        assert len(rows) == 3, f"expected 3 expenses (one per item), got {len(rows)}"
        amounts = sorted(r[0] for r in rows)
        assert amounts == pytest.approx([80.0, 100.0, 120.0])

    def test_expense_tags_inserted_from_llm_tag_ids(self, drain_db):  # noqa: ARG002
        """LLM tag_ids on a ClassificationResult become expense_tags rows."""
        parsed = ParsedReceipt(
            store_name="",
            store_pib="",
            total_amount=50.0,
            invoice_number="INV-TAGS-1",
            items=[
                ReceiptItem(
                    name_raw="X", unit_price=50.0, quantity=1.0, total_price=50.0, tax_label="E"
                ),
            ],
            items_total=50.0,
            total_ok=True,
            used_journal_fallback=False,
        )
        conn = storage.get_connection()
        try:
            _seed_drain_db(conn)
            conn.execute("INSERT INTO tags (id, name, is_active) VALUES (1, 'собака', 1)")
            conn.execute(
                "INSERT INTO receipts (client_receipt_id, url) VALUES ('tag-r1', 'https://x')"
            )
            receipt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO receipt_classification_jobs (receipt_id) VALUES (?)", [receipt_id]
            )
        finally:
            conn.close()

        job = ReceiptJobRow(
            receipt_id=receipt_id,
            url="https://x",
            store_name_raw="",
            store_pib_raw="",
            invoice_number="INV-TAGS-1",
            parsed_at=None,
            used_journal_fallback=False,
            claim_token="tok",
        )

        with (
            patch("dinary.background.classification.task.parse_receipt", return_value=parsed),
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=(
                    [ClassificationResult("x", category_id=1, confidence_level=3, tag_ids=[1])],
                    False,
                ),
            ),
        ):
            asyncio.run(_process_job(job, _make_broker()))

        conn = storage.get_connection()
        try:
            exp_id = conn.execute(
                "SELECT id FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()
            assert exp_id is not None
            tag_rows = conn.execute(
                "SELECT tag_id FROM expense_tags WHERE expense_id = ?", [exp_id[0]]
            ).fetchall()
        finally:
            conn.close()

        assert len(tag_rows) == 1
        assert tag_rows[0][0] == 1

    def test_expense_tags_empty_when_no_tags(self, drain_db):  # noqa: ARG002
        """When LLM returns no tag_ids, no expense_tags rows are created."""
        parsed = ParsedReceipt(
            store_name="",
            store_pib="",
            total_amount=50.0,
            invoice_number="INV-NOTAG",
            items=[
                ReceiptItem(
                    name_raw="Y", unit_price=50.0, quantity=1.0, total_price=50.0, tax_label="E"
                ),
            ],
            items_total=50.0,
            total_ok=True,
            used_journal_fallback=False,
        )
        conn = storage.get_connection()
        try:
            _seed_drain_db(conn)
            conn.execute(
                "INSERT INTO receipts (client_receipt_id, url) VALUES ('notag-r1', 'https://x')"
            )
            receipt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO receipt_classification_jobs (receipt_id) VALUES (?)", [receipt_id]
            )
        finally:
            conn.close()

        job = ReceiptJobRow(
            receipt_id=receipt_id,
            url="https://x",
            store_name_raw="",
            store_pib_raw="",
            invoice_number="INV-NOTAG",
            parsed_at=None,
            used_journal_fallback=False,
            claim_token="tok",
        )

        with (
            patch("dinary.background.classification.task.parse_receipt", return_value=parsed),
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=(
                    [ClassificationResult("y", category_id=1, confidence_level=3)],
                    False,
                ),
            ),
        ):
            asyncio.run(_process_job(job, _make_broker()))

        conn = storage.get_connection()
        try:
            tag_count = conn.execute("SELECT COUNT(*) FROM expense_tags").fetchone()[0]
        finally:
            conn.close()

        assert tag_count == 0

    def test_auto_event_attached_when_event_covers_receipt_date(self, drain_db):  # noqa: ARG002
        """When an auto-attach event covers the receipt date, expenses get event_id set."""
        parsed = ParsedReceipt(
            store_name="",
            store_pib="",
            total_amount=100.0,
            invoice_number="INV-EVT",
            items=[
                ReceiptItem(
                    name_raw="Z", unit_price=100.0, quantity=1.0, total_price=100.0, tax_label="E"
                ),
            ],
            items_total=100.0,
            total_ok=True,
            used_journal_fallback=False,
        )
        conn = storage.get_connection()
        try:
            _seed_drain_db(conn)
            conn.execute(
                "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled, is_active, auto_tags)"
                " VALUES (1, 'Test', '2026-01-01', '2026-12-31', 1, 1, '[]')"
            )
            conn.execute(
                "INSERT INTO receipts (client_receipt_id, url, created_at)"
                " VALUES ('evt-r1', 'https://x', '2026-06-01 12:00:00')"
            )
            receipt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO receipt_classification_jobs (receipt_id) VALUES (?)", [receipt_id]
            )
        finally:
            conn.close()

        job = ReceiptJobRow(
            receipt_id=receipt_id,
            url="https://x",
            store_name_raw="",
            store_pib_raw="",
            invoice_number="INV-EVT",
            parsed_at=None,
            used_journal_fallback=False,
            claim_token="tok",
        )

        with (
            patch("dinary.background.classification.task.parse_receipt", return_value=parsed),
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=(
                    [ClassificationResult("z", category_id=1, confidence_level=3)],
                    False,
                ),
            ),
        ):
            asyncio.run(_process_job(job, _make_broker()))

        conn = storage.get_connection()
        try:
            exp = conn.execute(
                "SELECT event_id FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()
        finally:
            conn.close()

        assert exp is not None
        assert exp[0] == 1, f"expected event_id=1, got {exp[0]}"

    def test_no_auto_event_when_no_covering_event(self, drain_db):  # noqa: ARG002
        """When no auto-attach event covers the receipt date, expenses get event_id = NULL."""
        parsed = ParsedReceipt(
            store_name="",
            store_pib="",
            total_amount=50.0,
            invoice_number="INV-NOEVT",
            items=[
                ReceiptItem(
                    name_raw="W", unit_price=50.0, quantity=1.0, total_price=50.0, tax_label="E"
                ),
            ],
            items_total=50.0,
            total_ok=True,
            used_journal_fallback=False,
        )
        conn = storage.get_connection()
        try:
            _seed_drain_db(conn)
            # Event does NOT cover 2020 dates
            conn.execute(
                "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled, is_active)"
                " VALUES (1, 'Future', '2027-01-01', '2027-12-31', 1, 1)"
            )
            conn.execute(
                "INSERT INTO receipts (client_receipt_id, url, created_at)"
                " VALUES ('noevt-r1', 'https://x', '2020-06-01 12:00:00')"
            )
            receipt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO receipt_classification_jobs (receipt_id) VALUES (?)", [receipt_id]
            )
        finally:
            conn.close()

        job = ReceiptJobRow(
            receipt_id=receipt_id,
            url="https://x",
            store_name_raw="",
            store_pib_raw="",
            invoice_number="INV-NOEVT",
            parsed_at=None,
            used_journal_fallback=False,
            claim_token="tok",
        )

        with (
            patch("dinary.background.classification.task.parse_receipt", return_value=parsed),
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=(
                    [ClassificationResult("w", category_id=1, confidence_level=3)],
                    False,
                ),
            ),
        ):
            asyncio.run(_process_job(job, _make_broker()))

        conn = storage.get_connection()
        try:
            exp = conn.execute(
                "SELECT event_id FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()
        finally:
            conn.close()

        assert exp is not None
        assert exp[0] is None, f"expected event_id=NULL, got {exp[0]}"

    def test_auto_event_auto_tags_merged_into_expense_tags(self, drain_db):  # noqa: ARG002
        """Event auto_tags are merged into expense_tags for auto-attached expenses."""
        parsed = ParsedReceipt(
            store_name="",
            store_pib="",
            total_amount=100.0,
            invoice_number="INV-AUTOTAG",
            items=[
                ReceiptItem(
                    name_raw="Q", unit_price=100.0, quantity=1.0, total_price=100.0, tax_label="E"
                ),
            ],
            items_total=100.0,
            total_ok=True,
            used_journal_fallback=False,
        )
        conn = storage.get_connection()
        try:
            _seed_drain_db(conn)
            conn.execute("INSERT INTO tags (id, name, is_active) VALUES (1, 'собака', 1)")
            conn.execute(
                "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled, is_active, auto_tags)"
                " VALUES (1, 'Dog Year', '2026-01-01', '2026-12-31', 1, 1, ?)",
                [json.dumps(["собака"])],
            )
            conn.execute(
                "INSERT INTO receipts (client_receipt_id, url, created_at)"
                " VALUES ('autotag-r1', 'https://x', '2026-06-01 12:00:00')"
            )
            receipt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO receipt_classification_jobs (receipt_id) VALUES (?)", [receipt_id]
            )
        finally:
            conn.close()

        job = ReceiptJobRow(
            receipt_id=receipt_id,
            url="https://x",
            store_name_raw="",
            store_pib_raw="",
            invoice_number="INV-AUTOTAG",
            parsed_at=None,
            used_journal_fallback=False,
            claim_token="tok",
        )

        with (
            patch("dinary.background.classification.task.parse_receipt", return_value=parsed),
            patch(
                "dinary.background.classification.task.classify_receipt",
                return_value=(
                    [ClassificationResult("q", category_id=1, confidence_level=3)],
                    False,
                ),
            ),
        ):
            asyncio.run(_process_job(job, _make_broker()))

        conn = storage.get_connection()
        try:
            exp_id = conn.execute(
                "SELECT id FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()
            assert exp_id is not None
            tag_ids = [
                r[0]
                for r in conn.execute(
                    "SELECT tag_id FROM expense_tags WHERE expense_id = ?", [exp_id[0]]
                ).fetchall()
            ]
        finally:
            conn.close()

        assert 1 in tag_ids, f"tag 'собака' (id=1) must be in expense_tags, got {tag_ids}"


# ---------------------------------------------------------------------------
# Main drain loop behaviour
# ---------------------------------------------------------------------------


@allure.epic("Background Tasks")
@allure.feature("Receipt drain — main loop")
class TestDrainLoop:
    def test_wakeup_triggers_drain(self):
        """notify_new_receipt wakes the drain loop immediately."""
        drained = []

        async def mock_drain(_broker=None):
            drained.append(1)

        async def run():
            with (
                patch.object(drain_mod, "_drain_all_pending", side_effect=mock_drain),
                patch.object(drain_mod.settings, "receipt_classification_enabled", True),
            ):
                task = asyncio.create_task(receipt_classification_task(_make_broker()))
                await asyncio.sleep(0)  # let task start and run initial drain
                drained.clear()  # discard the startup drain
                notify_new_receipt()
                for _ in range(10):
                    await asyncio.sleep(0)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(run())
        assert drained, "notify_new_receipt must trigger drain"

    def test_multiple_notifies_batch_into_one_drain(self):
        """Multiple rapid notifies result in a single drain cycle (events are coalesced)."""
        drained = []

        async def mock_drain(_broker=None):
            drained.append(1)

        async def run():
            with (
                patch.object(drain_mod, "_drain_all_pending", side_effect=mock_drain),
                patch.object(drain_mod.settings, "receipt_classification_enabled", True),
            ):
                task = asyncio.create_task(receipt_classification_task(_make_broker()))
                await asyncio.sleep(0)
                drained.clear()
                notify_new_receipt()
                notify_new_receipt()  # second notify before drain starts
                notify_new_receipt()
                for _ in range(20):
                    await asyncio.sleep(0)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(run())
        assert len(drained) == 1, "rapid notifies must batch into one drain cycle"

    def test_notify_new_receipt_sets_wakeup_event(self):
        """notify_new_receipt sets the module-level event so the drain loop wakes immediately."""

        async def run():
            event = asyncio.Event()
            loop = asyncio.get_running_loop()
            drain_mod._wakeup_event = event
            drain_mod._wakeup_loop = loop
            try:
                assert not event.is_set()
                notify_new_receipt()
                await asyncio.sleep(0)  # allow call_soon_threadsafe to fire
                assert event.is_set()
            finally:
                drain_mod._wakeup_event = None
                drain_mod._wakeup_loop = None

        asyncio.run(run())

    def test_notify_new_receipt_is_noop_before_task_starts(self):
        """notify_new_receipt does not raise if called before the drain task has started."""
        original = drain_mod._wakeup_event
        drain_mod._wakeup_event = None
        try:
            notify_new_receipt()  # must not raise
        finally:
            drain_mod._wakeup_event = original

    def test_drain_all_pending_noop_with_no_jobs(self, drain_db):  # noqa: ARG002
        """_drain_all_pending completes immediately when no jobs are pending."""
        asyncio.run(_drain_all_pending(_make_broker()))  # must not raise

    def test_drain_all_pending_runs_jobs_concurrently(self, drain_db):  # noqa: ARG002
        """Multiple pending jobs all run in parallel via _drain_all_pending."""
        start_times: list[float] = []

        async def slow_job(job: ReceiptJobRow, _broker=None) -> None:
            start_times.append(time.monotonic())
            await asyncio.sleep(0.05)  # simulate I/O

        conn = storage.get_connection()
        try:
            _seed_drain_db(conn)
            for i in range(3):
                conn.execute(
                    "INSERT INTO receipts (client_receipt_id, url, parsed_at)"
                    " VALUES (?, 'https://x', '2026-05-01')",
                    [f"r{i}"],
                )
                rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO receipt_classification_jobs (receipt_id) VALUES (?)", [rid]
                )
        finally:
            conn.close()

        with patch.object(drain_mod, "_process_job", side_effect=slow_job):
            asyncio.run(_drain_all_pending(_make_broker()))

        assert len(start_times) == 3
        # All 3 should start within a short window (concurrent, not sequential)
        assert max(start_times) - min(start_times) < 0.04, "jobs must run concurrently"

    def test_safety_net_timeout_triggers_drain(self):
        """Drain fires when the 300 s wait_for times out, even without notify_new_receipt."""
        drain_call_count = {"n": 0}
        timeout_fired = {"n": 0}

        async def mock_drain(_broker=None):
            drain_call_count["n"] += 1

        async def instant_timeout(coro, timeout):  # noqa: ARG001
            # Always wrap the coro in a task and cancel it so asyncio considers it
            # properly started — prevents "coroutine was never awaited" RuntimeWarning.
            t = asyncio.ensure_future(coro)
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
            timeout_fired["n"] += 1
            if timeout_fired["n"] == 1:
                raise asyncio.TimeoutError
            # After firing once, park here so the event loop can deliver cancellation.
            await asyncio.sleep(3600)

        async def run():
            with (
                patch.object(drain_mod, "_drain_all_pending", side_effect=mock_drain),
                patch.object(drain_mod.settings, "receipt_classification_enabled", True),
                patch(
                    "dinary.background.classification.task.asyncio.wait_for",
                    side_effect=instant_timeout,
                ),
            ):
                task = asyncio.create_task(receipt_classification_task(_make_broker()))
                for _ in range(10):
                    await asyncio.sleep(0)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(run())
        assert timeout_fired["n"] >= 1, "wait_for timeout path must be exercised"
        # startup drain (call 1) + timeout-triggered drain (call 2)
        assert drain_call_count["n"] >= 2, (
            "drain must fire after 300 s timeout, not only on startup"
        )

    def test_error_in_one_job_does_not_cancel_others(self, drain_db):  # noqa: ARG002
        """An exception in one _process_job does not prevent others from completing."""
        completed: list[int] = []

        call_count = {"n": 0}

        async def mixed_job(job: ReceiptJobRow, _broker=None) -> None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated failure")
            completed.append(job.receipt_id)

        conn = storage.get_connection()
        try:
            _seed_drain_db(conn)
            for i in range(3):
                conn.execute(
                    "INSERT INTO receipts (client_receipt_id, url, parsed_at)"
                    " VALUES (?, 'https://x', '2026-05-01')",
                    [f"err-r{i}"],
                )
                rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO receipt_classification_jobs (receipt_id) VALUES (?)", [rid]
                )
        finally:
            conn.close()

        with patch.object(drain_mod, "_process_job", side_effect=mixed_job):
            asyncio.run(_drain_all_pending(_make_broker()))

        assert len(completed) == 2, "2 of 3 jobs must complete despite one failure"

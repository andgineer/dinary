"""Tests for the receipt classification drain loop and circuit breaker."""

import asyncio
import shutil
import sqlite3
import unittest.mock
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest

import dinary.background.receipt_classification_task as drain_mod
from dinary.services import db_migrations, ledger_repo
from dinary.background.receipt_classification_task import (
    _activate_llm_backoff,
    _drain_one,
    _reset_llm_backoff,
)
from dinary.services.llm_client import AllProvidersExhausted


@pytest.fixture(autouse=True)
def _reset_backoff():
    """Reset circuit-breaker globals before and after each test."""
    _reset_llm_backoff()
    yield
    _reset_llm_backoff()


def _run(coro):
    return asyncio.run(coro)


@allure.epic("Background Tasks")
@allure.feature("Receipt drain — circuit breaker")
class TestCircuitBreaker:
    def test_drain_skips_when_backoff_active(self):
        """_drain_one returns immediately when the LLM backoff window is active."""
        drain_mod._llm_backoff_until = datetime.now(UTC) + timedelta(hours=1)

        with patch.object(drain_mod, "_claim_job", return_value=None) as mock_claim:
            _run(_drain_one())

        mock_claim.assert_not_called()

    def test_drain_runs_when_backoff_expired(self):
        """_drain_one claims a job once the backoff window has passed."""
        drain_mod._llm_backoff_until = datetime.now(UTC) - timedelta(seconds=1)

        with patch.object(drain_mod, "_claim_job", return_value=None) as mock_claim:
            _run(_drain_one())

        mock_claim.assert_called_once()

    def test_all_providers_exhausted_activates_backoff(self):
        """AllProvidersExhausted in _process_job activates the circuit breaker."""
        mock_job = MagicMock()
        mock_job.receipt_id = 1
        mock_job.claim_token = "tok"

        with (
            patch.object(drain_mod, "_claim_job", return_value=mock_job),
            patch.object(
                drain_mod,
                "_process_job",
                new=AsyncMock(side_effect=AllProvidersExhausted),
            ),
            patch.object(drain_mod, "_release", new=MagicMock()),
        ):
            _run(_drain_one())

        assert drain_mod._llm_backoff_until is not None
        assert drain_mod._llm_backoff_until > datetime.now(UTC)
        assert drain_mod._llm_current_backoff_sec == drain_mod._LLM_BACKOFF_INITIAL_SEC

    def test_backoff_doubles_on_repeated_exhaustion(self):
        """Successive AllProvidersExhausted events double the backoff."""
        mock_job = MagicMock()
        mock_job.receipt_id = 1
        mock_job.claim_token = "tok"

        for _ in range(3):
            with (
                patch.object(drain_mod, "_claim_job", return_value=mock_job),
                patch.object(
                    drain_mod,
                    "_process_job",
                    new=AsyncMock(side_effect=AllProvidersExhausted),
                ),
                patch.object(drain_mod, "_release", new=MagicMock()),
            ):
                # Clear backoff so the loop body runs each iteration
                drain_mod._llm_backoff_until = None
                _run(_drain_one())

        expected = min(
            drain_mod._LLM_BACKOFF_INITIAL_SEC * (2**2),
            drain_mod._LLM_BACKOFF_MAX_SEC,
        )
        assert drain_mod._llm_current_backoff_sec == expected

    def test_backoff_caps_at_max(self):
        """Backoff never exceeds _LLM_BACKOFF_MAX_SEC."""
        drain_mod._llm_current_backoff_sec = drain_mod._LLM_BACKOFF_MAX_SEC
        _activate_llm_backoff()
        assert drain_mod._llm_current_backoff_sec == drain_mod._LLM_BACKOFF_MAX_SEC

    def test_successful_drain_resets_backoff(self):
        """A successful _process_job call resets the circuit breaker."""
        drain_mod._llm_backoff_until = datetime.now(UTC) - timedelta(seconds=1)
        drain_mod._llm_current_backoff_sec = 120.0

        mock_job = MagicMock()
        mock_job.receipt_id = 1

        with (
            patch.object(drain_mod, "_claim_job", return_value=mock_job),
            patch.object(drain_mod, "_process_job", new=AsyncMock()),
        ):
            _run(_drain_one())

        assert drain_mod._llm_backoff_until is None
        assert drain_mod._llm_current_backoff_sec == 0.0

    def test_parse_error_does_not_activate_backoff(self):
        """Parse errors (permanent) poison the job but do not activate the circuit breaker."""
        from sr_invoice_parser.exceptions import ParserParseException

        mock_job = MagicMock()
        mock_job.receipt_id = 1
        mock_job.claim_token = "tok"

        with (
            patch.object(drain_mod, "_claim_job", return_value=mock_job),
            patch.object(
                drain_mod,
                "_process_job",
                new=AsyncMock(side_effect=ParserParseException("bad")),
            ),
            patch.object(drain_mod, "_poison", new=MagicMock()),
        ):
            _run(_drain_one())

        assert drain_mod._llm_backoff_until is None

    def test_no_job_does_not_affect_backoff(self):
        """An empty queue (no job) leaves backoff state unchanged."""
        with patch.object(drain_mod, "_claim_job", return_value=None):
            _run(_drain_one())

        assert drain_mod._llm_backoff_until is None
        assert drain_mod._llm_current_backoff_sec == 0.0


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
    monkeypatch.setattr(ledger_repo, "DB_PATH", dst)
    monkeypatch.setattr(ledger_repo, "DATA_DIR", tmp_path)
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
        import logging

        from dinary.background.receipt_classification_task import _process_job
        from dinary.services.receipt_repo import ReceiptJobRow

        conn = ledger_repo.get_connection()
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
        with caplog.at_level(
            logging.WARNING, logger="dinary.background.receipt_classification_task"
        ):
            asyncio.run(_process_job(job))

        assert any("no items" in r.message.lower() for r in caplog.records)

        conn = ledger_repo.get_connection()
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
        from unittest.mock import AsyncMock, MagicMock

        from dinary.background.receipt_classification_task import _process_job
        from dinary.services.llm_client import ClassificationResult
        from dinary.services.receipt_parser import ParsedReceipt, ReceiptItem
        from dinary.services.receipt_repo import ReceiptJobRow

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
        pool = MagicMock()
        pool.get_chain_name = AsyncMock(return_value="Lidl")
        pool.classify_receipt = AsyncMock(
            return_value=(
                [
                    ClassificationResult(
                        item_name_normalized="hleb", category_id=1, confidence_level=3
                    )
                ],
                True,  # used_failover=True → penalty −1 → final conf = 2
            )
        )

        conn = ledger_repo.get_connection()
        try:
            _seed_drain_db(conn)
            conn.execute("INSERT INTO stores (chain_name, pib) VALUES ('Lidl', '100')")
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
                "dinary.background.receipt_classification_task.parse_receipt",
                return_value=parsed,
            ),
            patch(
                "dinary.background.receipt_classification_task.ProviderPool",
                return_value=pool,
            ),
        ):
            asyncio.run(_process_job(job))

        conn = ledger_repo.get_connection()
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
        from unittest.mock import AsyncMock, MagicMock

        from dinary.background.receipt_classification_task import _process_job
        from dinary.services.llm_client import ClassificationResult
        from dinary.services.receipt_parser import ParsedReceipt, ReceiptItem
        from dinary.services.receipt_repo import ReceiptJobRow

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
        pool = MagicMock()
        pool.get_chain_name = AsyncMock(return_value="")
        pool.classify_receipt = AsyncMock(
            return_value=(
                [
                    ClassificationResult(
                        item_name_normalized="mleko", category_id=1, confidence_level=4
                    )
                ],
                False,
            )
        )

        fixed_created_at = "2026-01-15 08:30:00"
        conn = ledger_repo.get_connection()
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
                "dinary.background.receipt_classification_task.parse_receipt",
                return_value=parsed,
            ),
            patch(
                "dinary.background.receipt_classification_task.ProviderPool",
                return_value=pool,
            ),
        ):
            asyncio.run(_process_job(job))

        conn = ledger_repo.get_connection()
        try:
            exp = conn.execute(
                "SELECT datetime FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()
        finally:
            conn.close()

        assert exp is not None
        assert str(exp[0]).startswith("2026-01-15 08:30")

    def test_fallback_metadata_cleared_on_successful_parse(self, drain_db):  # noqa: ARG002
        """_save_parsed clears fallback metadata when /specifications succeeds."""
        from dinary.background.receipt_classification_task import _save_parsed
        from dinary.services.receipt_parser import ParsedReceipt, ReceiptItem

        conn = ledger_repo.get_connection()
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

        conn = ledger_repo.get_connection()
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
        from unittest.mock import AsyncMock, MagicMock

        from dinary.background.receipt_classification_task import _process_job
        from dinary.services.llm_client import ClassificationResult
        from dinary.services.receipt_parser import ParsedReceipt, ReceiptItem
        from dinary.services.receipt_repo import ReceiptJobRow

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
        pool = MagicMock()
        pool.get_chain_name = AsyncMock(return_value="")
        pool.classify_receipt = AsyncMock(
            return_value=(
                [
                    ClassificationResult(
                        item_name_normalized="kafa", category_id=1, confidence_level=4
                    )
                ],
                False,
            )
        )

        conn = ledger_repo.get_connection()
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
                "dinary.background.receipt_classification_task.parse_receipt",
                return_value=parsed,
            ),
            patch(
                "dinary.background.receipt_classification_task.ProviderPool",
                return_value=pool,
            ),
        ):
            asyncio.run(_process_job(job))

        conn = ledger_repo.get_connection()
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
        from dinary.background.receipt_classification_task import _process_job
        from dinary.services.llm_client import ClassificationResult
        from dinary.services.receipt_parser import ParsedReceipt, ReceiptItem
        from dinary.services.receipt_repo import ReceiptJobRow

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
        pool = MagicMock()
        pool.get_chain_name = AsyncMock(return_value="Lidl")
        # LLM returns cat=1 conf=2; journal penalty (−1) → final conf=1
        pool.classify_receipt = AsyncMock(
            return_value=(
                [
                    ClassificationResult(
                        item_name_normalized="nepoznato", category_id=1, confidence_level=2
                    )
                ],
                False,
            )
        )

        conn = ledger_repo.get_connection()
        try:
            _seed_drain_db(conn)
            conn.execute("INSERT INTO stores (chain_name, pib) VALUES ('Lidl', '100')")
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

        with (
            patch(
                "dinary.background.receipt_classification_task.parse_receipt", return_value=parsed
            ),
            patch("dinary.background.receipt_classification_task.ProviderPool", return_value=pool),
        ):
            asyncio.run(_process_job(job))

        conn = ledger_repo.get_connection()
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

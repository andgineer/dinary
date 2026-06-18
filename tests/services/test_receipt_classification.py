"""Tests for the receipt classification drain cycle."""

import asyncio
import shutil
import sqlite3
import unittest.mock
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest

from conftest import NullStorage
from dinary.adapters.llmbroker import Execution, LLMBroker
from dinary.adapters.rate_helpers import save_db_rate
from dinary.background.classification.receipt_classifier import (
    ClassificationResult,
    ClassifyOutcome,
)
from dinary.background.classification.item_normalizer import normalize_item_name
from dinary.background.classification.persist import (
    RECEIPT_CURRENCY,
    RateMissingError,
    persist_classification_results,
)
from dinary.db.storage import connect
from dinary.background.classification.task import (
    _check_store_already_resolved,
    _classify_and_persist,
    _drain_all_pending,
    _load_top_fallback_categories,
    _run_llm_pass,
    ClassificationExhaustedError,
    InsufficientCategoriesError,
)
from dinary.config import settings
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
        "INSERT INTO category_groups (id, name, sort_order, is_active) VALUES (1, 'Food', 1, 1)"
    )
    conn.execute(
        "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'groceries', 1, 1)"
    )
    conn.execute(
        "INSERT INTO categories (id, name, group_id, is_active) VALUES (2, 'household', 1, 1)"
    )
    # Add more categories so _load_top_fallback_categories pre-check (>= 5) passes
    conn.execute("INSERT INTO categories (id, name, group_id, is_active) VALUES (3, 'cat3', 1, 1)")
    conn.execute("INSERT INTO categories (id, name, group_id, is_active) VALUES (4, 'cat4', 1, 1)")
    conn.execute("INSERT INTO categories (id, name, group_id, is_active) VALUES (5, 'cat5', 1, 1)")


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


def _make_execution() -> Execution:
    storage_mock = MagicMock()
    storage_mock.on_quality_feedback = AsyncMock()
    return Execution(output="ok", provider_label="P1", storage=storage_mock)


def _classify_patch(results: list[ClassificationResult], execution_failed: bool = False):
    execution = _make_execution()
    outcome = ClassifyOutcome(
        results=results,
        broker_unavailable=False,
        execution_failed=execution_failed
        or any(r.category_id is None for r in results)
        or not results,
        execution=execution,
    )
    return patch(
        "dinary.background.classification.task.classify_receipt",
        return_value=outcome,
    )


def _hleb_result(cat_id=1, conf=3) -> ClassificationResult:
    return ClassificationResult(
        item_name_normalized="hleb", category_id=cat_id, confidence_level=conf
    )


@allure.epic("Receipts")
@allure.feature("Background tasks")
class TestClassifyAndPersist:
    @pytest.fixture(autouse=True)
    def _no_fx_conversion(self, monkeypatch):
        """Patch accounting_currency to RSD so tests skip currency conversion."""
        monkeypatch.setattr(settings, "accounting_currency", "RSD")

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
            asyncio.run(_classify_and_persist(_broker(), job, items, None, None))
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
            asyncio.run(_classify_and_persist(_broker(), job, items, None, None))
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

    def test_all_none_result_uses_fallback_and_creates_expense(self, conn):
        """When LLM returns cat_id=None for all items, fallback kicks in and creates expense."""
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)
        conn.close()

        # classify_receipt returns execution_failed=True (any None) → retry loop exhausted
        # → ClassificationExhaustedError → fallback
        with patch(
            "dinary.background.classification.task.classify_receipt",
            side_effect=_exhausted_classify_side_effect(),
        ):
            asyncio.run(_classify_and_persist(_broker(), job, items, None, None))

        conn2 = storage.get_connection()
        try:
            exp_count = conn2.execute(
                "SELECT COUNT(*) FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()[0]
            job_row = conn2.execute(
                "SELECT status FROM receipt_classification_jobs WHERE receipt_id = ?",
                [receipt_id],
            ).fetchone()
        finally:
            conn2.close()

        assert exp_count == 1, "fallback must create an expense at the top catalog category"
        assert job_row is None, "job must be completed (deleted) after fallback"

    def test_conf1_llm_result_creates_expense_and_rule(self, conn):
        """cat_id=1, conf=1 → execution_failed=False → expense IS created and rule IS created."""
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)
        conn.close()

        with _classify_patch(
            [ClassificationResult(item_name_normalized="hleb", category_id=1, confidence_level=1)]
        ):
            asyncio.run(_classify_and_persist(_broker(), job, items, None, None))

        conn2 = storage.get_connection()
        try:
            exp_count = conn2.execute(
                "SELECT COUNT(*) FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()[0]
            rule = conn2.execute(
                "SELECT category_id FROM classification_rules WHERE item_name_normalized = 'hleb'"
            ).fetchone()
        finally:
            conn2.close()

        assert exp_count == 1, "conf=1 with valid category must create expense"
        assert rule is not None, "conf=1 with valid category must create rule"

    def test_complete_job_inside_transaction(self, conn):
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)
        conn.close()

        with _classify_patch([_hleb_result()]):
            asyncio.run(_classify_and_persist(_broker(), job, items, None, None))

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

        with _classify_patch([_hleb_result()]):
            asyncio.run(_classify_and_persist(_broker(), job, items, None, None))

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
            asyncio.run(_classify_and_persist(_broker(), job, items, None, None))

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
            asyncio.run(_classify_and_persist(_broker(), job, items, None, None))

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
            asyncio.run(_classify_and_persist(_broker(), job, items, None, None))
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

    def test_amount_converted_to_accounting_currency(self, conn, monkeypatch):
        """expenses.amount stores the converted accounting-currency value."""
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        _seed_catalog(conn)
        conn.execute(
            "INSERT INTO receipts (client_receipt_id, url, parsed_at, purchase_datetime)"
            " VALUES ('r_fx', 'https://x', '2026-05-01T10:00:00', '2026-05-01T10:00:00')"
        )
        receipt_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            "INSERT INTO receipt_items (receipt_id, name_raw, total_price, quantity, unit_price)"
            " VALUES (?, 'hleb', 120.0, 1, 120.0)",
            [receipt_id],
        )
        insert_job(conn, receipt_id)
        save_db_rate(conn, date(2026, 5, 1), RECEIPT_CURRENCY, "EUR", Decimal("0.009"))
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)
        conn.close()

        with _classify_patch([_hleb_result(cat_id=1, conf=3)]):
            asyncio.run(_classify_and_persist(_broker(), job, items, None, None))

        conn2 = storage.get_connection()
        try:
            exp = conn2.execute(
                "SELECT amount, amount_original, currency_original"
                " FROM expenses WHERE receipt_id = ?",
                [receipt_id],
            ).fetchone()
        finally:
            conn2.close()

        assert exp is not None
        assert float(exp[0]) == pytest.approx(1.08)  # 120 * 0.009
        assert exp[1] == 120.0
        assert exp[2] == RECEIPT_CURRENCY

    def test_fallback_preserves_item_name_normalized(self, conn):
        """Fallback ClassificationResult carries the original item name, not the category label."""
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)  # name_raw = "hleb"
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)
        conn.close()

        captured: list = []

        def _cap_persist(job, items, classifications, rule_hits, llm_results, *args, **kwargs):
            captured.extend(llm_results.values())

        with (
            patch(
                "dinary.background.classification.task.classify_receipt",
                side_effect=_exhausted_classify_side_effect(),
            ),
            patch(
                "dinary.background.classification.task.persist_classification_results",
                _cap_persist,
            ),
        ):
            asyncio.run(_classify_and_persist(_broker(), job, items, None, None))

        assert len(captured) == 1
        assert captured[0].item_name_normalized == normalize_item_name("hleb")

    def test_fallback_alternative_category_ids_is_top_cats_tail(self, conn):
        """Fallback alternative_category_ids equals top_cats[1:]; primary not repeated."""
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)
        conn.close()

        captured: list = []

        def _cap_persist(job, items, classifications, rule_hits, llm_results, *args, **kwargs):
            captured.extend(llm_results.values())

        with (
            patch(
                "dinary.background.classification.task.classify_receipt",
                side_effect=_exhausted_classify_side_effect(),
            ),
            patch(
                "dinary.background.classification.task._load_top_fallback_categories",
                return_value=[3, 1, 2, 4, 5],
            ),
            patch(
                "dinary.background.classification.task.persist_classification_results",
                _cap_persist,
            ),
        ):
            asyncio.run(_classify_and_persist(_broker(), job, items, None, None))

        assert len(captured) == 1
        assert captured[0].category_id == 3
        assert captured[0].alternative_category_ids == [1, 2, 4, 5]

    def test_notify_new_work_raises_expenses_committed_no_propagation(self, conn):
        """notify_new_work raising must not propagate — expenses stay committed, job not poisoned."""
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)
        conn.close()

        with (
            _classify_patch([_hleb_result(cat_id=1, conf=3)]),
            patch(
                "dinary.background.classification.persist.sheet_logging.notify_new_work",
                side_effect=RuntimeError("sheet down"),
            ),
        ):
            asyncio.run(_classify_and_persist(_broker(), job, items, None, None))

        conn2 = storage.get_connection()
        try:
            exp_count = conn2.execute(
                "SELECT COUNT(*) FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()[0]
        finally:
            conn2.close()

        assert exp_count == 1

    def test_mark_failed_raises_fallback_creates_expense(self, conn):
        """mark_failed() raising on the last attempt must not prevent fallback expense creation."""
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)
        conn.close()

        async def _failing_mark_failed(*_args, **_kwargs):
            storage_mock = MagicMock()
            storage_mock.on_quality_feedback = AsyncMock(side_effect=RuntimeError("storage down"))
            execution = Execution(output="bad json", provider_label="P1", storage=storage_mock)
            return ClassifyOutcome(
                results=[],
                broker_unavailable=False,
                execution_failed=True,
                execution=execution,
            )

        with patch(
            "dinary.background.classification.task.classify_receipt",
            side_effect=_failing_mark_failed,
        ):
            asyncio.run(_classify_and_persist(_broker(), job, items, None, None))

        conn2 = storage.get_connection()
        try:
            exp_count = conn2.execute(
                "SELECT COUNT(*) FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()[0]
        finally:
            conn2.close()

        assert exp_count == 1, "fallback must create an expense even when mark_failed raises"

    def test_rate_missing_raises_and_no_expense(self, conn, monkeypatch):
        """When no rate is available RateMissingError is raised and no expense is stored."""
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        _seed_catalog(conn)
        conn.execute(
            "INSERT INTO receipts (client_receipt_id, url, parsed_at, purchase_datetime)"
            " VALUES ('r_nofx', 'https://x', '2026-05-01T10:00:00', '2026-05-01T10:00:00')"
        )
        receipt_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            "INSERT INTO receipt_items (receipt_id, name_raw, total_price, quantity, unit_price)"
            " VALUES (?, 'hleb', 120.0, 1, 120.0)",
            [receipt_id],
        )
        insert_job(conn, receipt_id)
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)
        conn.close()

        with _classify_patch([_hleb_result(cat_id=1, conf=3)]):
            with patch(
                "dinary.background.classification.persist.get_rate",
                side_effect=ValueError("no rate"),
            ):
                with pytest.raises(RateMissingError):
                    asyncio.run(_classify_and_persist(_broker(), job, items, None, None))

        conn2 = storage.get_connection()
        try:
            exp_count = conn2.execute(
                "SELECT COUNT(*) FROM expenses WHERE receipt_id = ?", [receipt_id]
            ).fetchone()[0]
        finally:
            conn2.close()

        assert exp_count == 0


def _exhausted_classify_side_effect():
    """Return a side_effect that always returns execution_failed=True, simulating exhaustion."""

    async def _side_effect(*_args, **_kwargs):
        storage_mock = MagicMock()
        storage_mock.on_quality_feedback = AsyncMock()
        execution = Execution(output="bad json", provider_label="P1", storage=storage_mock)
        return ClassifyOutcome(
            results=[],
            broker_unavailable=False,
            execution_failed=True,
            execution=execution,
        )

    return _side_effect


@allure.epic("Receipts")
@allure.feature("Background tasks")
class TestLoadTopFallbackCategories:
    def test_raises_when_fewer_than_5_active_categories(self, conn):
        conn.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active) VALUES (1, 'G', 1, 1)"
        )
        conn.execute("INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'a', 1, 1)")
        conn.execute("INSERT INTO categories (id, name, group_id, is_active) VALUES (2, 'b', 1, 1)")
        conn.close()

        with pytest.raises(InsufficientCategoriesError):
            _load_top_fallback_categories(6)

    def test_pads_with_active_categories_when_no_history(self, conn):
        conn.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active) VALUES (1, 'G', 1, 1)"
        )
        for i in range(1, 7):
            conn.execute(
                f"INSERT INTO categories (id, name, group_id, is_active) VALUES ({i}, 'c{i}', 1, 1)"
            )
        conn.close()

        result = _load_top_fallback_categories(6)

        assert result == [1, 2, 3, 4, 5, 6]

    def test_returns_top_history_then_pads(self, conn):
        conn.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active) VALUES (1, 'G', 1, 1)"
        )
        for i in range(1, 8):
            conn.execute(
                f"INSERT INTO categories (id, name, group_id, is_active) VALUES ({i}, 'c{i}', 1, 1)"
            )
        conn.execute("INSERT INTO receipts (client_receipt_id, url) VALUES ('r1', 'https://x')")
        rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for j in range(3):
            conn.execute(
                "INSERT INTO expenses (client_expense_id, datetime, amount, amount_original,"
                " currency_original, category_id, receipt_id)"
                " VALUES (?, datetime('now'), 100, 100, 'RSD', 5, ?)",
                [f"e5-{j}", rid],
            )
        conn.execute(
            "INSERT INTO expenses (client_expense_id, datetime, amount, amount_original,"
            " currency_original, category_id, receipt_id)"
            " VALUES ('e6', datetime('now'), 100, 100, 'RSD', 6, ?)",
            [rid],
        )
        conn.close()

        result = _load_top_fallback_categories(4)

        assert result[0] == 5
        assert result[1] == 6
        assert len(result) == 4
        assert 5 not in result[2:]
        assert 6 not in result[2:]

    def test_padding_excludes_categories_already_in_history(self, conn):
        conn.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active) VALUES (1, 'G', 1, 1)"
        )
        for i in range(1, 7):
            conn.execute(
                f"INSERT INTO categories (id, name, group_id, is_active) VALUES ({i}, 'c{i}', 1, 1)"
            )
        conn.execute("INSERT INTO receipts (client_receipt_id, url) VALUES ('r1', 'https://x')")
        rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for cat_id in [3, 4, 5]:
            conn.execute(
                "INSERT INTO expenses (client_expense_id, datetime, amount, amount_original,"
                " currency_original, category_id, receipt_id)"
                " VALUES (?, datetime('now'), 100, 100, 'RSD', ?, ?)",
                [f"e{cat_id}", cat_id, rid],
            )
        conn.close()

        result = _load_top_fallback_categories(6)

        assert len(result) == 6
        assert len(set(result)) == 6, "no duplicates"
        for cat_id in [1, 2, 3, 4, 5, 6]:
            assert cat_id in result


@allure.epic("Receipts")
@allure.feature("Background tasks")
class TestRunLLMPass:
    def test_single_provider_one_attempt_on_success(self):
        """provider_count=1 → max_attempts=1; success on first attempt."""
        from unittest.mock import MagicMock

        broker = MagicMock(spec=LLMBroker)
        broker.provider_count = 1
        job = _make_job(receipt_id=1)
        expected = ClassificationResult(
            item_name_normalized="hleb", category_id=1, confidence_level=3
        )
        outcome = ClassifyOutcome(
            results=[expected],
            broker_unavailable=False,
            execution_failed=False,
            execution=_make_execution(),
        )
        with patch(
            "dinary.background.classification.task.classify_receipt",
            return_value=outcome,
        ) as mock_classify:
            result = asyncio.run(_run_llm_pass(broker, job, [(1, "hleb")], {1: "x"}, {}))
        assert mock_classify.call_count == 1
        assert result[1] == expected

    def test_three_providers_all_fail_raises_exhausted_after_3_calls(self):
        """provider_count=3 → max_attempts=3; all fail → ClassificationExhaustedError after 3 calls."""
        broker = MagicMock(spec=LLMBroker)
        broker.provider_count = 3
        job = _make_job(receipt_id=1)
        with patch(
            "dinary.background.classification.task.classify_receipt",
            side_effect=_exhausted_classify_side_effect(),
        ) as mock_classify:
            with pytest.raises(ClassificationExhaustedError):
                asyncio.run(_run_llm_pass(broker, job, [(1, "hleb")], {1: "x"}, {}))
        assert mock_classify.call_count == 3

    def test_provider_count_above_3_caps_attempts_at_3(self):
        """provider_count=5 → max_attempts capped at 3, not 5."""
        broker = MagicMock(spec=LLMBroker)
        broker.provider_count = 5
        job = _make_job(receipt_id=1)
        with patch(
            "dinary.background.classification.task.classify_receipt",
            side_effect=_exhausted_classify_side_effect(),
        ) as mock_classify:
            with pytest.raises(ClassificationExhaustedError):
                asyncio.run(_run_llm_pass(broker, job, [(1, "hleb")], {1: "x"}, {}))
        assert mock_classify.call_count == 3

    def test_mark_failed_raises_still_raises_exhausted(self):
        """mark_failed() raising must not prevent ClassificationExhaustedError from propagating."""
        broker = MagicMock(spec=LLMBroker)
        broker.provider_count = 1
        job = _make_job(receipt_id=1)

        async def _failing_mark_failed(*_args, **_kwargs):
            storage_mock = MagicMock()
            storage_mock.on_quality_feedback = AsyncMock(side_effect=RuntimeError("storage down"))
            execution = Execution(output="bad json", provider_label="P1", storage=storage_mock)
            return ClassifyOutcome(
                results=[],
                broker_unavailable=False,
                execution_failed=True,
                execution=execution,
            )

        with patch(
            "dinary.background.classification.task.classify_receipt",
            side_effect=_failing_mark_failed,
        ):
            with pytest.raises(ClassificationExhaustedError):
                asyncio.run(_run_llm_pass(broker, job, [(1, "hleb")], {1: "x"}, {}))

    def test_second_attempt_succeeds_after_first_fails(self):
        """provider_count=2 → max_attempts=2; first fails, second succeeds."""
        broker = MagicMock(spec=LLMBroker)
        broker.provider_count = 2
        job = _make_job(receipt_id=1)
        expected = ClassificationResult(
            item_name_normalized="hleb", category_id=1, confidence_level=3
        )
        call_count = {"n": 0}

        async def _side_effect(*_args, **_kwargs):
            call_count["n"] += 1
            storage_mock = MagicMock()
            storage_mock.on_quality_feedback = AsyncMock()
            execution = Execution(output="ok", provider_label="P1", storage=storage_mock)
            if call_count["n"] == 1:
                return ClassifyOutcome(
                    results=[], broker_unavailable=False, execution_failed=True, execution=execution
                )
            return ClassifyOutcome(
                results=[expected],
                broker_unavailable=False,
                execution_failed=False,
                execution=execution,
            )

        with patch(
            "dinary.background.classification.task.classify_receipt",
            side_effect=_side_effect,
        ) as mock_classify:
            result = asyncio.run(_run_llm_pass(broker, job, [(1, "hleb")], {1: "x"}, {}))
        assert mock_classify.call_count == 2
        assert result[1] == expected


@allure.epic("Receipts")
@allure.feature("Background tasks")
class TestDrainAllPending:
    def test_cancelled_job_does_not_propagate(self):
        """CancelledError from a gather result must be logged, not propagated."""
        broker = MagicMock(spec=LLMBroker)
        job = _make_job(receipt_id=99)

        async def _raise_cancelled(*_args, **_kwargs):
            raise asyncio.CancelledError()

        with (
            patch(
                "dinary.background.classification.task._claim_all_pending",
                return_value=[job],
            ),
            patch(
                "dinary.background.classification.task._process_job",
                side_effect=_raise_cancelled,
            ),
        ):
            asyncio.run(_drain_all_pending(broker))


@allure.epic("Receipts")
@allure.feature("Background tasks")
class TestCheckStoreAlreadyResolved:
    def test_null_chain_id_returns_store_id_with_none_chain(self, conn):
        """stores.chain_id = NULL must return (store_id, None), not None — store is resolved, chain is pending."""
        conn.execute("INSERT INTO stores (name) VALUES ('NULL CHAIN STORE')")
        store_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO receipts (client_receipt_id, url, parsed_at, store_id)"
            " VALUES ('r_nullchain', 'https://x', '2026-05-01T10:00:00', ?)",
            [store_id],
        )
        receipt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()

        result = _check_store_already_resolved(receipt_id)
        assert result == (store_id, None)


@allure.epic("Receipts")
@allure.feature("Background tasks")
class TestPersistRollbackSafety:
    @pytest.fixture(autouse=True)
    def _no_fx_conversion(self, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "RSD")

    def test_write_failure_propagates_original_exception(self, conn, monkeypatch):
        """Write failures inside the transaction must reach the caller unchanged."""
        _seed_catalog(conn)
        receipt_id = _seed_receipt(conn)
        job = claim_next_job(conn)
        items = get_receipt_items(conn, receipt_id)
        conn.close()

        def _bad_write(*_args, **_kw):
            raise RuntimeError("intentional write failure")

        monkeypatch.setattr(
            "dinary.background.classification.persist._write_single_item",
            _bad_write,
        )

        with pytest.raises(RuntimeError, match="intentional write failure"):
            persist_classification_results(
                job,
                items,
                {item.id: (1, 3) for item in items},
                {},
                {},
                None,
                None,
                {item.id: normalize_item_name(item.name_raw) for item in items},
            )

    def test_best_effort_rollback_suppresses_secondary_error(self, tmp_path):
        """best_effort_rollback on a closed connection must not raise.

        This is what prevents a failed ROLLBACK from masking the original
        exception in persist_classification_results's except BaseException block.
        """
        from dinary.db.storage import best_effort_rollback

        c = connect(str(tmp_path / "t.db"))
        c.close()
        best_effort_rollback(c, context="test")  # must not raise


@allure.epic("Infrastructure")
@allure.feature("Storage")
class TestConnectRowFactory:
    def test_connection_has_row_factory_set(self, tmp_path):
        """connect() must return a connection with row_factory=sqlite3.Row so
        named column access works without per-call cursor setup."""
        db_path = tmp_path / "test.db"
        con = connect(str(db_path))
        try:
            con.execute("CREATE TABLE t (id INTEGER, name TEXT)")
            con.execute("INSERT INTO t VALUES (1, 'hello')")
            row = con.execute("SELECT id, name FROM t").fetchone()
            assert row["name"] == "hello"
            assert row["id"] == 1
            assert row[0] == 1  # integer indexing must still work
        finally:
            con.close()

    def test_read_only_connection_has_row_factory_set(self, tmp_path):
        """read_only=True path must also set row_factory."""
        db_path = tmp_path / "test_ro.db"
        rw = connect(str(db_path))
        rw.execute("CREATE TABLE t (id INTEGER)")
        rw.execute("INSERT INTO t VALUES (42)")
        rw.close()

        ro = connect(str(db_path), read_only=True)
        try:
            row = ro.execute("SELECT id FROM t").fetchone()
            assert row["id"] == 42
        finally:
            ro.close()

"""Delayed model-quality rating on user category corrections.

A user correction of an llm-created rule rates the model that made it: partial
credit when the corrected-to category was one of the model's own alternatives,
full negative otherwise. Repeated corrections of the same rule rate only once
(the first upsert flips the rule to ``user_correction``).
"""

import shutil
import sqlite3
import unittest.mock

import allure
import pytest

from dinary.api.controllers.expense_corrections import (
    CategoryCorrectionRequest,
    _pending_rating_for_correction,
    correct_category_sync,
    record_correction_ratings,
)
from dinary.db import db_migrations, storage
from dinary.db.classification_rules import RuleSpec, create_or_update_rule


@pytest.fixture
def conn(tmp_path, monkeypatch):
    dst = tmp_path / "dinary.db"
    blank_src = tmp_path / "blank.db"

    def _migration_connect(self, dburi):  # noqa: ARG001
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
    c.execute("INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'Food', 1)")
    c.execute("INSERT INTO categories (id, name, group_id, code) VALUES (1, 'Groceries', 1, 'g')")
    c.execute("INSERT INTO categories (id, name, group_id, code) VALUES (2, 'Drinks', 1, 'd')")
    c.execute("INSERT INTO categories (id, name, group_id, code) VALUES (3, 'Sweets', 1, 's')")
    yield c
    c.close()


def _seed_expense_with_item(
    conn: sqlite3.Connection,
    *,
    name_norm: str,
    category_id: int,
    rule: RuleSpec | None,
) -> int:
    """Insert receipt + receipt_item + expense; optionally pre-create a rule. Returns expense id."""
    conn.execute(
        "INSERT INTO receipts (id, client_receipt_id, url) VALUES (1, 'r1', 'http://x')",
    )
    conn.execute(
        "INSERT INTO expenses"
        " (id, client_expense_id, datetime, amount, amount_original, currency_original,"
        "  category_id, receipt_id, confidence_level)"
        " VALUES (1, 'e1', '2026-01-01T00:00:00', 100, 100, 'RSD', ?, 1, 3)",
        [category_id],
    )
    conn.execute(
        "INSERT INTO receipt_items (id, receipt_id, name_raw, name_normalized, expense_id)"
        " VALUES (1, 1, ?, ?, 1)",
        [name_norm, name_norm],
    )
    if rule is not None:
        create_or_update_rule(conn, None, name_norm, rule)
    return 1


@allure.epic("Review & Rules")
@allure.feature("Model quality")
class TestPendingRatingForCorrection:
    def test_alternative_gets_partial_credit(self, conn):
        create_or_update_rule(
            conn, None, "cola", RuleSpec(1, 3, "llm", alternative_category_ids=(2, 3), llm_name="m")
        )
        assert _pending_rating_for_correction(conn, None, "cola", 2) == ("m", 0.5)

    def test_non_alternative_gets_full_negative(self, conn):
        create_or_update_rule(
            conn, None, "cola", RuleSpec(1, 3, "llm", alternative_category_ids=(3,), llm_name="m")
        )
        assert _pending_rating_for_correction(conn, None, "cola", 2) == ("m", 0.0)

    def test_user_sourced_rule_not_rated(self, conn):
        create_or_update_rule(conn, None, "cola", RuleSpec(1, 3, "user_correction", llm_name="m"))
        assert _pending_rating_for_correction(conn, None, "cola", 2) is None

    def test_llm_rule_without_model_not_rated(self, conn):
        create_or_update_rule(conn, None, "cola", RuleSpec(1, 3, "llm"))
        assert _pending_rating_for_correction(conn, None, "cola", 2) is None

    def test_missing_rule_not_rated(self, conn):
        assert _pending_rating_for_correction(conn, None, "cola", 2) is None


@allure.epic("Review & Rules")
@allure.feature("Model quality")
class TestCorrectCategoryRatings:
    def test_correction_of_llm_rule_records_negative(self, conn):
        _seed_expense_with_item(
            conn,
            name_norm="cola",
            category_id=1,
            rule=RuleSpec(1, 3, "llm", alternative_category_ids=(3,), llm_name="groq"),
        )
        pending: list[tuple[str, float]] = []
        correct_category_sync(
            1, CategoryCorrectionRequest(category_id=2), conn, pending_ratings=pending
        )
        assert pending == [("groq", 0.0)]

    def test_correction_to_alternative_records_partial(self, conn):
        _seed_expense_with_item(
            conn,
            name_norm="cola",
            category_id=1,
            rule=RuleSpec(1, 3, "llm", alternative_category_ids=(2,), llm_name="groq"),
        )
        pending: list[tuple[str, float]] = []
        correct_category_sync(
            1, CategoryCorrectionRequest(category_id=2), conn, pending_ratings=pending
        )
        assert pending == [("groq", 0.5)]

    def test_second_correction_records_nothing(self, conn):
        _seed_expense_with_item(
            conn,
            name_norm="cola",
            category_id=1,
            rule=RuleSpec(1, 3, "llm", alternative_category_ids=(3,), llm_name="groq"),
        )
        first: list[tuple[str, float]] = []
        correct_category_sync(
            1, CategoryCorrectionRequest(category_id=2), conn, pending_ratings=first
        )
        assert first == [("groq", 0.0)]
        # The rule is now source='user_correction'; a second correction rates nothing.
        second: list[tuple[str, float]] = []
        correct_category_sync(
            1, CategoryCorrectionRequest(category_id=3), conn, pending_ratings=second
        )
        assert second == []

    def test_user_sourced_rule_records_nothing(self, conn):
        _seed_expense_with_item(
            conn,
            name_norm="cola",
            category_id=1,
            rule=RuleSpec(1, 4, "user_correction"),
        )
        pending: list[tuple[str, float]] = []
        correct_category_sync(
            1, CategoryCorrectionRequest(category_id=2), conn, pending_ratings=pending
        )
        assert pending == []

    def test_skip_rule_mode_records_nothing(self, conn):
        _seed_expense_with_item(
            conn,
            name_norm="cola",
            category_id=1,
            rule=RuleSpec(1, 3, "llm", alternative_category_ids=(3,), llm_name="groq"),
        )
        pending: list[tuple[str, float]] = []
        correct_category_sync(
            1,
            CategoryCorrectionRequest(category_id=2),
            conn,
            skip_rule=True,
            pending_ratings=pending,
        )
        assert pending == []


@allure.epic("Review & Rules")
@allure.feature("Model quality")
class TestRecordCorrectionRatings:
    def test_records_each_rating(self):
        calls: list[tuple] = []

        class _Broker:
            async def record_quality(self, name, operation, score):
                calls.append((name, operation, score))

        import asyncio

        asyncio.run(record_correction_ratings(_Broker(), [("groq", 0.0), ("openrouter", 0.5)]))
        assert calls == [
            ("groq", "receipt_classification", 0.0),
            ("openrouter", "receipt_classification", 0.5),
        ]

    def test_none_broker_is_noop(self):
        import asyncio

        asyncio.run(record_correction_ratings(None, [("groq", 0.0)]))

    def test_rating_failure_swallowed(self):
        import asyncio

        class _Broker:
            async def record_quality(self, name, operation, score):
                raise RuntimeError("telemetry down")

        # Must not raise.
        asyncio.run(record_correction_ratings(_Broker(), [("groq", 0.0)]))

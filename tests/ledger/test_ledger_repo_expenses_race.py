"""Race-recovery branch in ``insert_expense``: production uses ON CONFLICT DO NOTHING
so this branch never fires naturally, so these tests force it via a bare INSERT and
pin the classifier against real (not fabricated) SQLite error wording."""

import sqlite3
from datetime import datetime

import allure
import pytest

from dinary.db import storage
from dinary.db.expenses import ExpensePayload, insert_expense
from dinary.db import expenses as expenses_mod

from _ledger_repo_helpers import (  # noqa: F401  (autouse + fixtures)
    data_dir,
    fresh_db,
    populated_catalog,
)


@allure.epic("Expenses")
@allure.feature("DB layer")
@allure.story("Race recovery")
class TestInsertExpenseRaceRecovery:
    """Forces the ``_RACE_EXCS`` branch by swapping ``insert_expense.sql`` for a bare
    INSERT (no ON CONFLICT) so a real duplicate key raises ``IntegrityError``."""

    def _insert_winner(self, populated_catalog) -> None:
        """Commit the original ``client_expense_id='race-x'`` row."""
        con = storage.get_connection()
        try:
            result = insert_expense(
                con,
                ExpensePayload(
                    client_expense_id="race-x",
                    expense_datetime=datetime(2026, 5, 5, 12),
                    amount=10.0,
                    amount_original=10.0,
                    currency_original="EUR",
                    category_id=1,
                    comment="winner",
                    tag_ids=[1],
                ),
                enqueue_logging=False,
            )
            assert result == "created"
        finally:
            con.close()

    def _bare_insert_sql(self) -> str:
        """Mirrors ``sql/insert_expense.sql``'s column order minus the ON CONFLICT
        clause, so ``insert_expense`` binds the same parameters but a duplicate
        key actually raises ``IntegrityError``."""
        return (
            "INSERT INTO expenses (client_expense_id, datetime, amount,"
            " amount_original, currency_original, category_id, event_id,"
            " comment, sheet_category, sheet_group)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " RETURNING id"
        )

    def test_duplicate_on_bare_insert_falls_through_to_compare_path(
        self, populated_catalog, monkeypatch
    ):
        """Identical second insert → ``IntegrityError`` → recovery → ``duplicate``."""
        self._insert_winner(populated_catalog)

        original_load_sql = expenses_mod.load_sql
        bare_sql = self._bare_insert_sql()

        def fake_load_sql(name: str) -> str:
            if name == "insert_expense.sql":
                return bare_sql
            return original_load_sql(name)

        monkeypatch.setattr(expenses_mod, "load_sql", fake_load_sql)

        con = storage.get_connection()
        try:
            result = insert_expense(
                con,
                ExpensePayload(
                    client_expense_id="race-x",
                    expense_datetime=datetime(2026, 5, 5, 12),
                    amount=10.0,
                    amount_original=10.0,
                    currency_original="EUR",
                    category_id=1,
                    comment="winner",
                    tag_ids=[1],
                ),
                enqueue_logging=False,
            )
        finally:
            con.close()

        assert result == "duplicate"

    def test_conflict_on_bare_insert_falls_through_to_compare_path(
        self, populated_catalog, monkeypatch
    ):
        """Different amount on second insert → ``IntegrityError`` → recovery → ``conflict``."""
        self._insert_winner(populated_catalog)

        original_load_sql = expenses_mod.load_sql
        bare_sql = self._bare_insert_sql()

        def fake_load_sql(name: str) -> str:
            if name == "insert_expense.sql":
                return bare_sql
            return original_load_sql(name)

        monkeypatch.setattr(expenses_mod, "load_sql", fake_load_sql)

        con = storage.get_connection()
        try:
            result = insert_expense(
                con,
                ExpensePayload(
                    client_expense_id="race-x",
                    expense_datetime=datetime(2026, 5, 5, 12),
                    amount=99.0,
                    amount_original=99.0,
                    currency_original="EUR",
                    category_id=1,
                    comment="loser",
                    tag_ids=[1],
                ),
                enqueue_logging=False,
            )
        finally:
            con.close()

        assert result == "conflict"


@allure.epic("Expenses")
@allure.feature("DB layer")
@allure.story("Race recovery")
class TestIsUniqueViolationOfClientExpenseId:
    """``_is_unique_violation_of_client_expense_id`` decides duplicate/conflict vs.
    500 by pattern-matching SQLite's English error text — these tests pin the real
    wording so a future SQLite release can't silently flip the classifier."""

    def _real_duplicate_key_exception(self) -> sqlite3.Error:
        """Provokes a real ``IntegrityError`` (in-memory throwaway DB) so the message
        is genuine SQLite wording, not hand-crafted."""
        con = sqlite3.connect(":memory:")
        try:
            con.execute(
                "CREATE TABLE expenses (client_expense_id TEXT UNIQUE, v INTEGER)",
            )
            con.execute("INSERT INTO expenses VALUES ('k1', 1)")
            try:
                con.execute("INSERT INTO expenses VALUES ('k1', 2)")
            except sqlite3.Error as exc:
                return exc
            msg = "SQLite unexpectedly accepted a duplicate key"
            raise AssertionError(msg)
        finally:
            con.close()

    def _real_fk_violation_exception(self) -> sqlite3.Error:
        """Same idea for the FK path — real SQLite wording, not a hand-crafted stub."""
        con = sqlite3.connect(":memory:")
        con.execute("PRAGMA foreign_keys=ON")
        try:
            con.execute("CREATE TABLE p (id INTEGER PRIMARY KEY)")
            con.execute(
                "CREATE TABLE c (pid INTEGER REFERENCES p(id))",
            )
            try:
                con.execute("INSERT INTO c VALUES (42)")
            except sqlite3.Error as exc:
                return exc
            msg = "SQLite unexpectedly accepted an FK violation"
            raise AssertionError(msg)
        finally:
            con.close()

    def test_classifies_real_duplicate_key_as_race(self):
        exc = self._real_duplicate_key_exception()
        assert expenses_mod._is_unique_violation_of_client_expense_id(exc), (
            f"SQLite dup-key message no longer matches classifier keywords: {exc!s}"
        )

    def test_classifies_real_fk_violation_as_not_a_race(self):
        exc = self._real_fk_violation_exception()
        assert not expenses_mod._is_unique_violation_of_client_expense_id(exc), (
            f"FK violation was misclassified as a client_expense_id "
            f"UNIQUE race; would be swallowed as a duplicate/conflict "
            f"response instead of surfacing as 500. Message: {exc!s}"
        )

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            # SQLite's canonical dup-key wording; the only form currently
            # produced by the CPython stdlib binding we target.
            (
                "UNIQUE constraint failed: expenses.client_expense_id",
                True,
            ),
            # FK variants must not match even when other tables' unique
            # constraints appear in the same batch — the ``foreign key``
            # carve-out kicks in as soon as the phrase is present.
            (
                "FOREIGN KEY constraint failed",
                False,
            ),
            # Unique-constraint violations on *other* columns must not
            # be misclassified as the client_expense_id race.
            (
                "UNIQUE constraint failed: expenses.id",
                False,
            ),
            (
                "UNIQUE constraint failed: tags.name",
                False,
            ),
            # Unrelated failures must pass through untouched.
            ("disk I/O error", False),
            ("database is locked", False),
        ],
    )
    def test_fixed_message_matrix(self, message: str, expected: bool):
        """Matrix pinning the classifier against unique / FK / unrelated-failure wordings."""
        fake = RuntimeError(message)
        assert expenses_mod._is_unique_violation_of_client_expense_id(fake) is expected, message

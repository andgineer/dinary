"""Race-recovery branch in ``insert_expense`` and the
``_is_unique_violation_of_client_expense_id`` classifier that gates it.

The production path uses ``ON CONFLICT (client_expense_id) DO NOTHING``
so the recovery branch never fires under SQLite's serialized writer;
these tests force it by swapping the loaded SQL for a bare INSERT, and
freeze the engine's actual error wording so the classifier doesn't
silently flip on a future SQLite release.
"""

import sqlite3
from datetime import datetime

import allure
import pytest

from dinary.services import ledger_repo

from _ledger_repo_helpers import (  # noqa: F401  (autouse + fixtures)
    _tmp_data_dir,
    fresh_db,
    populated_catalog,
)


@allure.epic("Ledger repo")
@allure.feature("insert_expense race-recovery branch")
class TestInsertExpenseRaceRecovery:
    """Exercise the ``_RACE_EXCS`` branch inside ``insert_expense``.

    The production SQL (``sql/insert_expense.sql``) uses
    ``ON CONFLICT (client_expense_id) DO NOTHING`` so SQLite silently
    absorbs conflicts with already-committed winners — the
    ``except _RACE_EXCS`` branch never fires on the happy path, and
    the end-to-end concurrency tests in ``tests/test_api.py`` now
    assert that the ``race_counter`` stays at zero under SQLite's
    serialized-writer model.

    That leaves the recovery branch uncovered by regular tests even
    though it stays in the source as a defensive net for any future
    writer module that drops ``ON CONFLICT`` (or for a raw SQL path
    that bubbles the IntegrityError up). This test forces the branch
    by swapping ``load_sql("insert_expense.sql")`` for a bare INSERT
    (no ``ON CONFLICT``) for the duration of the second call, so the
    same code path gets to observe a real ``sqlite3.IntegrityError``
    from a real duplicate key and must land in the compare-path.

    Paired with ``TestIsUniqueViolationOfClientExpenseId`` (which
    pins the message-matching classifier) and the ``sql/insert_expense``
    file itself, this keeps every leg of the race path exercised
    against a real SQLite engine.
    """

    def _insert_winner(self, populated_catalog) -> None:
        """Commit the original ``client_expense_id='race-x'`` row."""
        con = ledger_repo.get_connection()
        try:
            result = ledger_repo.insert_expense(
                con,
                client_expense_id="race-x",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=10.0,
                amount_original=10.0,
                currency_original="EUR",
                category_id=1,
                comment="winner",
                tag_ids=[1],
                enqueue_logging=False,
            )
            assert result == "created"
        finally:
            con.close()

    def _bare_insert_sql(self) -> str:
        """Replacement SQL with no ``ON CONFLICT`` clause.

        Mirrors ``sql/insert_expense.sql`` column shape so
        ``insert_expense`` binds the same 10 parameters in the same
        order; only the conflict-absorption clause is dropped so the
        second call to ``con.execute`` actually raises ``IntegrityError``.
        """
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

        original_load_sql = ledger_repo.load_sql
        bare_sql = self._bare_insert_sql()

        def fake_load_sql(name: str) -> str:
            if name == "insert_expense.sql":
                return bare_sql
            return original_load_sql(name)

        monkeypatch.setattr(ledger_repo, "load_sql", fake_load_sql)

        con = ledger_repo.get_connection()
        try:
            result = ledger_repo.insert_expense(
                con,
                client_expense_id="race-x",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=10.0,
                amount_original=10.0,
                currency_original="EUR",
                category_id=1,
                comment="winner",
                tag_ids=[1],
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

        original_load_sql = ledger_repo.load_sql
        bare_sql = self._bare_insert_sql()

        def fake_load_sql(name: str) -> str:
            if name == "insert_expense.sql":
                return bare_sql
            return original_load_sql(name)

        monkeypatch.setattr(ledger_repo, "load_sql", fake_load_sql)

        con = ledger_repo.get_connection()
        try:
            result = ledger_repo.insert_expense(
                con,
                client_expense_id="race-x",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=99.0,
                amount_original=99.0,
                currency_original="EUR",
                category_id=1,
                comment="loser",
                tag_ids=[1],
                enqueue_logging=False,
            )
        finally:
            con.close()

        assert result == "conflict"


@allure.epic("Ledger repo")
@allure.feature("Race-recovery exception classification")
class TestIsUniqueViolationOfClientExpenseId:
    """Pin the classifier behind ``insert_expense``'s race recovery.

    ``_is_unique_violation_of_client_expense_id`` is the *only* gate
    between "fall through to the compare path" (serve
    duplicate/conflict) and "propagate as 500" (unknown DB failure)
    for the concurrent-POST paths. It decides by pattern-matching the
    engine's English error messages, so a quiet diagnostic-text change
    in a future engine release could silently flip every unique race
    into a 500. These tests freeze the real SQLite error strings from
    the version this code targets so the classifier's contract stays
    observable in CI.

    Paired with
    ``TestInsertExpense::test_unique_client_id_raises_constraint``
    (which pins that the *exception classes* SQLite raises stay the
    set we catch), this covers the full input surface of the recovery
    path without needing to fabricate synthetic exceptions.
    """

    def _real_duplicate_key_exception(self) -> sqlite3.Error:
        """Provoke a real ``IntegrityError`` from SQLite so we freeze
        its actual wording, not a hand-crafted message we invented.
        Uses a throwaway in-memory DB so it doesn't touch the per-test
        tmp DB at all.

        The schema mirrors the ``expenses.client_expense_id`` column
        so the resulting message contains both ``UNIQUE constraint
        failed`` and ``expenses.client_expense_id`` — the two tokens
        the classifier keys on.
        """
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
        """Same idea for the FK path — catch the real SQLite wording
        so we're classifier-against-truth, not
        classifier-against-stub. The classifier's ``"foreign key"``
        carve-out is the discriminator: SQLite's FK violation message
        carries that phrase and none of the duplicate-key positive
        keywords.
        """
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
        assert ledger_repo._is_unique_violation_of_client_expense_id(exc), (
            f"SQLite dup-key message no longer matches classifier keywords: {exc!s}"
        )

    def test_classifies_real_fk_violation_as_not_a_race(self):
        exc = self._real_fk_violation_exception()
        assert not ledger_repo._is_unique_violation_of_client_expense_id(exc), (
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
        """Explicit matrix pinning the classifier against a mix of
        unique / FK / unrelated-failure wordings. Any keyword
        rearrangement in the classifier (dropping the table-qualified
        column match, dropping the ``foreign key`` carve-out, etc.)
        flips at least one row here.
        """
        fake = RuntimeError(message)
        assert ledger_repo._is_unique_violation_of_client_expense_id(fake) is expected, message

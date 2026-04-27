"""``insert_expense`` happy-path + ``client_expense_id`` UNIQUE / FK
guards on a freshly-seeded catalog.

Race-recovery branch coverage lives in
``test_ledger_repo_expenses_race.py``; lookup/get-by-id round-trips in
``test_ledger_repo_expenses_lookup.py``.
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
@allure.feature("Insert expense (client_expense_id)")
class TestInsertExpense:
    def test_insert_then_duplicate(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            r1 = ledger_repo.insert_expense(
                con,
                client_expense_id="x1",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=10.0,
                amount_original=10.0,
                currency_original="EUR",
                category_id=1,
                event_id=None,
                comment="hi",
                sheet_category=None,
                sheet_group=None,
                tag_ids=[1],
                enqueue_logging=False,
            )
            r2 = ledger_repo.insert_expense(
                con,
                client_expense_id="x1",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=10.0,
                amount_original=10.0,
                currency_original="EUR",
                category_id=1,
                event_id=None,
                comment="hi",
                sheet_category=None,
                sheet_group=None,
                tag_ids=[1],
                enqueue_logging=False,
            )
            assert r1 == "created"
            assert r2 == "duplicate"
        finally:
            con.close()

    def test_conflict_on_changed_amount(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            ledger_repo.insert_expense(
                con,
                client_expense_id="x2",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=10.0,
                amount_original=10.0,
                currency_original="EUR",
                category_id=1,
                comment="",
                tag_ids=[],
                enqueue_logging=False,
            )
            r = ledger_repo.insert_expense(
                con,
                client_expense_id="x2",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=99.0,
                amount_original=99.0,
                currency_original="EUR",
                category_id=1,
                comment="",
                tag_ids=[],
                enqueue_logging=False,
            )
            assert r == "conflict"
        finally:
            con.close()

    def test_invalid_category_raises(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            with pytest.raises(ValueError, match="category_id"):
                ledger_repo.insert_expense(
                    con,
                    client_expense_id="x3",
                    expense_datetime=datetime(2026, 5, 5, 12),
                    amount=1.0,
                    amount_original=1.0,
                    currency_original="EUR",
                    category_id=9999,
                    comment="",
                    tag_ids=[],
                )
        finally:
            con.close()

    def test_invalid_provenance_pair_raises(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            with pytest.raises(ValueError, match="sheet_category"):
                ledger_repo.insert_expense(
                    con,
                    client_expense_id="x4",
                    expense_datetime=datetime(2026, 5, 5, 12),
                    amount=1.0,
                    amount_original=1.0,
                    currency_original="EUR",
                    category_id=1,
                    comment="",
                    sheet_category="X",
                    sheet_group=None,
                    tag_ids=[],
                )
        finally:
            con.close()

    def test_enqueue_logging_creates_pending_row(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            ledger_repo.insert_expense(
                con,
                client_expense_id="x5",
                expense_datetime=datetime(2026, 5, 5, 12),
                amount=1.0,
                amount_original=1.0,
                currency_original="EUR",
                category_id=1,
                comment="",
                tag_ids=[],
                enqueue_logging=True,
            )
            jobs = ledger_repo.list_logging_jobs(con)
        finally:
            con.close()
        # Integer expense PKs in the queue.
        assert len(jobs) == 1
        assert isinstance(jobs[0], int)

    def test_null_client_id_allows_multiple(self, populated_catalog):
        """Bootstrap rows use ``client_expense_id=None`` and must coexist."""
        con = ledger_repo.get_connection()
        try:
            r1 = ledger_repo.insert_expense(
                con,
                client_expense_id=None,
                expense_datetime=datetime(2026, 1, 1, 12),
                amount=1.0,
                amount_original=1.0,
                currency_original="EUR",
                category_id=1,
                comment="legacy-1",
                sheet_category="cat",
                sheet_group="grp",
                tag_ids=[],
                enqueue_logging=False,
            )
            r2 = ledger_repo.insert_expense(
                con,
                client_expense_id=None,
                expense_datetime=datetime(2026, 1, 2, 12),
                amount=2.0,
                amount_original=2.0,
                currency_original="EUR",
                category_id=1,
                comment="legacy-2",
                sheet_category="cat",
                sheet_group="grp",
                tag_ids=[],
                enqueue_logging=False,
            )
            assert r1 == "created"
            assert r2 == "created"
            count = con.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
            assert count == 2
        finally:
            con.close()

    def test_unique_client_id_raises_constraint(self, populated_catalog):
        """Direct SQL violation — the wrapper catches and returns
        duplicate/conflict, but the DB enforces UNIQUE regardless."""
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO expenses (client_expense_id, datetime, amount,"
                " amount_original, currency_original, category_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                ["u1", datetime(2026, 1, 1, 12), 1, 1, "EUR", 1],
            )
            con.commit()
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO expenses (client_expense_id, datetime, amount,"
                    " amount_original, currency_original, category_id)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    ["u1", datetime(2026, 1, 1, 12), 1, 1, "EUR", 1],
                )
        finally:
            con.close()

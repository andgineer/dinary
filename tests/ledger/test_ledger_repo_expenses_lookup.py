"""Read-side ``ledger_repo`` paths: ``lookup_existing_expense`` and
``get_expense_by_id`` round-trips against an inserted row.
"""

from datetime import datetime

import allure

from dinary.services import ledger_repo

from _ledger_repo_helpers import (  # noqa: F401  (autouse + fixtures)
    data_dir,
    fresh_db,
    populated_catalog,
)


@allure.epic("Ledger repo")
@allure.feature("lookup_existing_expense")
class TestLookupExistingExpense:
    def test_found(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            ledger_repo.insert_expense(
                con,
                client_expense_id="L1",
                expense_datetime=datetime(2026, 4, 15, 12),
                amount=42.0,
                amount_original=42.0,
                currency_original="EUR",
                category_id=1,
                comment="lunch",
                tag_ids=[],
                enqueue_logging=False,
            )
        finally:
            con.close()

        row = ledger_repo.lookup_existing_expense("L1")
        assert row is not None
        assert row.currency_original == "EUR"
        assert row.category_id == 1
        assert row.comment == "lunch"

    def test_not_found(self, populated_catalog):
        assert ledger_repo.lookup_existing_expense("missing") is None


@allure.epic("Ledger repo")
@allure.feature("get_expense_by_id")
class TestGetExpenseById:
    def test_roundtrip(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            ledger_repo.insert_expense(
                con,
                client_expense_id="E1",
                expense_datetime=datetime(2026, 3, 3, 12),
                amount=1.5,
                amount_original=1.5,
                currency_original="EUR",
                category_id=1,
                comment="c",
                tag_ids=[],
                enqueue_logging=False,
            )
            pk = con.execute(
                "SELECT id FROM expenses WHERE client_expense_id = 'E1'",
            ).fetchone()[0]
            row = ledger_repo.get_expense_by_id(con, int(pk))
        finally:
            con.close()
        assert row is not None
        assert row.category_id == 1
        assert row.currency_original == "EUR"
        assert row.comment == "c"

    def test_missing_returns_none(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            assert ledger_repo.get_expense_by_id(con, 99999) is None
        finally:
            con.close()

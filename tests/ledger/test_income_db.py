"""income table: insert, update, delete, list; currency conversion."""

import sqlite3
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import allure
import pytest

from dinary.api.controllers.income import _convert_to_accounting
from dinary.config import settings
from dinary.db import storage
from dinary.db.income import (
    delete_income,
    get_income_by_year_month,
    insert_income,
    list_incomes,
    update_income,
)

from _ledger_repo_helpers import data_dir, fresh_db  # noqa: F401


@pytest.fixture
def con(fresh_db):  # noqa: F811
    c = storage.get_connection()
    yield c
    c.close()


@allure.epic("Income")
@allure.feature("DB layer")
class TestInsertIncome:
    def test_happy_path(self, con):
        insert_income(con, 2026, 5, 540.0, enqueue_logging=False)
        row = get_income_by_year_month(con, 2026, 5)
        assert row is not None
        assert row.year == 2026
        assert row.month == 5
        assert float(row.amount) == pytest.approx(540.0)

    def test_duplicate_raises_integrity_error(self, con):
        insert_income(con, 2026, 5, 540.0, enqueue_logging=False)
        with pytest.raises(sqlite3.IntegrityError):
            insert_income(con, 2026, 5, 600.0, enqueue_logging=False)

    def test_enqueues_logging_job(self, con):
        insert_income(con, 2026, 5, 540.0, enqueue_logging=True)
        rows = con.execute(
            "SELECT year, month, status FROM income_logging_jobs WHERE year=2026 AND month=5"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][2] == "pending"

    def test_no_logging_when_disabled(self, con):
        insert_income(con, 2026, 5, 540.0, enqueue_logging=False)
        count = con.execute("SELECT COUNT(*) FROM income_logging_jobs").fetchone()[0]
        assert count == 0


@allure.epic("Income")
@allure.feature("DB layer")
class TestUpdateIncome:
    def test_updates_amount(self, con):
        insert_income(con, 2026, 5, 540.0, enqueue_logging=False)
        row = update_income(con, 2026, 5, 600.0, enqueue_logging=False)
        assert float(row.amount) == pytest.approx(600.0)
        stored = get_income_by_year_month(con, 2026, 5)
        assert float(stored.amount) == pytest.approx(600.0)

    def test_raises_if_not_found(self, con):
        with pytest.raises(ValueError, match="not found"):
            update_income(con, 2026, 5, 600.0, enqueue_logging=False)

    def test_upserts_logging_job(self, con):
        insert_income(con, 2026, 5, 540.0, enqueue_logging=False)
        update_income(con, 2026, 5, 600.0, enqueue_logging=True)
        rows = con.execute(
            "SELECT status FROM income_logging_jobs WHERE year=2026 AND month=5"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "pending"


@allure.epic("Income")
@allure.feature("DB layer")
class TestDeleteIncome:
    def test_happy_path(self, con):
        insert_income(con, 2026, 5, 540.0, enqueue_logging=False)
        delete_income(con, 2026, 5)
        assert get_income_by_year_month(con, 2026, 5) is None

    def test_raises_if_not_found(self, con):
        with pytest.raises(ValueError, match="not found"):
            delete_income(con, 2026, 5)

    def test_cascade_deletes_logging_job(self, con):
        insert_income(con, 2026, 5, 540.0, enqueue_logging=True)
        delete_income(con, 2026, 5)
        count = con.execute("SELECT COUNT(*) FROM income_logging_jobs").fetchone()[0]
        assert count == 0


@allure.epic("Income")
@allure.feature("DB layer")
class TestListIncomes:
    def _insert(self, con, rows):
        for year, month, amount in rows:
            insert_income(con, year, month, amount, enqueue_logging=False)

    def test_descending_sort(self, con):
        self._insert(con, [(2025, 1, 100.0), (2026, 3, 200.0), (2026, 1, 150.0)])
        items, _ = list_incomes(con, page=1, page_size=10)
        assert [(r.year, r.month) for r in items] == [(2026, 3), (2026, 1), (2025, 1)]

    def test_has_more_false_when_exact_fit(self, con):
        self._insert(con, [(2026, m, 100.0) for m in range(1, 4)])
        _, has_more = list_incomes(con, page=1, page_size=3)
        assert has_more is False

    def test_has_more_true_when_overflow(self, con):
        self._insert(con, [(2026, m, 100.0) for m in range(1, 5)])
        _, has_more = list_incomes(con, page=1, page_size=3)
        assert has_more is True

    def test_pagination_page_2(self, con):
        self._insert(con, [(2026, m, float(m)) for m in range(1, 5)])
        items_p1, _ = list_incomes(con, page=1, page_size=2)
        items_p2, _ = list_incomes(con, page=2, page_size=2)
        assert len(items_p1) == 2
        assert len(items_p2) == 2
        all_months = [r.month for r in items_p1] + [r.month for r in items_p2]
        assert sorted(all_months, reverse=True) == all_months

    def test_empty(self, con):
        items, has_more = list_incomes(con, page=1, page_size=20)
        assert items == []
        assert has_more is False


@allure.epic("Income")
@allure.feature("Currency conversion")
class TestCurrencyConversion:
    def test_passthrough_when_same_currency(self, con, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        result = _convert_to_accounting(con, Decimal("540"), "EUR", date(2026, 5, 1))
        assert result == pytest.approx(540.0)

    def test_converts_via_rate(self, con, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        with patch("dinary.api.controllers.income.get_rate", return_value=Decimal("0.0085")):
            result = _convert_to_accounting(con, Decimal("1000"), "RSD", date(2026, 5, 1))
        assert result == pytest.approx(8.50, abs=0.01)

"""income table: insert, update, delete, list; currency conversion."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import allure
import pytest

from dinary.api.controllers.income import _convert_to_accounting
from dinary.config import settings
from dinary.db import storage
from dinary.db.income import (
    IncomeData,
    delete_income,
    get_income_by_id,
    get_income_total_for_month,
    insert_income,
    list_incomes,
    update_income,
)

from _ledger_repo_helpers import data_dir, fresh_db  # noqa: F401

_D = date(2026, 5, 15)
_D2 = date(2026, 5, 20)


@pytest.fixture
def con(fresh_db):  # noqa: F811
    c = storage.get_connection()
    yield c
    c.close()


def _data(
    income_date=_D, amount=540.0, amount_original=540.0, currency_original="EUR", comment=None
):
    return IncomeData(
        year=income_date.year,
        month=income_date.month,
        income_date=income_date,
        amount=amount,
        amount_original=amount_original,
        currency_original=currency_original,
        comment=comment,
    )


def _insert(con, income_date=_D, **kwargs):
    return insert_income(
        con,
        _data(income_date=income_date, **kwargs),
        enqueue_logging=False,
    )


@allure.epic("Income")
@allure.feature("DB layer")
class TestInsertIncome:
    def test_happy_path(self, con):
        row = _insert(con)
        assert row.id > 0
        assert row.year == 2026
        assert row.month == 5
        assert row.income_date == _D
        assert float(row.amount) == pytest.approx(540.0)
        assert float(row.amount_original) == pytest.approx(540.0)
        assert row.currency_original == "EUR"
        assert row.comment is None

    def test_returns_inserted_row_with_id(self, con):
        row = _insert(con)
        fetched = get_income_by_id(con, row.id)
        assert fetched is not None
        assert fetched.id == row.id

    def test_multiple_records_same_month_allowed(self, con):
        _insert(con, income_date=_D)
        _insert(con, income_date=_D2)
        total = con.execute("SELECT COUNT(*) FROM income WHERE year=2026 AND month=5").fetchone()[0]
        assert total == 2

    def test_stores_comment(self, con):
        row = _insert(con, comment="salary")
        fetched = get_income_by_id(con, row.id)
        assert fetched.comment == "salary"

    def test_enqueues_logging_job(self, con):
        insert_income(con, _data(), enqueue_logging=True)
        rows = con.execute(
            "SELECT year, month, status FROM income_logging_jobs WHERE year=2026 AND month=5"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][2] == "pending"

    def test_no_logging_when_disabled(self, con):
        _insert(con)
        count = con.execute("SELECT COUNT(*) FROM income_logging_jobs").fetchone()[0]
        assert count == 0


@allure.epic("Income")
@allure.feature("DB layer")
class TestUpdateIncome:
    def test_updates_all_fields(self, con):
        row = _insert(con)
        new_date = date(2026, 5, 25)
        updated = update_income(
            con,
            row.id,
            IncomeData(
                year=2026,
                month=5,
                income_date=new_date,
                amount=600.0,
                amount_original=700.0,
                currency_original="RSD",
                comment="updated",
            ),
            enqueue_logging=False,
        )
        assert float(updated.amount) == pytest.approx(600.0)
        assert float(updated.amount_original) == pytest.approx(700.0)
        assert updated.currency_original == "RSD"
        assert updated.income_date == new_date
        assert updated.comment == "updated"

    def test_raises_if_not_found(self, con):
        with pytest.raises(ValueError, match="not found"):
            update_income(con, 9999, _data(), enqueue_logging=False)

    def test_upserts_logging_job(self, con):
        row = _insert(con)
        update_income(con, row.id, _data(), enqueue_logging=True)
        rows = con.execute(
            "SELECT status FROM income_logging_jobs WHERE year=2026 AND month=5"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "pending"


@allure.epic("Income")
@allure.feature("DB layer")
class TestDeleteIncome:
    def test_happy_path(self, con):
        row = _insert(con)
        delete_income(con, row.id)
        assert get_income_by_id(con, row.id) is None

    def test_raises_if_not_found(self, con):
        with pytest.raises(ValueError, match="not found"):
            delete_income(con, 9999)

    def test_clears_logging_job_when_last_record_deleted(self, con):
        row = insert_income(con, _data(), enqueue_logging=True)
        delete_income(con, row.id)
        count = con.execute("SELECT COUNT(*) FROM income_logging_jobs").fetchone()[0]
        assert count == 0

    def test_keeps_logging_job_when_other_records_remain(self, con):
        r1 = _insert(con, income_date=_D)
        insert_income(
            con,
            _data(income_date=_D2, amount=400.0, amount_original=400.0),
            enqueue_logging=True,
        )
        delete_income(con, r1.id)
        count = con.execute("SELECT COUNT(*) FROM income_logging_jobs").fetchone()[0]
        assert count == 1


@allure.epic("Income")
@allure.feature("DB layer")
class TestListIncomes:
    def test_descending_date_sort(self, con):
        for d in [date(2025, 1, 15), date(2026, 3, 10), date(2026, 1, 5)]:
            insert_income(con, _data(income_date=d), enqueue_logging=False)
        items, _ = list_incomes(con, page=1, page_size=10)
        assert [r.income_date for r in items] == sorted(
            [r.income_date for r in items], reverse=True
        )

    def test_has_more_false_when_exact_fit(self, con):
        for d in [date(2026, m, 1) for m in range(1, 4)]:
            insert_income(con, _data(income_date=d), enqueue_logging=False)
        _, has_more = list_incomes(con, page=1, page_size=3)
        assert has_more is False

    def test_has_more_true_when_overflow(self, con):
        for d in [date(2026, m, 1) for m in range(1, 5)]:
            insert_income(con, _data(income_date=d), enqueue_logging=False)
        _, has_more = list_incomes(con, page=1, page_size=3)
        assert has_more is True

    def test_empty(self, con):
        items, has_more = list_incomes(con, page=1, page_size=20)
        assert items == []
        assert has_more is False


@allure.epic("Income")
@allure.feature("DB layer")
class TestGetIncomeTotalForMonth:
    def test_sums_multiple_records(self, con):
        insert_income(con, _data(amount=300.0, amount_original=300.0), enqueue_logging=False)
        insert_income(
            con,
            _data(income_date=_D2, amount=200.0, amount_original=200.0),
            enqueue_logging=False,
        )
        total = get_income_total_for_month(con, 2026, 5)
        assert total == pytest.approx(Decimal("500.00"))

    def test_returns_none_when_no_records(self, con):
        assert get_income_total_for_month(con, 2026, 5) is None


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

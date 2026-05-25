"""Income sheet drain: append, update, idempotency, worksheet creation."""

import shutil
from unittest.mock import MagicMock, patch

import allure
import pytest

from dinary.background.sheet_logging import income_sheet_logging
from dinary.background.sheet_logging.income_sheet_logging import drain_income_pending
from dinary.config import settings
from dinary.db import storage
from dinary.db.income import IncomeData, insert_income


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch, blank_db):
    shutil.copy(blank_db, tmp_path / "dinary.db")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "dinary.db")
    monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "test-ss-id")
    monkeypatch.setattr(settings, "accounting_currency", "EUR")
    monkeypatch.setattr(settings, "app_currency", "EUR")
    from datetime import date

    con = storage.get_connection()
    insert_income(
        con,
        IncomeData(
            year=2026,
            month=5,
            income_date=date(2026, 5, 15),
            amount=540.0,
            amount_original=540.0,
            currency_original="EUR",
        ),
        enqueue_logging=True,
    )
    con.close()
    income_sheet_logging._reset_backoff()


def _make_ws(all_values=None):
    ws = MagicMock()
    ws.get_all_values.return_value = all_values or [
        ["Date", "Amount", "EUR", "Rate", "Month", "Key"]
    ]
    ws.batch_get.return_value = [[[""]]]
    return ws


def _make_ss(ws=None, has_income_ws=True):
    ss = MagicMock()
    income_ws = ws or _make_ws()
    income_ws.title = "Income"
    if has_income_ws:
        ss.worksheets.return_value = [income_ws]
    else:
        ss.worksheets.return_value = []
        ss.add_worksheet.return_value = income_ws
    return ss, income_ws


@allure.epic("Income")
@allure.feature("Sheets")
class TestIncomeDrain:
    def test_append_new_row(self):
        ss, ws = _make_ss()
        ws.get_all_values.return_value = [["Date", "Amount", "EUR", "Rate", "Month", "Key"]]
        with patch(
            "dinary.background.sheet_logging.income_sheet_logging.fetch_row_years",
            return_value=[None],
        ):
            with patch(
                "dinary.background.sheet_logging.income_sheet_logging.get_sheet", return_value=ss
            ):
                result = drain_income_pending()
        ws.append_row.assert_called_once()
        assert result.get("appended", 0) == 1

    def test_update_existing_row(self):
        existing = ["2026-05-01", "500", '=IF(D2="","",B2/D2)', "1.0", "5", "2026-5-old"]
        ss, ws = _make_ss()
        ws.get_all_values.return_value = [
            ["Date", "Amount", "EUR", "Rate", "Month", "Key"],
            existing,
        ]
        with patch(
            "dinary.background.sheet_logging.income_sheet_logging.fetch_row_years",
            return_value=[None, 2026],
        ):
            with patch(
                "dinary.background.sheet_logging.income_sheet_logging.get_sheet", return_value=ss
            ):
                result = drain_income_pending()
        ws.batch_update.assert_called()
        assert result.get("updated", 0) == 1

    def test_idempotency_skip(self):
        existing = ["2026-05-01", "540.0", '=IF(D2="","",B2/D2)', "1.0", "5", "2026-5"]
        ss, ws = _make_ss()
        ws.get_all_values.return_value = [
            ["Date", "Amount", "EUR", "Rate", "Month", "Key"],
            existing,
        ]
        with patch(
            "dinary.background.sheet_logging.income_sheet_logging.fetch_row_years",
            return_value=[None, 2026],
        ):
            with patch(
                "dinary.background.sheet_logging.income_sheet_logging.get_sheet", return_value=ss
            ):
                result = drain_income_pending()
        ws.append_row.assert_not_called()
        assert result.get("skipped", 0) == 1

    def test_creates_worksheet_if_absent(self):
        ss, ws = _make_ss(has_income_ws=False)
        ws.get_all_values.return_value = [["Date", "Amount", "EUR", "Rate", "Month", "Key"]]
        with patch(
            "dinary.background.sheet_logging.income_sheet_logging.fetch_row_years",
            return_value=[None],
        ):
            with patch(
                "dinary.background.sheet_logging.income_sheet_logging.get_sheet", return_value=ss
            ):
                drain_income_pending()
        ss.add_worksheet.assert_called_once()

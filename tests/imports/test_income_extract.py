"""Tests for income_extract: individual-record extraction from sheets."""

import json
from unittest.mock import MagicMock, patch

import allure
import pytest

import tasks.imports.income_extract as _mod
from dinary.config import ImportSourceRow
from tasks.imports.income_extract import (
    _predict_income_month,
    export_to_file,
    extract_all_years,
    extract_income_records,
)
from tasks.imports.income_import import INCOME_LAYOUTS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ws(rows: list[list[str]]) -> MagicMock:
    ws = MagicMock()
    ws.get_all_values.return_value = rows
    return ws


def _ss(ws: MagicMock) -> MagicMock:
    ss = MagicMock()
    ss.worksheet.return_value = ws
    return ss


# ---------------------------------------------------------------------------
# _predict_income_month
# ---------------------------------------------------------------------------


@allure.epic("Income")
@allure.feature("Import")
class TestPredictIncomeMonth:
    def test_day_25_is_prev_month(self):
        assert _predict_income_month(2024, 3, 25) == (2024, 2)

    def test_day_1_is_prev_month(self):
        assert _predict_income_month(2024, 5, 1) == (2024, 4)

    def test_day_26_is_current_month(self):
        assert _predict_income_month(2024, 3, 26) == (2024, 3)

    def test_day_31_is_current_month(self):
        assert _predict_income_month(2024, 1, 31) == (2024, 1)

    def test_january_day_le_25_wraps_to_december_prev_year(self):
        assert _predict_income_month(2024, 1, 10) == (2023, 12)

    def test_january_day_26_stays_january(self):
        assert _predict_income_month(2024, 1, 26) == (2024, 1)


# ---------------------------------------------------------------------------
# extract_income_records
# ---------------------------------------------------------------------------


@allure.epic("Income")
@allure.feature("Import")
class TestExtractIncomeRecords:
    def test_returns_separate_record_per_row(self):
        layout = INCOME_LAYOUTS["balance_rub"]
        rows = [
            ["date", "amount"],
            ["2019-01-15", "50000"],
            ["2019-01-20", "35000"],
        ]
        ss = _ss(_ws(rows))
        with patch("tasks.imports.income_extract.get_sheet", return_value=ss):
            result = extract_income_records(2019, "sid", "Income", layout)

        assert len(result) == 2
        assert result[0]["amount"] == "50000"
        assert result[1]["amount"] == "35000"

    def test_record_fields(self):
        layout = INCOME_LAYOUTS["balance_rub"]
        rows = [["date", "amount"], ["2019-03-10", "90000"]]
        ss = _ss(_ws(rows))
        with patch("tasks.imports.income_extract.get_sheet", return_value=ss):
            result = extract_income_records(2019, "sid", "Income", layout)

        rec = result[0]
        assert rec["year"] == 2019
        assert rec["month"] == 3
        assert rec["day"] == 10
        assert rec["currency"] == "RUB"
        assert rec["income_year"] == 2019
        assert rec["income_month"] == 2  # day 10 <= 25 -> prev month

    def test_day_26_maps_to_current_month(self):
        layout = INCOME_LAYOUTS["balance_rsd"]
        rows = [["date", "amount"], ["2023-05-26", "120000"]]
        ss = _ss(_ws(rows))
        with patch("tasks.imports.income_extract.get_sheet", return_value=ss):
            result = extract_income_records(2023, "sid", "Income", layout)

        assert result[0]["income_month"] == 5
        assert result[0]["income_year"] == 2023

    def test_transition_layout_currency(self):
        layout = INCOME_LAYOUTS["balance_rub_rsd"]
        rows = [
            ["date", "amount"],
            ["2022-07-10", "60000"],
            ["2022-08-10", "120000"],
        ]
        ss = _ss(_ws(rows))
        with patch("tasks.imports.income_extract.get_sheet", return_value=ss):
            result = extract_income_records(2022, "sid", "Balance", layout)

        assert result[0]["currency"] == "RUB"
        assert result[1]["currency"] == "RSD"

    def test_skips_rows_from_other_years(self):
        layout = INCOME_LAYOUTS["balance_rub"]
        rows = [
            ["date", "amount"],
            ["2019-06-01", "10000"],
            ["2020-06-01", "99999"],
        ]
        ss = _ss(_ws(rows))
        with patch("tasks.imports.income_extract.get_sheet", return_value=ss):
            result = extract_income_records(2019, "sid", "Balance", layout)

        assert len(result) == 1
        assert result[0]["month"] == 6

    def test_skips_blank_and_zero_amounts(self):
        layout = INCOME_LAYOUTS["balance_rsd"]
        rows = [
            ["date", "amount"],
            ["2023-03-01", ""],
            ["2023-03-05", "0"],
            ["2023-03-10", "75000"],
        ]
        ss = _ss(_ws(rows))
        with patch("tasks.imports.income_extract.get_sheet", return_value=ss):
            result = extract_income_records(2023, "sid", "Income", layout)

        assert len(result) == 1
        assert result[0]["amount"] == "75000"

    def test_skips_unparseable_dates(self):
        layout = INCOME_LAYOUTS["balance_rsd"]
        rows = [
            ["date", "amount"],
            ["not-a-date", "50000"],
            ["2023-04-01", "50000"],
        ]
        ss = _ss(_ws(rows))
        with patch("tasks.imports.income_extract.get_sheet", return_value=ss):
            result = extract_income_records(2023, "sid", "Income", layout)

        assert len(result) == 1
        assert result[0]["month"] == 4


# ---------------------------------------------------------------------------
# extract_all_years
# ---------------------------------------------------------------------------


@allure.epic("Income")
@allure.feature("Import")
class TestExtractAllYears:
    @pytest.fixture(autouse=True)
    def _stub_sources(self, monkeypatch):
        sources = [
            ImportSourceRow(
                year=2021,
                spreadsheet_id="sid21",
                worksheet_name="Budget",
                income_worksheet_name="Balance",
                income_layout_key="balance_rub",
            ),
            ImportSourceRow(
                year=2023,
                spreadsheet_id="sid23",
                worksheet_name="Budget",
                income_worksheet_name="Income",
                income_layout_key="balance_rsd",
            ),
            ImportSourceRow(
                year=2025,
                spreadsheet_id="sid25",
                worksheet_name="Budget",
                income_worksheet_name="",
                income_layout_key="",
            ),
        ]
        monkeypatch.setattr(_mod, "read_import_sources", lambda: list(sources))

    def _make_get_sheet(self, data_by_sid: dict[str, list[list[str]]]):
        def _get_sheet(spreadsheet_id: str):
            return _ss(_ws(data_by_sid[spreadsheet_id]))

        return _get_sheet

    def test_returns_entries_for_years_with_income(self):
        data = {
            "sid21": [["date", "amount"], ["2021-05-10", "90000"]],
            "sid23": [["date", "amount"], ["2023-06-10", "150000"]],
        }
        with patch(
            "tasks.imports.income_extract.get_sheet", side_effect=self._make_get_sheet(data)
        ):
            entries = extract_all_years()

        assert len(entries) == 2
        years = {e["year"] for e in entries}
        assert years == {2021, 2023}

    def test_skips_year_without_income_worksheet(self):
        data = {
            "sid21": [["date", "amount"], ["2021-01-01", "50000"]],
            "sid23": [["date", "amount"], ["2023-01-01", "100000"]],
        }
        with patch(
            "tasks.imports.income_extract.get_sheet", side_effect=self._make_get_sheet(data)
        ):
            entries = extract_all_years()

        assert all(e["year"] != 2025 for e in entries)

    def test_sheet_failure_skips_year_continues(self):
        call_count = 0

        def _get_sheet(spreadsheet_id: str):
            nonlocal call_count
            call_count += 1
            if spreadsheet_id == "sid21":
                raise OSError("network error")
            return _ss(_ws([["date", "amount"], ["2023-03-01", "120000"]]))

        with patch("tasks.imports.income_extract.get_sheet", side_effect=_get_sheet):
            entries = extract_all_years()

        assert call_count == 2
        assert all(e["year"] != 2021 for e in entries)
        assert any(e["year"] == 2023 for e in entries)

    def test_entries_sorted_by_month_then_day_within_year(self):
        data = {
            "sid21": [
                ["date", "amount"],
                ["2021-03-15", "1000"],
                ["2021-01-20", "2000"],
                ["2021-01-05", "3000"],
            ],
            "sid23": [["date", "amount"], ["2023-02-01", "4000"]],
        }
        with patch(
            "tasks.imports.income_extract.get_sheet", side_effect=self._make_get_sheet(data)
        ):
            entries = extract_all_years()

        yr21 = [e for e in entries if e["year"] == 2021]
        assert yr21[0]["month"] == 1
        assert yr21[0]["day"] == 5
        assert yr21[1]["month"] == 1
        assert yr21[1]["day"] == 20
        assert yr21[2]["month"] == 3


# ---------------------------------------------------------------------------
# export_to_file
# ---------------------------------------------------------------------------


@allure.epic("Income")
@allure.feature("Import")
class TestExportToFile:
    def test_writes_valid_json_with_all_fields(self, tmp_path, monkeypatch):
        sources = [
            ImportSourceRow(
                year=2024,
                spreadsheet_id="sid24",
                worksheet_name="Budget",
                income_worksheet_name="Income",
                income_layout_key="income_rsd",
            ),
        ]
        monkeypatch.setattr(_mod, "read_import_sources", lambda: list(sources))
        ws = _ws([["date", "amount"], ["2024-04-10", "300000"]])
        ss = _ss(ws)
        with patch("tasks.imports.income_extract.get_sheet", return_value=ss):
            dest = tmp_path / "out.json"
            count = export_to_file(dest)

        assert count == 1
        payload = json.loads(dest.read_text())
        assert "generated_at" in payload
        entry = payload["entries"][0]
        assert entry["year"] == 2024
        assert entry["month"] == 4
        assert entry["day"] == 10
        assert entry["currency"] == "RSD"
        assert entry["income_year"] == 2024
        assert entry["income_month"] == 3  # day 10 <= 25 -> prev month

    def test_creates_parent_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "read_import_sources", lambda: [])
        dest = tmp_path / "nested" / "dir" / "income.json"
        export_to_file(dest)
        assert dest.exists()

    def test_amount_is_decimal_string(self, tmp_path, monkeypatch):
        sources = [
            ImportSourceRow(
                year=2023,
                spreadsheet_id="sid23",
                worksheet_name="Budget",
                income_worksheet_name="Income",
                income_layout_key="balance_rsd",
            ),
        ]
        monkeypatch.setattr(_mod, "read_import_sources", lambda: list(sources))
        ws = _ws([["date", "amount"], ["2023-01-10", "123456.78"]])
        ss = _ss(ws)
        with patch("tasks.imports.income_extract.get_sheet", return_value=ss):
            dest = tmp_path / "out.json"
            export_to_file(dest)

        payload = json.loads(dest.read_text())
        amount = payload["entries"][0]["amount"]
        assert isinstance(amount, str)
        assert "." in amount

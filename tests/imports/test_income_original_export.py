"""Tests for income_original_export: original-currency extraction from sheets."""

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

import tasks.imports.income_original_export as _income_export_mod
from dinary.config import ImportSourceRow
from tasks.imports.income_original_export import (
    aggregate_original_currency,
    export_to_file,
    extract_all_years,
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
# aggregate_original_currency
# ---------------------------------------------------------------------------


class TestAggregateOriginalCurrency:
    def test_rub_layout_sums_by_month(self):
        layout = INCOME_LAYOUTS["balance_rub"]
        rows = [
            ["date", "amount"],  # header
            ["2019-01-15", "50000"],
            ["2019-01-20", "35000"],
            ["2019-02-15", "85000"],
        ]
        ss = _ss(_ws(rows))
        with patch("tasks.imports.income_original_export.get_sheet", return_value=ss):
            result = aggregate_original_currency(2019, "sid", "Income", layout)

        assert result == {(1, "RUB"): Decimal("85000"), (2, "RUB"): Decimal("85000")}

    def test_transition_layout_splits_currency_at_month(self):
        layout = INCOME_LAYOUTS["balance_rub_rsd"]
        rows = [
            ["date", "amount"],
            ["2022-07-10", "60000"],  # month 7 < transition_month(8) → RUB
            ["2022-08-10", "120000"],  # month 8 >= transition_month(8) → RSD
            ["2022-09-10", "130000"],  # month 9 → RSD
        ]
        ss = _ss(_ws(rows))
        with patch("tasks.imports.income_original_export.get_sheet", return_value=ss):
            result = aggregate_original_currency(2022, "sid", "Balance", layout)

        assert result == {
            (7, "RUB"): Decimal("60000"),
            (8, "RSD"): Decimal("120000"),
            (9, "RSD"): Decimal("130000"),
        }

    def test_skips_rows_from_other_years(self):
        layout = INCOME_LAYOUTS["balance_rub"]
        rows = [
            ["date", "amount"],
            ["2019-06-01", "10000"],
            ["2020-06-01", "99999"],  # different year — must be excluded
        ]
        ss = _ss(_ws(rows))
        with patch("tasks.imports.income_original_export.get_sheet", return_value=ss):
            result = aggregate_original_currency(2019, "sid", "Balance", layout)

        assert result == {(6, "RUB"): Decimal("10000")}

    def test_skips_blank_and_zero_amounts(self):
        layout = INCOME_LAYOUTS["balance_rsd"]
        rows = [
            ["date", "amount"],
            ["2023-03-01", ""],
            ["2023-03-05", "0"],
            ["2023-03-10", "75000"],
        ]
        ss = _ss(_ws(rows))
        with patch("tasks.imports.income_original_export.get_sheet", return_value=ss):
            result = aggregate_original_currency(2023, "sid", "Income", layout)

        assert result == {(3, "RSD"): Decimal("75000")}

    def test_skips_unparseable_dates(self):
        layout = INCOME_LAYOUTS["balance_rsd"]
        rows = [
            ["date", "amount"],
            ["not-a-date", "50000"],
            ["2023-04-01", "50000"],
        ]
        ss = _ss(_ws(rows))
        with patch("tasks.imports.income_original_export.get_sheet", return_value=ss):
            result = aggregate_original_currency(2023, "sid", "Income", layout)

        assert result == {(4, "RSD"): Decimal("50000")}

    def test_rsd_layout_no_transition(self):
        layout = INCOME_LAYOUTS["income_rsd"]
        rows = [
            ["date", "amount"],
            ["2024-11-01", "200000"],
            ["2024-12-01", "210000"],
        ]
        ss = _ss(_ws(rows))
        with patch("tasks.imports.income_original_export.get_sheet", return_value=ss):
            result = aggregate_original_currency(2024, "sid", "Income", layout)

        assert result == {(11, "RSD"): Decimal("200000"), (12, "RSD"): Decimal("210000")}


# ---------------------------------------------------------------------------
# extract_all_years
# ---------------------------------------------------------------------------


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
                income_worksheet_name="",  # no income — must be skipped
                income_layout_key="",
            ),
        ]
        monkeypatch.setattr(_income_export_mod, "read_import_sources", lambda: list(sources))

    def _make_get_sheet(self, data_by_sid: dict[str, list[list[str]]]):
        def _get_sheet(spreadsheet_id: str):
            return _ss(_ws(data_by_sid[spreadsheet_id]))

        return _get_sheet

    def test_returns_entries_for_years_with_income(self):
        data = {
            "sid21": [["date", "amount"], ["2021-05-01", "90000"]],
            "sid23": [["date", "amount"], ["2023-06-01", "150000"]],
        }
        with patch(
            "tasks.imports.income_original_export.get_sheet",
            side_effect=self._make_get_sheet(data),
        ):
            entries = extract_all_years()

        assert len(entries) == 2
        assert {"year": 2021, "month": 5, "amount": "90000", "currency": "RUB"} in entries
        assert {"year": 2023, "month": 6, "amount": "150000", "currency": "RSD"} in entries

    def test_skips_year_without_income_worksheet(self):
        data = {
            2021: [["date", "amount"], ["2021-01-01", "50000"]],
            2023: [["date", "amount"], ["2023-01-01", "100000"]],
        }
        with patch(
            "tasks.imports.income_original_export.get_sheet",
            side_effect=self._make_get_sheet(data),
        ):
            entries = extract_all_years()

        years = {e["year"] for e in entries}
        assert 2025 not in years

    def test_sheet_failure_skips_year_continues(self, monkeypatch):
        call_count = 0

        def _get_sheet(spreadsheet_id: str):
            nonlocal call_count
            call_count += 1
            if spreadsheet_id == "sid21":
                raise OSError("network error")
            return _ss(_ws([["date", "amount"], ["2023-03-01", "120000"]]))

        with patch("tasks.imports.income_original_export.get_sheet", side_effect=_get_sheet):
            entries = extract_all_years()

        assert call_count == 2
        years = {e["year"] for e in entries}
        assert 2021 not in years
        assert 2023 in years

    def test_entries_sorted_by_year_then_month(self):
        data = {
            "sid21": [
                ["date", "amount"],
                ["2021-03-01", "1000"],
                ["2021-01-01", "2000"],
            ],
            "sid23": [["date", "amount"], ["2023-02-01", "3000"]],
        }
        with patch(
            "tasks.imports.income_original_export.get_sheet",
            side_effect=self._make_get_sheet(data),
        ):
            entries = extract_all_years()

        assert [e["year"] for e in entries] == [2021, 2021, 2023]
        assert entries[0]["month"] == 1
        assert entries[1]["month"] == 3


# ---------------------------------------------------------------------------
# export_to_file
# ---------------------------------------------------------------------------


class TestExportToFile:
    def test_writes_valid_json(self, tmp_path, monkeypatch):
        sources = [
            ImportSourceRow(
                year=2024,
                spreadsheet_id="sid24",
                worksheet_name="Budget",
                income_worksheet_name="Income",
                income_layout_key="income_rsd",
            ),
        ]
        monkeypatch.setattr(_income_export_mod, "read_import_sources", lambda: list(sources))

        ws = _ws([["date", "amount"], ["2024-04-01", "300000"]])
        ss = _ss(ws)
        with patch("tasks.imports.income_original_export.get_sheet", return_value=ss):
            dest = tmp_path / "out.json"
            count = export_to_file(dest)

        assert count == 1
        payload = json.loads(dest.read_text())
        assert "generated_at" in payload
        assert payload["entries"] == [
            {"year": 2024, "month": 4, "amount": "300000", "currency": "RSD"}
        ]

    def test_creates_parent_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_income_export_mod, "read_import_sources", lambda: [])
        dest = tmp_path / "nested" / "dir" / "income.json"
        export_to_file(dest)
        assert dest.exists()

    def test_amount_is_decimal_string_not_float(self, tmp_path, monkeypatch):
        sources = [
            ImportSourceRow(
                year=2023,
                spreadsheet_id="sid23",
                worksheet_name="Budget",
                income_worksheet_name="Income",
                income_layout_key="balance_rsd",
            ),
        ]
        monkeypatch.setattr(_income_export_mod, "read_import_sources", lambda: list(sources))

        ws = _ws([["date", "amount"], ["2023-01-01", "123456.78"]])
        ss = _ss(ws)
        with patch("tasks.imports.income_original_export.get_sheet", return_value=ss):
            dest = tmp_path / "out.json"
            export_to_file(dest)

        payload = json.loads(dest.read_text())
        amount = payload["entries"][0]["amount"]
        assert isinstance(amount, str)
        assert "." in amount

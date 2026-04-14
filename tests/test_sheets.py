"""Tests for sheets.py service — _find_month_block, load_categories, write_expense."""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dinary.services.category_store import Category


def _make_worksheet(all_values, col_a=None):
    """Create a mock gspread.Worksheet from a 2D list of values."""
    ws = MagicMock()
    ws.get_all_values.return_value = all_values
    if col_a is None:
        col_a = [row[0] if row else "" for row in all_values]
    ws.col_values.return_value = col_a
    return ws


SAMPLE_SHEET = [
    ["2026-03", "", "", "", "", "", "117.00"],
    ["", "Food", "Essentials", "5000", "42.74", "", ""],
    ["", "Transport", "Essentials", "2000", "17.09", "", ""],
    ["", "Cinema", "Entertainment", "1000", "8.55", "", ""],
    ["2026-04", "", "", "", "", "", ""],
    ["", "Food", "Essentials", "0", "0", "", ""],
    ["", "Transport", "Essentials", "0", "0", "", ""],
    ["", "Cinema", "Entertainment", "0", "0", "", ""],
]


class TestFindMonthBlock:
    def test_finds_existing_block(self):
        from dinary.services.sheets import _find_month_block

        ws = _make_worksheet(SAMPLE_SHEET)
        result = _find_month_block(ws, date(2026, 3, 15))
        assert result == (1, 5)  # rows 1..4 (1-indexed, end exclusive)

    def test_finds_last_block(self):
        from dinary.services.sheets import _find_month_block

        ws = _make_worksheet(SAMPLE_SHEET)
        result = _find_month_block(ws, date(2026, 4, 1))
        assert result == (5, 9)  # rows 5..8

    def test_returns_none_for_missing_month(self):
        from dinary.services.sheets import _find_month_block

        ws = _make_worksheet(SAMPLE_SHEET)
        result = _find_month_block(ws, date(2026, 5, 1))
        assert result is None


class TestLoadCategories:
    @patch("dinary.services.sheets._get_sheet")
    def test_loads_from_first_block(self, mock_get_sheet):
        from dinary.services.sheets import load_categories

        ws = _make_worksheet(SAMPLE_SHEET)
        cats = load_categories(ws)

        assert len(cats) == 3
        assert cats[0] == Category(name="Food", group="Essentials")
        assert cats[1] == Category(name="Transport", group="Essentials")
        assert cats[2] == Category(name="Cinema", group="Entertainment")

    @patch("dinary.services.sheets._get_sheet")
    def test_empty_sheet(self, mock_get_sheet):
        from dinary.services.sheets import load_categories

        ws = _make_worksheet([])
        cats = load_categories(ws)
        assert cats == []


class TestWriteExpense:
    @pytest.mark.anyio
    @patch("dinary.services.sheets.fetch_eur_rsd_rate", new_callable=AsyncMock)
    @patch("dinary.services.sheets._get_sheet")
    async def test_writes_to_correct_row(self, mock_get_sheet, mock_rate):
        from dinary.services.sheets import write_expense

        mock_rate.return_value = Decimal("117.32")

        ws = MagicMock()
        mock_get_sheet.return_value.sheet1 = ws

        col_a = [row[0] for row in SAMPLE_SHEET]
        ws.col_values.return_value = col_a
        ws.get.return_value = [
            ["2026-04", "", "", "", "", "", ""],
            ["", "Food", "Essentials", "1000", "8.53", "", ""],
            ["", "Transport", "Essentials", "0", "0", "", ""],
            ["", "Cinema", "Entertainment", "0", "0", "", ""],
        ]

        result = await write_expense(
            amount_rsd=500.0,
            category="Food",
            comment="test",
            expense_date=date(2026, 4, 14),
        )

        assert result["category"] == "Food"
        assert result["amount_rsd"] == 500.0
        assert result["new_total_rsd"] == 1500.0

        ws.update_cell.assert_any_call(6, 4, "1500.00")
        ws.update_cell.assert_any_call(5, 7, str(Decimal("117.32")))

    @pytest.mark.anyio
    @patch("dinary.services.sheets.fetch_eur_rsd_rate", new_callable=AsyncMock)
    @patch("dinary.services.sheets._get_sheet")
    async def test_uses_existing_rate(self, mock_get_sheet, mock_rate):
        from dinary.services.sheets import write_expense

        ws = MagicMock()
        mock_get_sheet.return_value.sheet1 = ws

        col_a = [row[0] for row in SAMPLE_SHEET]
        ws.col_values.return_value = col_a
        ws.get.return_value = [
            ["2026-04", "", "", "", "", "", "117.50"],
            ["", "Food", "Essentials", "0", "0", "", ""],
            ["", "Transport", "Essentials", "0", "0", "", ""],
            ["", "Cinema", "Entertainment", "0", "0", "", ""],
        ]

        await write_expense(
            amount_rsd=1000.0,
            category="Food",
            comment="",
            expense_date=date(2026, 4, 14),
        )

        mock_rate.assert_not_called()

    @pytest.mark.anyio
    @patch("dinary.services.sheets.fetch_eur_rsd_rate", new_callable=AsyncMock)
    @patch("dinary.services.sheets._get_sheet")
    async def test_unknown_category_raises(self, mock_get_sheet, mock_rate):
        from dinary.services.sheets import write_expense

        ws = MagicMock()
        mock_get_sheet.return_value.sheet1 = ws

        col_a = [row[0] for row in SAMPLE_SHEET]
        ws.col_values.return_value = col_a
        ws.get.return_value = [
            ["2026-04", "", "", "", "", "", "117.50"],
            ["", "Food", "Essentials", "0", "0", "", ""],
        ]

        with pytest.raises(ValueError, match="Category 'Unknown'"):
            await write_expense(
                amount_rsd=100.0,
                category="Unknown",
                comment="",
                expense_date=date(2026, 4, 14),
            )


class TestCreateMonthBlock:
    @patch("dinary.services.sheets._get_sheet")
    def test_creates_block_from_template(self, mock_get_sheet):
        from dinary.services.sheets import _create_month_block

        ws = MagicMock()
        ws.get_all_values.return_value = list(SAMPLE_SHEET)

        start, end = _create_month_block(ws, date(2026, 5, 1))

        assert start == 9  # after 8 existing rows
        assert end == 13  # 4 rows in block

        ws.update.assert_called_once()
        call_args = ws.update.call_args
        new_rows = call_args.kwargs["values"]
        assert new_rows[0][0] == "2026-05"
        assert new_rows[1][3] == "0"  # amounts zeroed

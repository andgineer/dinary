"""Tests for sheets.py — flat-table layout with formula columns."""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dinary.services.category_store import Category

# Matches the actual sheet layout:
#   A=Date  B=RSD(formula)  C=EUR(formula)  D=Category  E=Group
#   F=Comment  G=Month(formula)  H=Rate
HEADER = ["Date", "", "Sum", "Category", "Group", "Comment", "Month", "Euro"]

SAMPLE_SHEET = [
    HEADER,
    ["Apr-1", "", "0", "Food", "Essentials", "", "4", ""],
    ["Apr-1", "", "0", "Transport", "Essentials", "", "4", ""],
    ["Apr-1", "", "0", "Cinema", "Entertainment", "", "4", ""],
    ["Apr-1", "500", "4", "Food", "Travel", "", "4", ""],
    ["Mar-1", "5000", "43", "Food", "Essentials", "prev", "3", "117.00"],
    ["Mar-1", "2000", "17", "Transport", "Essentials", "", "3", ""],
    ["Mar-1", "", "0", "Cinema", "Entertainment", "", "3", ""],
]


def _make_worksheet(all_values):
    ws = MagicMock()
    ws.get_all_values.return_value = all_values
    ws.id = 0
    ws.spreadsheet = MagicMock()
    return ws


class TestLoadCategories:
    @patch("dinary.services.sheets._get_sheet")
    def test_loads_unique_pairs(self, mock_get_sheet):
        from dinary.services.sheets import load_categories

        ws = _make_worksheet(list(SAMPLE_SHEET))
        cats = load_categories(ws)

        assert len(cats) == 4
        assert Category(name="Food", group="Essentials") in cats
        assert Category(name="Food", group="Travel") in cats
        assert Category(name="Transport", group="Essentials") in cats
        assert Category(name="Cinema", group="Entertainment") in cats

    @patch("dinary.services.sheets._get_sheet")
    def test_empty_sheet(self, mock_get_sheet):
        from dinary.services.sheets import load_categories

        ws = _make_worksheet([HEADER])
        cats = load_categories(ws)
        assert cats == []


class TestFindCategoryRow:
    def test_finds_row(self):
        from dinary.services.sheets import _find_category_row

        row = _find_category_row(SAMPLE_SHEET, 4, "Food", "Essentials")
        assert row == 2

    def test_finds_duplicate_category_in_different_group(self):
        from dinary.services.sheets import _find_category_row

        row = _find_category_row(SAMPLE_SHEET, 4, "Food", "Travel")
        assert row == 5

    def test_returns_none_for_missing(self):
        from dinary.services.sheets import _find_category_row

        row = _find_category_row(SAMPLE_SHEET, 4, "Unknown", "")
        assert row is None

    def test_returns_none_for_wrong_month(self):
        from dinary.services.sheets import _find_category_row

        row = _find_category_row(SAMPLE_SHEET, 5, "Food", "Essentials")
        assert row is None


class TestWriteExpense:
    @pytest.mark.anyio
    @patch("dinary.services.sheets.fetch_eur_rsd_rate", new_callable=AsyncMock)
    @patch("dinary.services.sheets._get_sheet")
    async def test_appends_to_formula(self, mock_get_sheet, mock_rate):
        from dinary.services.sheets import write_expense

        mock_rate.return_value = Decimal("117.32")

        ws = MagicMock()
        mock_get_sheet.return_value.sheet1 = ws
        ws.get_all_values.return_value = list(SAMPLE_SHEET)

        mock_cell = MagicMock()
        mock_cell.value = "=300+200"
        ws.acell.return_value = mock_cell

        result = await write_expense(
            amount_rsd=500.0,
            category="Food",
            group="Essentials",
            comment="test",
            expense_date=date(2026, 4, 14),
        )

        assert result["category"] == "Food"
        assert result["amount_rsd"] == 500.0

        ws.update.assert_called_once()
        call_kwargs = ws.update.call_args.kwargs
        assert call_kwargs["values"] == [["=300+200+500"]]
        assert call_kwargs["value_input_option"] == "USER_ENTERED"

    @pytest.mark.anyio
    @patch("dinary.services.sheets.fetch_eur_rsd_rate", new_callable=AsyncMock)
    @patch("dinary.services.sheets._get_sheet")
    async def test_creates_formula_from_empty(self, mock_get_sheet, mock_rate):
        from dinary.services.sheets import write_expense

        mock_rate.return_value = Decimal("117.32")

        ws = MagicMock()
        mock_get_sheet.return_value.sheet1 = ws
        ws.get_all_values.return_value = list(SAMPLE_SHEET)

        mock_cell = MagicMock()
        mock_cell.value = ""
        ws.acell.return_value = mock_cell

        await write_expense(
            amount_rsd=1500.0,
            category="Food",
            group="Essentials",
            comment="",
            expense_date=date(2026, 4, 14),
        )

        call_kwargs = ws.update.call_args.kwargs
        assert call_kwargs["values"] == [["=1500"]]

    @pytest.mark.anyio
    @patch("dinary.services.sheets.fetch_eur_rsd_rate", new_callable=AsyncMock)
    @patch("dinary.services.sheets._get_sheet")
    async def test_appends_to_plain_number(self, mock_get_sheet, mock_rate):
        """Cell with a plain number (not formula) should become =number+amount."""
        from dinary.services.sheets import write_expense

        mock_rate.return_value = Decimal("117.32")

        ws = MagicMock()
        mock_get_sheet.return_value.sheet1 = ws
        ws.get_all_values.return_value = list(SAMPLE_SHEET)

        mock_cell = MagicMock()
        mock_cell.value = 500
        ws.acell.return_value = mock_cell

        await write_expense(
            amount_rsd=300.0,
            category="Food",
            group="Essentials",
            comment="",
            expense_date=date(2026, 4, 14),
        )

        call_kwargs = ws.update.call_args.kwargs
        assert call_kwargs["values"] == [["=500+300"]]

    @pytest.mark.anyio
    @patch("dinary.services.sheets.fetch_eur_rsd_rate", new_callable=AsyncMock)
    @patch("dinary.services.sheets._get_sheet")
    async def test_uses_existing_rate(self, mock_get_sheet, mock_rate):
        from dinary.services.sheets import write_expense

        ws = MagicMock()
        mock_get_sheet.return_value.sheet1 = ws

        sheet = [row[:] for row in SAMPLE_SHEET]
        sheet[1][7] = "117.50"
        ws.get_all_values.return_value = sheet

        mock_cell = MagicMock()
        mock_cell.value = ""
        ws.acell.return_value = mock_cell

        await write_expense(
            amount_rsd=1000.0,
            category="Food",
            group="Essentials",
            comment="",
            expense_date=date(2026, 4, 14),
        )

        mock_rate.assert_not_called()

    @pytest.mark.anyio
    @patch("dinary.services.sheets.fetch_eur_rsd_rate", new_callable=AsyncMock)
    @patch("dinary.services.sheets._get_sheet")
    async def test_rate_written_to_first_row_of_month(self, mock_get_sheet, mock_rate):
        """EUR rate should be written to the first row of the month, not the expense row."""
        from dinary.services.sheets import write_expense

        mock_rate.return_value = Decimal("117.32")

        ws = MagicMock()
        mock_get_sheet.return_value.sheet1 = ws
        ws.get_all_values.return_value = list(SAMPLE_SHEET)

        mock_cell = MagicMock()
        mock_cell.value = ""
        ws.acell.return_value = mock_cell

        await write_expense(
            amount_rsd=500.0,
            category="Cinema",
            group="Entertainment",
            comment="",
            expense_date=date(2026, 4, 14),
        )

        rate_calls = [c for c in ws.update_cell.call_args_list if c[0][1] == 8]
        assert len(rate_calls) == 1
        assert rate_calls[0][0][0] == 2, "Rate should be at row 2 (first Apr row)"

    @pytest.mark.anyio
    @patch("dinary.services.sheets.fetch_eur_rsd_rate", new_callable=AsyncMock)
    @patch("dinary.services.sheets._get_sheet")
    async def test_does_not_write_eur_or_month(self, mock_get_sheet, mock_rate):
        """Columns C (EUR) and G (month) are formula-driven — never written."""
        from dinary.services.sheets import write_expense

        mock_rate.return_value = Decimal("117.32")

        ws = MagicMock()
        mock_get_sheet.return_value.sheet1 = ws
        ws.get_all_values.return_value = list(SAMPLE_SHEET)

        mock_cell = MagicMock()
        mock_cell.value = ""
        ws.acell.return_value = mock_cell

        await write_expense(
            amount_rsd=500.0,
            category="Cinema",
            group="Entertainment",
            comment="",
            expense_date=date(2026, 4, 14),
        )

        for call in ws.update_cell.call_args_list:
            col = call[0][1]
            assert col not in (3, 7), f"Should not write to column {col}"

    @pytest.mark.anyio
    @patch("dinary.services.sheets.fetch_eur_rsd_rate", new_callable=AsyncMock)
    @patch("dinary.services.sheets._get_sheet")
    async def test_unknown_category_raises(self, mock_get_sheet, mock_rate):
        from dinary.services.sheets import write_expense

        ws = MagicMock()
        mock_get_sheet.return_value.sheet1 = ws
        ws.get_all_values.return_value = list(SAMPLE_SHEET)

        with pytest.raises(ValueError, match="Category 'Unknown'"):
            await write_expense(
                amount_rsd=100.0,
                category="Unknown",
                group="",
                comment="",
                expense_date=date(2026, 4, 14),
            )


class TestCreateMonthRows:
    @patch("dinary.services.sheets._get_sheet")
    def test_inserts_at_top_with_copy_paste(self, mock_get_sheet):
        from dinary.services.sheets import _create_month_rows

        ws = _make_worksheet([row[:] for row in SAMPLE_SHEET])

        _create_month_rows(ws, list(SAMPLE_SHEET), date(2026, 5, 1))

        ws.spreadsheet.batch_update.assert_called_once()
        requests = ws.spreadsheet.batch_update.call_args[0][0]["requests"]
        assert len(requests) == 2

        insert = requests[0]["insertDimension"]["range"]
        assert insert["startIndex"] == 1  # after header
        assert insert["endIndex"] == 5  # 4 Apr rows

        cp = requests[1]["copyPaste"]
        # Source shifted down by 4 (num_rows): Apr was rows 2-5, now 6-9
        assert cp["source"]["startRowIndex"] == 5  # row 6, 0-indexed
        assert cp["source"]["endRowIndex"] == 9
        # Destination is right after header
        assert cp["destination"]["startRowIndex"] == 1
        assert cp["destination"]["endRowIndex"] == 5

        ws.batch_update.assert_called_once()
        batch_data = ws.batch_update.call_args[0][0]
        a_updates = [d for d in batch_data if d["range"].startswith("A")]
        assert len(a_updates) == 4
        assert a_updates[0]["values"] == [["2026-05-01"]]
        # New rows start at row 2
        assert a_updates[0]["range"] == "A2"

    @patch("dinary.services.sheets._get_sheet")
    def test_no_previous_month_raises(self, mock_get_sheet):
        from dinary.services.sheets import _create_month_rows

        ws = _make_worksheet([HEADER])

        with pytest.raises(ValueError, match="No rows found"):
            _create_month_rows(ws, [HEADER], date(2026, 5, 1))

"""Tests for sheets.py — flat-table layout with formula columns."""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import allure
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


@allure.epic("Google Sheets")
@allure.feature("Read Categories")
class TestLoadCategories:
    @patch("dinary.services.sheets.get_sheet")
    def test_loads_unique_pairs(self, mock_get_sheet):
        from dinary.services.sheets import load_categories

        ws = _make_worksheet(list(SAMPLE_SHEET))
        cats = load_categories(ws)

        assert len(cats) == 4
        assert Category(name="Food", group="Essentials") in cats
        assert Category(name="Food", group="Travel") in cats
        assert Category(name="Transport", group="Essentials") in cats
        assert Category(name="Cinema", group="Entertainment") in cats

    @patch("dinary.services.sheets.get_sheet")
    def test_empty_sheet(self, mock_get_sheet):
        from dinary.services.sheets import load_categories

        ws = _make_worksheet([HEADER])
        cats = load_categories(ws)
        assert cats == []


@allure.epic("Google Sheets")
@allure.feature("Read Categories")
class TestFindCategoryRow:
    def test_finds_row(self):
        from dinary.services.sheets import find_category_row

        row = find_category_row(SAMPLE_SHEET, 4, "Food", "Essentials")
        assert row == 2

    def test_finds_duplicate_category_in_different_group(self):
        from dinary.services.sheets import find_category_row

        row = find_category_row(SAMPLE_SHEET, 4, "Food", "Travel")
        assert row == 5

    def test_returns_none_for_missing(self):
        from dinary.services.sheets import find_category_row

        row = find_category_row(SAMPLE_SHEET, 4, "Unknown", "")
        assert row is None

    def test_returns_none_for_wrong_month(self):
        from dinary.services.sheets import find_category_row

        row = find_category_row(SAMPLE_SHEET, 5, "Food", "Essentials")
        assert row is None


@allure.epic("Google Sheets")
@allure.feature("Write Expense")
class TestWriteExpense:
    @pytest.mark.anyio
    @patch("dinary.services.sheets.fetch_eur_rsd_rate", new_callable=AsyncMock)
    @patch("dinary.services.sheets.get_sheet")
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
    @patch("dinary.services.sheets.get_sheet")
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
    @patch("dinary.services.sheets.get_sheet")
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
    @patch("dinary.services.sheets.get_sheet")
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
    @patch("dinary.services.sheets.get_sheet")
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
    @patch("dinary.services.sheets.get_sheet")
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
    @patch("dinary.services.sheets.get_sheet")
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


@allure.epic("Data Safety")
@allure.feature("Formula Preservation")
class TestAppendToRsdFormula:
    """append_to_rsd_formula must NEVER overwrite existing data.

    It must always append +amount to whatever is already in the cell.
    """

    def _run(self, existing_value, amount, expected_formula):
        from dinary.services.sheets import append_to_rsd_formula

        ws = MagicMock()
        mock_cell = MagicMock()
        mock_cell.value = existing_value
        ws.acell.return_value = mock_cell

        append_to_rsd_formula(ws, 2, amount)

        ws.update.assert_called_once()
        actual = ws.update.call_args.kwargs["values"][0][0]
        assert actual == expected_formula, (
            f"For existing={existing_value!r}, amount={amount}: got {actual!r}"
        )

    def test_append_to_existing_formula(self):
        self._run("=300+200", 500, "=300+200+500")

    def test_append_to_long_formula(self):
        existing = "=460+373+755+1436+6206+902+214+2035"
        self._run(existing, 100, f"{existing}+100")

    def test_append_to_single_value_formula(self):
        self._run("=1500", 300, "=1500+300")

    def test_empty_cell_creates_formula(self):
        self._run("", 1500, "=1500")

    def test_none_cell_creates_formula(self):
        self._run(None, 1500, "=1500")

    def test_plain_integer(self):
        self._run(500, 300, "=500+300")

    def test_plain_integer_zero(self):
        self._run(0, 1500, "=0+1500")

    def test_plain_string_number(self):
        self._run("500", 300, "=500+300")

    def test_plain_float(self):
        self._run(500.5, 100, "=500.5+100")

    def test_plain_string_float(self):
        self._run("500.50", 100, "=500.50+100")

    def test_decimal_amount(self):
        self._run("=1000", 99.5, "=1000+99.50")

    def test_integer_amount_no_decimals(self):
        """1500.0 should be formatted as '1500', not '1500.0'."""
        self._run("=100", 1500.0, "=100+1500")

    def test_whitespace_only_cell(self):
        self._run("   ", 500, "=500")

    def test_formula_result_is_always_formula(self):
        """Result must always start with '=' to be a Google Sheets formula."""
        from dinary.services.sheets import append_to_rsd_formula

        for existing in ["", None, 0, 500, "500", "=100", "=100+200"]:
            ws = MagicMock()
            mock_cell = MagicMock()
            mock_cell.value = existing
            ws.acell.return_value = mock_cell

            append_to_rsd_formula(ws, 2, 100)

            result = ws.update.call_args.kwargs["values"][0][0]
            assert result.startswith("="), (
                f"For existing={existing!r}: result {result!r} is not a formula"
            )

    def test_never_overwrites_to_plain_number(self):
        """The written value must NEVER be a plain number — always a formula."""
        from dinary.services.sheets import append_to_rsd_formula

        ws = MagicMock()
        mock_cell = MagicMock()
        mock_cell.value = "=460+373+755"
        ws.acell.return_value = mock_cell

        append_to_rsd_formula(ws, 2, 100)

        result = ws.update.call_args.kwargs["values"][0][0]
        assert "+" in result, "Formula must contain + (append, not overwrite)"
        assert result.count("+") >= 3, "Must preserve all existing terms"

    def test_acell_uses_formula_render_option(self):
        """Must read with FORMULA render option to get the formula, not the computed value."""
        from gspread.utils import ValueRenderOption

        from dinary.services.sheets import append_to_rsd_formula

        ws = MagicMock()
        mock_cell = MagicMock()
        mock_cell.value = "=100"
        ws.acell.return_value = mock_cell

        append_to_rsd_formula(ws, 2, 50)

        ws.acell.assert_called_once()
        call_kwargs = ws.acell.call_args
        assert (
            call_kwargs.kwargs.get("value_render_option") == ValueRenderOption.formula
            or call_kwargs[1].get("value_render_option") == ValueRenderOption.formula
        )


@allure.epic("Data Safety")
@allure.feature("Comment Preservation")
class TestAppendComment:
    """append_comment must never overwrite existing comments."""

    def test_append_to_existing(self):
        from dinary.services.sheets import append_comment

        ws = MagicMock()
        row_data = ["", "", "", "Food", "Essentials", "lunch", "4", ""]

        append_comment(ws, 2, row_data, "dinner")

        ws.update_cell.assert_called_once_with(2, 6, "lunch; dinner")

    def test_first_comment(self):
        from dinary.services.sheets import append_comment

        ws = MagicMock()
        row_data = ["", "", "", "Food", "Essentials", "", "4", ""]

        append_comment(ws, 2, row_data, "lunch")

        ws.update_cell.assert_called_once_with(2, 6, "lunch")

    def test_preserves_multiple_existing_comments(self):
        from dinary.services.sheets import append_comment

        ws = MagicMock()
        row_data = ["", "", "", "Food", "Essentials", "a; b; c", "4", ""]

        append_comment(ws, 2, row_data, "d")

        ws.update_cell.assert_called_once_with(2, 6, "a; b; c; d")


@allure.epic("Data Safety")
@allure.feature("Column Protection")
class TestWriteExpenseNeverCorrupts:
    """write_expense must NEVER overwrite columns C (EUR) or G (Month)."""

    @pytest.mark.anyio
    @patch("dinary.services.sheets.fetch_eur_rsd_rate", new_callable=AsyncMock)
    @patch("dinary.services.sheets.get_sheet")
    async def test_never_writes_to_eur_column(self, mock_get_sheet, mock_rate):
        from dinary.services.sheets import write_expense

        mock_rate.return_value = Decimal("117.32")

        ws = MagicMock()
        mock_get_sheet.return_value.sheet1 = ws
        ws.get_all_values.return_value = [row[:] for row in SAMPLE_SHEET]

        mock_cell = MagicMock()
        mock_cell.value = "=300"
        ws.acell.return_value = mock_cell

        await write_expense(500.0, "Food", "Essentials", "test", date(2026, 4, 14))

        for call in ws.update_cell.call_args_list:
            assert call[0][1] != 3, "Must not write to column C (EUR)"

        for call in ws.update.call_args_list:
            range_name = call.kwargs.get("range_name", "")
            assert not range_name.startswith("C"), "Must not write to column C (EUR)"

    @pytest.mark.anyio
    @patch("dinary.services.sheets.fetch_eur_rsd_rate", new_callable=AsyncMock)
    @patch("dinary.services.sheets.get_sheet")
    async def test_never_writes_to_month_column(self, mock_get_sheet, mock_rate):
        from dinary.services.sheets import write_expense

        mock_rate.return_value = Decimal("117.32")

        ws = MagicMock()
        mock_get_sheet.return_value.sheet1 = ws
        ws.get_all_values.return_value = [row[:] for row in SAMPLE_SHEET]

        mock_cell = MagicMock()
        mock_cell.value = "=300"
        ws.acell.return_value = mock_cell

        await write_expense(500.0, "Food", "Essentials", "", date(2026, 4, 14))

        for call in ws.update_cell.call_args_list:
            assert call[0][1] != 7, "Must not write to column G (Month)"

    @pytest.mark.anyio
    @patch("dinary.services.sheets.fetch_eur_rsd_rate", new_callable=AsyncMock)
    @patch("dinary.services.sheets.get_sheet")
    async def test_never_writes_to_date_column(self, mock_get_sheet, mock_rate):
        from dinary.services.sheets import write_expense

        mock_rate.return_value = Decimal("117.32")

        ws = MagicMock()
        mock_get_sheet.return_value.sheet1 = ws
        ws.get_all_values.return_value = [row[:] for row in SAMPLE_SHEET]

        mock_cell = MagicMock()
        mock_cell.value = "=300"
        ws.acell.return_value = mock_cell

        await write_expense(500.0, "Food", "Essentials", "", date(2026, 4, 14))

        for call in ws.update_cell.call_args_list:
            assert call[0][1] != 1, "Must not write to column A (Date)"

    @pytest.mark.anyio
    @patch("dinary.services.sheets.fetch_eur_rsd_rate", new_callable=AsyncMock)
    @patch("dinary.services.sheets.get_sheet")
    async def test_never_writes_to_category_or_group(self, mock_get_sheet, mock_rate):
        from dinary.services.sheets import write_expense

        mock_rate.return_value = Decimal("117.32")

        ws = MagicMock()
        mock_get_sheet.return_value.sheet1 = ws
        ws.get_all_values.return_value = [row[:] for row in SAMPLE_SHEET]

        mock_cell = MagicMock()
        mock_cell.value = ""
        ws.acell.return_value = mock_cell

        await write_expense(500.0, "Cinema", "Entertainment", "", date(2026, 4, 14))

        for call in ws.update_cell.call_args_list:
            col = call[0][1]
            assert col not in (4, 5), f"Must not write to column {col} (Category/Group)"

    @pytest.mark.anyio
    @patch("dinary.services.sheets.fetch_eur_rsd_rate", new_callable=AsyncMock)
    @patch("dinary.services.sheets.get_sheet")
    async def test_only_writes_to_rsd_comment_rate_columns(self, mock_get_sheet, mock_rate):
        """write_expense may only modify columns B (RSD), F (Comment), H (Rate)."""
        from dinary.services.sheets import write_expense

        mock_rate.return_value = Decimal("117.32")

        ws = MagicMock()
        mock_get_sheet.return_value.sheet1 = ws
        ws.get_all_values.return_value = [row[:] for row in SAMPLE_SHEET]

        mock_cell = MagicMock()
        mock_cell.value = "=100"
        ws.acell.return_value = mock_cell

        await write_expense(500.0, "Food", "Essentials", "test comment", date(2026, 4, 14))

        allowed_cols = {2, 6, 8}  # B=RSD, F=Comment, H=Rate
        for call in ws.update_cell.call_args_list:
            col = call[0][1]
            assert col in allowed_cols, f"Wrote to unexpected column {col}"

        for call in ws.update.call_args_list:
            range_name = call.kwargs.get("range_name", "")
            if range_name:
                col_letter = range_name[0]
                assert col_letter in ("B",), f"update() wrote to unexpected column {col_letter}"


@allure.epic("Google Sheets")
@allure.feature("Exchange Rate")
class TestEnsureRate:
    @pytest.mark.anyio
    @patch("dinary.services.sheets.fetch_eur_rsd_rate", new_callable=AsyncMock)
    async def test_does_not_fetch_when_rate_exists(self, mock_rate):
        from dinary.services.sheets import _ensure_rate

        ws = MagicMock()
        sheet = [row[:] for row in SAMPLE_SHEET]
        sheet[1][7] = "117.50"

        rate = await _ensure_rate(ws, sheet, 4, date(2026, 4, 1))

        mock_rate.assert_not_called()
        assert rate == Decimal("117.50")

    @pytest.mark.anyio
    @patch("dinary.services.sheets.fetch_eur_rsd_rate", new_callable=AsyncMock)
    async def test_writes_rate_to_first_row_of_month(self, mock_rate):
        from dinary.services.sheets import _ensure_rate

        mock_rate.return_value = Decimal("117.32")

        ws = MagicMock()
        sheet = [row[:] for row in SAMPLE_SHEET]

        await _ensure_rate(ws, sheet, 4, date(2026, 4, 1))

        ws.update_cell.assert_called_once()
        row, col, val = ws.update_cell.call_args[0]
        assert row == 2, "Rate must go in first row of the month"
        assert col == 8, "Rate must go in column H"

    @pytest.mark.anyio
    @patch("dinary.services.sheets.fetch_eur_rsd_rate", new_callable=AsyncMock)
    async def test_rate_not_written_to_expense_row(self, mock_rate):
        """If expense is on row 4, rate must still go to row 2 (first row of month)."""
        from dinary.services.sheets import _ensure_rate

        mock_rate.return_value = Decimal("117.32")

        ws = MagicMock()
        sheet = [row[:] for row in SAMPLE_SHEET]

        await _ensure_rate(ws, sheet, 4, date(2026, 4, 14))

        row = ws.update_cell.call_args[0][0]
        assert row == 2, f"Rate written to row {row} instead of first month row (2)"


@allure.epic("Google Sheets")
@allure.feature("Month Creation")
class TestCreateMonthRows:
    @patch("dinary.services.sheets.get_sheet")
    def test_inserts_at_top_with_copy_paste(self, mock_get_sheet):
        from dinary.services.sheets import create_month_rows

        ws = _make_worksheet([row[:] for row in SAMPLE_SHEET])

        create_month_rows(ws, list(SAMPLE_SHEET), date(2026, 5, 1))

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

    @patch("dinary.services.sheets.get_sheet")
    def test_no_previous_month_raises(self, mock_get_sheet):
        from dinary.services.sheets import create_month_rows

        ws = _make_worksheet([HEADER])

        with pytest.raises(ValueError, match="No rows found"):
            create_month_rows(ws, [HEADER], date(2026, 5, 1))

    @patch("dinary.services.sheets.get_sheet")
    def test_clears_amounts_comments_rate_only(self, mock_get_sheet):
        """New month rows must clear B (amounts), F (comments), H (rate) but not D, E, G."""
        from dinary.services.sheets import create_month_rows

        ws = _make_worksheet([row[:] for row in SAMPLE_SHEET])

        create_month_rows(ws, list(SAMPLE_SHEET), date(2026, 5, 1))

        batch_data = ws.batch_update.call_args[0][0]
        cleared_cols = set()
        for item in batch_data:
            col_letter = item["range"][0]
            cleared_cols.add(col_letter)

        assert "A" in cleared_cols, "Should set new date"
        assert "B" in cleared_cols, "Should clear amounts"
        assert "F" in cleared_cols, "Should clear comments"
        assert "H" in cleared_cols, "Should clear rate"
        assert "C" not in cleared_cols, "Must not touch EUR column"
        assert "D" not in cleared_cols, "Must not touch Category column"
        assert "E" not in cleared_cols, "Must not touch Group column"
        assert "G" not in cleared_cols, "Must not touch Month column"

    @patch("dinary.services.sheets.get_sheet")
    def test_copies_all_rows_from_previous_month(self, mock_get_sheet):
        from dinary.services.sheets import create_month_rows

        ws = _make_worksheet([row[:] for row in SAMPLE_SHEET])

        create_month_rows(ws, list(SAMPLE_SHEET), date(2026, 5, 1))

        requests = ws.spreadsheet.batch_update.call_args[0][0]["requests"]
        insert = requests[0]["insertDimension"]["range"]
        num_inserted = insert["endIndex"] - insert["startIndex"]
        assert num_inserted == 4, "April has 4 rows, all should be copied"

    @patch("dinary.services.sheets.get_sheet")
    def test_new_rows_get_correct_date(self, mock_get_sheet):
        from dinary.services.sheets import create_month_rows

        ws = _make_worksheet([row[:] for row in SAMPLE_SHEET])

        create_month_rows(ws, list(SAMPLE_SHEET), date(2026, 5, 15))

        batch_data = ws.batch_update.call_args[0][0]
        date_updates = [d for d in batch_data if d["range"].startswith("A")]
        for d in date_updates:
            assert d["values"] == [["2026-05-01"]], "Date should be first of month"


@allure.epic("Google Sheets")
@allure.feature("Month Creation")
class TestFindMonthRange:
    def test_finds_contiguous_range(self):
        from dinary.services.sheets import find_month_range

        result = find_month_range(SAMPLE_SHEET, 4)
        assert result == (2, 5)

    def test_finds_march(self):
        from dinary.services.sheets import find_month_range

        result = find_month_range(SAMPLE_SHEET, 3)
        assert result == (6, 8)

    def test_returns_none_for_missing_month(self):
        from dinary.services.sheets import find_month_range

        assert find_month_range(SAMPLE_SHEET, 12) is None


@allure.epic("Google Sheets")
@allure.feature("Helpers")
class TestHelpers:
    def test_fmt_amount_integer(self):
        from dinary.services.sheets import fmt_amount

        assert fmt_amount(1500.0) == "1500"
        assert fmt_amount(1500) == "1500"

    def test_fmt_amount_decimal(self):
        from dinary.services.sheets import fmt_amount

        assert fmt_amount(99.5) == "99.50"
        assert fmt_amount(99.99) == "99.99"

    def test_is_numeric(self):
        from dinary.services.sheets import _is_numeric

        assert _is_numeric("500")
        assert _is_numeric("500.50")
        assert _is_numeric("1,500")
        assert not _is_numeric("")
        assert not _is_numeric("abc")
        assert not _is_numeric("=100+200")

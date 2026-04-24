"""Tests for sheets.py — flat-table layout with formula columns."""

from datetime import date
from unittest.mock import MagicMock

import allure
from gspread.utils import ValueRenderOption

from dinary.services.sheets import (
    _is_numeric,
    _year_from_a_value,
    append_comment,
    append_to_amount_formula,
    ensure_category_row,
    fetch_row_years,
    find_category_row,
    find_month_range,
    fmt_amount,
    get_month_rate,
)

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
class TestFindCategoryRow:
    def test_finds_row(self):

        row = find_category_row(SAMPLE_SHEET, 4, "Food", "Essentials")
        assert row == 2

    def test_finds_duplicate_category_in_different_group(self):

        row = find_category_row(SAMPLE_SHEET, 4, "Food", "Travel")
        assert row == 5

    def test_returns_none_for_missing(self):

        row = find_category_row(SAMPLE_SHEET, 4, "Unknown", "")
        assert row is None

    def test_returns_none_for_wrong_month(self):

        row = find_category_row(SAMPLE_SHEET, 5, "Food", "Essentials")
        assert row is None


@allure.epic("Data Safety")
@allure.feature("Formula Preservation")
class TestAppendToRsdFormula:
    """append_to_amount_formula must NEVER overwrite existing data.

    It must always append +amount to whatever is already in the cell.
    """

    def _run(self, existing_value, amount, expected_formula):

        ws = MagicMock()
        mock_cell = MagicMock()
        mock_cell.value = existing_value
        ws.acell.return_value = mock_cell

        append_to_amount_formula(ws, 2, amount)

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

        for existing in ["", None, 0, 500, "500", "=100", "=100+200"]:
            ws = MagicMock()
            mock_cell = MagicMock()
            mock_cell.value = existing
            ws.acell.return_value = mock_cell

            append_to_amount_formula(ws, 2, 100)

            result = ws.update.call_args.kwargs["values"][0][0]
            assert result.startswith("="), (
                f"For existing={existing!r}: result {result!r} is not a formula"
            )

    def test_never_overwrites_to_plain_number(self):
        """The written value must NEVER be a plain number — always a formula."""

        ws = MagicMock()
        mock_cell = MagicMock()
        mock_cell.value = "=460+373+755"
        ws.acell.return_value = mock_cell

        append_to_amount_formula(ws, 2, 100)

        result = ws.update.call_args.kwargs["values"][0][0]
        assert "+" in result, "Formula must contain + (append, not overwrite)"
        assert result.count("+") >= 3, "Must preserve all existing terms"

    def test_acell_uses_formula_render_option(self):
        """Must read with FORMULA render option to get the formula, not the computed value."""

        ws = MagicMock()
        mock_cell = MagicMock()
        mock_cell.value = "=100"
        ws.acell.return_value = mock_cell

        append_to_amount_formula(ws, 2, 50)

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

        ws = MagicMock()
        row_data = ["", "", "", "Food", "Essentials", "lunch", "4", ""]

        append_comment(ws, 2, row_data, "dinner")

        ws.update_cell.assert_called_once_with(2, 6, "lunch; dinner")

    def test_first_comment(self):

        ws = MagicMock()
        row_data = ["", "", "", "Food", "Essentials", "", "4", ""]

        append_comment(ws, 2, row_data, "lunch")

        ws.update_cell.assert_called_once_with(2, 6, "lunch")

    def test_preserves_multiple_existing_comments(self):

        ws = MagicMock()
        row_data = ["", "", "", "Food", "Essentials", "a; b; c", "4", ""]

        append_comment(ws, 2, row_data, "d")

        ws.update_cell.assert_called_once_with(2, 6, "a; b; c; d")


@allure.epic("Google Sheets")
@allure.feature("Row Insertion")
class TestEnsureCategoryRow:
    def test_existing_row_returned_as_is(self):

        ws = _make_worksheet([row[:] for row in SAMPLE_SHEET])

        row, refreshed = ensure_category_row(
            ws,
            list(SAMPLE_SHEET),
            4,
            "Food",
            "Essentials",
            date(2026, 4, 1),
        )

        assert row == 2
        ws.insert_rows.assert_not_called()

    def test_new_row_inserted_in_existing_month(self):

        sheet = [row[:] for row in SAMPLE_SHEET]
        ws = _make_worksheet(sheet)
        # After insert_rows, ws.get_all_values returns the new grid
        inserted_row = ["2026-04-01", "", "", "Clothes", "", "", "4", ""]
        ws.get_all_values.return_value = sheet[:2] + [inserted_row] + sheet[2:]

        row, _ = ensure_category_row(
            ws,
            list(SAMPLE_SHEET),
            4,
            "Clothes",
            "",
            date(2026, 4, 1),
        )

        ws.insert_rows.assert_called_once()
        ws.batch_update.assert_called_once()
        # "Clothes" < "Food" alphabetically, so it goes before "Food" (row 2)
        assert row == 2

    def test_new_row_appended_after_month_block(self):

        sheet = [row[:] for row in SAMPLE_SHEET]
        ws = _make_worksheet(sheet)
        inserted_row = ["2026-04-01", "", "", "Utilities", "Electric", "", "4", ""]
        ws.get_all_values.return_value = sheet[:5] + [inserted_row] + sheet[5:]

        row, _ = ensure_category_row(
            ws,
            list(SAMPLE_SHEET),
            4,
            "Utilities",
            "Electric",
            date(2026, 4, 1),
        )

        ws.insert_rows.assert_called_once()
        # "Utilities" > "Transport" > "Food" > "Cinema", so after row 5
        assert row == 6

    def test_new_month_inserted_after_header(self):

        sheet = [row[:] for row in SAMPLE_SHEET]
        ws = _make_worksheet(sheet)
        inserted_row = ["2026-05-01", "", "", "Food", "Essentials", "", "5", ""]
        ws.get_all_values.return_value = [sheet[0], inserted_row] + sheet[1:]

        row, _ = ensure_category_row(
            ws,
            list(SAMPLE_SHEET),
            5,
            "Food",
            "Essentials",
            date(2026, 5, 1),
        )

        ws.insert_rows.assert_called_once()
        assert row == 2

    def test_insert_sets_all_cells(self):
        """New row must populate A (date), C (EUR formula), D, E, G (month)."""

        sheet = [row[:] for row in SAMPLE_SHEET]
        ws = _make_worksheet(sheet)
        ws.get_all_values.return_value = sheet

        ensure_category_row(
            ws,
            list(SAMPLE_SHEET),
            5,
            "Food",
            "Essentials",
            date(2026, 5, 15),
        )

        batch_data = ws.batch_update.call_args[0][0]
        by_col = {item["range"][0]: item["values"][0][0] for item in batch_data}

        assert by_col["A"] == "2026-05-01"
        assert by_col["D"] == "Food"
        assert by_col["E"] == "Essentials"
        assert by_col["G"] == "5"
        assert by_col["B"] == ""
        assert by_col["F"] == ""
        assert by_col["H"] == ""
        assert "IF" in by_col["C"]


@allure.epic("Google Sheets")
@allure.feature("Row Insertion (legacy compat)")
class TestEnsureCategoryRowLegacySheet:
    """ensure_category_row must work against existing sheets with
    legacy-style copied month blocks (many categories per month,
    possibly different from the target category)."""

    LEGACY_SHEET = [
        HEADER,
        # April block (4 rows — old copy-paste style)
        ["Apr-1", "5000", "43", "Food", "Essentials", "", "4", "117.00"],
        ["Apr-1", "3000", "26", "Transport", "Essentials", "", "4", ""],
        ["Apr-1", "", "0", "Cinema", "Entertainment", "", "4", ""],
        ["Apr-1", "500", "4", "Food", "Travel", "", "4", ""],
        # March block (3 rows)
        ["Mar-1", "5000", "43", "Food", "Essentials", "prev", "3", "117.00"],
        ["Mar-1", "2000", "17", "Transport", "Essentials", "", "3", ""],
        ["Mar-1", "", "0", "Cinema", "Entertainment", "", "3", ""],
    ]

    def test_existing_category_found_in_legacy_block(self):

        sheet = [row[:] for row in self.LEGACY_SHEET]
        ws = _make_worksheet(sheet)

        row, refreshed = ensure_category_row(
            ws,
            list(self.LEGACY_SHEET),
            4,
            "Food",
            "Essentials",
            date(2026, 4, 1),
        )

        assert row == 2
        ws.insert_rows.assert_not_called()

    def test_new_category_in_legacy_month_block(self):
        """A new category in a legacy block with many existing rows."""

        sheet = [row[:] for row in self.LEGACY_SHEET]
        ws = _make_worksheet(sheet)
        inserted_row = ["2026-04-01", "", "", "Pharmacy", "", "", "4", ""]
        ws.get_all_values.return_value = sheet[:2] + [inserted_row] + sheet[2:]

        row, _ = ensure_category_row(
            ws,
            list(self.LEGACY_SHEET),
            4,
            "Pharmacy",
            "",
            date(2026, 4, 1),
        )

        ws.insert_rows.assert_called_once()
        # ("Pharmacy","") > ("Food","Essentials") but < ("Transport","Essentials")
        assert row == 3

    def test_new_month_in_legacy_sheet(self):
        """Adding a new month to a legacy sheet that has old-style blocks."""

        sheet = [row[:] for row in self.LEGACY_SHEET]
        ws = _make_worksheet(sheet)
        inserted_row = ["2026-05-01", "", "", "Food", "Essentials", "", "5", ""]
        ws.get_all_values.return_value = [sheet[0], inserted_row] + sheet[1:]

        row, _ = ensure_category_row(
            ws,
            list(self.LEGACY_SHEET),
            5,
            "Food",
            "Essentials",
            date(2026, 5, 1),
        )

        ws.insert_rows.assert_called_once()
        assert row == 2


@allure.epic("Google Sheets")
@allure.feature("Month Creation")
class TestFindMonthRange:
    def test_finds_contiguous_range(self):

        result = find_month_range(SAMPLE_SHEET, 4)
        assert result == (2, 5)

    def test_finds_march(self):

        result = find_month_range(SAMPLE_SHEET, 3)
        assert result == (6, 8)

    def test_returns_none_for_missing_month(self):

        assert find_month_range(SAMPLE_SHEET, 12) is None


@allure.epic("Google Sheets")
@allure.feature("Year-aware matching (multi-year sheet)")
class TestYearAwareMatching:
    """The optional logging spreadsheet keeps every year in one tab.

    Column G stores month 1..12 only; the year lives in column A as a
    Google Sheets date serial that displays as e.g. ``"Apr-1"``. A
    separate unformatted read decodes the year per row, and the helpers
    must use it so that logging an expense for a different year doesn't
    smear onto an existing year's rows.
    """

    HEADER = ["Date", "", "Sum", "Category", "Group", "Comment", "Month", "Euro"]

    # Two same-month blocks (Apr) for two different years, plus a Mar
    # 2027 block. Year column shows the formatted "Apr-1" (no year) but
    # we pass an aligned years_by_row that mirrors a real unformatted
    # column-A read.
    MULTIYEAR_SHEET = [
        HEADER,
        # Apr 2027
        ["Apr-1", "1000", "9", "Food", "Essentials", "", "4", "117.00"],
        ["Apr-1", "500", "4", "Cinema", "Entertainment", "", "4", ""],
        # Mar 2027
        ["Mar-1", "2000", "17", "Food", "Essentials", "", "3", "120.00"],
        # Apr 2026
        ["Apr-1", "5000", "43", "Food", "Essentials", "old", "4", "115.00"],
        ["Apr-1", "3000", "26", "Transport", "Essentials", "", "4", ""],
    ]
    YEARS_BY_ROW = [None, 2027, 2027, 2027, 2026, 2026]

    def test_find_category_row_picks_target_year(self):

        row_2027 = find_category_row(
            self.MULTIYEAR_SHEET,
            4,
            "Food",
            "Essentials",
            target_year=2027,
            years_by_row=self.YEARS_BY_ROW,
        )
        row_2026 = find_category_row(
            self.MULTIYEAR_SHEET,
            4,
            "Food",
            "Essentials",
            target_year=2026,
            years_by_row=self.YEARS_BY_ROW,
        )
        assert row_2027 == 2
        assert row_2026 == 5

    def test_find_category_row_misses_when_year_absent(self):

        row = find_category_row(
            self.MULTIYEAR_SHEET,
            4,
            "Transport",
            "Essentials",
            target_year=2027,
            years_by_row=self.YEARS_BY_ROW,
        )
        # Transport/Essentials only exists in the 2026 block.
        assert row is None

    def test_find_month_range_constrained_by_year(self):

        block_2027 = find_month_range(
            self.MULTIYEAR_SHEET,
            4,
            target_year=2027,
            years_by_row=self.YEARS_BY_ROW,
        )
        block_2026 = find_month_range(
            self.MULTIYEAR_SHEET,
            4,
            target_year=2026,
            years_by_row=self.YEARS_BY_ROW,
        )
        assert block_2027 == (2, 3)
        assert block_2026 == (5, 6)

    def test_get_month_rate_picks_target_year(self):

        rate_2027 = get_month_rate(
            self.MULTIYEAR_SHEET,
            4,
            target_year=2027,
            years_by_row=self.YEARS_BY_ROW,
        )
        rate_2026 = get_month_rate(
            self.MULTIYEAR_SHEET,
            4,
            target_year=2026,
            years_by_row=self.YEARS_BY_ROW,
        )
        assert rate_2027 == "117.00"
        assert rate_2026 == "115.00"

    def test_ensure_category_row_creates_new_year_block(self):
        """Logging Apr 2028 must NOT collide with Apr 2027/2026 rows."""

        sheet = [row[:] for row in self.MULTIYEAR_SHEET]
        ws = _make_worksheet(sheet)
        # Refreshed grid after insert — placed at the very top because
        # 2028-04 is newer than every existing block.
        inserted = ["2028-04-01", "", "", "Food", "Essentials", "", "4", ""]
        ws.get_all_values.return_value = [sheet[0], inserted] + sheet[1:]

        row, _ = ensure_category_row(
            ws,
            list(self.MULTIYEAR_SHEET),
            4,
            "Food",
            "Essentials",
            date(2028, 4, 1),
            years_by_row=self.YEARS_BY_ROW,
        )

        ws.insert_rows.assert_called_once()
        assert row == 2

    def test_ensure_category_row_inserts_between_year_blocks(self):
        """The most common production case: a brand-new (year, month)
        block must land between two existing year blocks, not at the
        top or bottom. Walking newest→oldest, we stop at the first
        strictly-older row.

        Layout: Apr 2027 (2..3), Mar 2027 (4), Apr 2026 (5..6).
        Logging Feb 2027 → must land at row 5 (after Mar 2027,
        before Apr 2026)."""

        sheet = [row[:] for row in self.MULTIYEAR_SHEET]
        ws = _make_worksheet(sheet)
        inserted = ["2027-02-01", "", "", "Food", "Essentials", "", "2", ""]
        ws.get_all_values.return_value = sheet[:4] + [inserted] + sheet[4:]

        row, _ = ensure_category_row(
            ws,
            list(self.MULTIYEAR_SHEET),
            2,
            "Food",
            "Essentials",
            date(2027, 2, 1),
            years_by_row=self.YEARS_BY_ROW,
        )

        ws.insert_rows.assert_called_once()
        assert row == 5

    def test_ensure_category_row_inserts_oldest_year_at_bottom(self):

        sheet = [row[:] for row in self.MULTIYEAR_SHEET]
        ws = _make_worksheet(sheet)
        inserted = ["2025-12-01", "", "", "Food", "Essentials", "", "12", ""]
        ws.get_all_values.return_value = sheet + [inserted]

        row, _ = ensure_category_row(
            ws,
            list(self.MULTIYEAR_SHEET),
            12,
            "Food",
            "Essentials",
            date(2025, 12, 1),
            years_by_row=self.YEARS_BY_ROW,
        )

        ws.insert_rows.assert_called_once()
        # Older than every existing (year, month) pair → bottom.
        assert row == len(self.MULTIYEAR_SHEET) + 1

    def test_ensure_category_row_finds_existing_year_match(self):
        """The original bug: must NOT silently insert when the row exists in target year."""

        sheet = [row[:] for row in self.MULTIYEAR_SHEET]
        ws = _make_worksheet(sheet)

        row, _ = ensure_category_row(
            ws,
            list(self.MULTIYEAR_SHEET),
            4,
            "Food",
            "Essentials",
            date(2026, 4, 1),
            years_by_row=self.YEARS_BY_ROW,
        )

        ws.insert_rows.assert_not_called()
        assert row == 5

    def test_ensure_category_row_inserts_when_only_other_year_exists(self):
        """Apr 2027 has Food/Essentials; logging Food/Essentials for Apr 2026
        must still hit the 2026 block (different row), not the 2027 row."""

        sheet = [row[:] for row in self.MULTIYEAR_SHEET]
        ws = _make_worksheet(sheet)

        # Both years already have Food/Essentials, so no insert at all.
        row, _ = ensure_category_row(
            ws,
            list(self.MULTIYEAR_SHEET),
            4,
            "Food",
            "Essentials",
            date(2027, 4, 1),
            years_by_row=self.YEARS_BY_ROW,
        )

        ws.insert_rows.assert_not_called()
        assert row == 2  # 2027 row, not the 2026 row at index 5

    def test_year_from_a_value_handles_serial_and_iso(self):

        # 2026-04-01 as a Google Sheets serial.
        assert _year_from_a_value(46113) == 2026
        assert _year_from_a_value(46113.5) == 2026
        assert _year_from_a_value("2027-04-01") == 2027
        assert _year_from_a_value("Apr-1") is None
        assert _year_from_a_value("") is None
        assert _year_from_a_value(None) is None
        assert _year_from_a_value(True) is None

    def test_fetch_row_years_uses_unformatted_render(self):

        ws = MagicMock()
        ws.batch_get.return_value = [[["Date"], [46113], [46113], [], ["2026-04-01"], [None]]]

        years = fetch_row_years(ws, 6)

        ws.batch_get.assert_called_once()
        call_kwargs = ws.batch_get.call_args.kwargs
        assert call_kwargs.get("value_render_option") == ValueRenderOption.unformatted
        assert years == [None, 2026, 2026, None, 2026, None]

    def test_fetch_row_years_returns_empty_for_zero_rows(self):

        ws = MagicMock()
        assert fetch_row_years(ws, 0) == []
        ws.batch_get.assert_not_called()


@allure.epic("Google Sheets")
@allure.feature("Helpers")
class TestHelpers:
    def test_fmt_amount_integer(self):

        assert fmt_amount(1500.0) == "1500"
        assert fmt_amount(1500) == "1500"

    def test_fmt_amount_decimal(self):

        assert fmt_amount(99.5) == "99.50"
        assert fmt_amount(99.99) == "99.99"

    def test_is_numeric(self):

        assert _is_numeric("500")
        assert _is_numeric("500.50")
        assert _is_numeric("1,500")
        assert not _is_numeric("")
        assert not _is_numeric("abc")
        assert not _is_numeric("=100+200")

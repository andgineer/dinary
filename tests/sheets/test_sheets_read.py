"""Read-side tests for ``dinary.services.sheets``.

Covers the lookup helpers (``find_category_row``, ``find_month_range``,
``get_month_rate``, ``fetch_row_years``, ``_year_from_a_value``) and
the multi-year matching pipeline that uses an aligned
``years_by_row`` produced from a separate unformatted column-A read.

Append-side helpers (``append_to_amount_formula``, ``append_comment``)
live in :file:`test_sheets_append.py`; new-row insertion lives in
:file:`test_sheets_rows.py`.
"""

from unittest.mock import MagicMock

import allure
from gspread.utils import ValueRenderOption

from dinary.services.sheets import (
    _is_numeric,
    _year_from_a_value,
    fetch_row_years,
    find_category_row,
    find_month_range,
    fmt_amount,
    get_month_rate,
)

from _sheets_helpers import HEADER, SAMPLE_SHEET


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

    HEADER = HEADER

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

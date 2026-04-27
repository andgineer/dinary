"""Append-side tests for ``dinary.services.sheets``.

Pin the data-safety contract for ``append_to_amount_formula``
(must always *append* to the formula in column B, never overwrite
the existing value, and the result must always render as a Google
Sheets formula starting with ``=``) and ``append_comment`` (must
preserve the existing semicolon-delimited comment list in column F).

Read-side helpers live in :file:`test_sheets_read.py`; new-row
insertion lives in :file:`test_sheets_rows.py`.
"""

from unittest.mock import MagicMock

import allure
from gspread.utils import ValueRenderOption

from dinary.services.sheets import append_comment, append_to_amount_formula


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

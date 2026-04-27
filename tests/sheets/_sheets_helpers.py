"""Shared header/grid fixtures + worksheet stub for the split
``test_sheets_*.py`` files.

Underscore prefix keeps pytest from collecting this as a test
module. Re-export only the surface the split files actually need —
the layout constant ``HEADER``, the canonical ``SAMPLE_SHEET`` grid,
and the ``_make_worksheet`` stub that mirrors the gspread surface
under test.
"""

from unittest.mock import MagicMock

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


__all__ = ["HEADER", "SAMPLE_SHEET", "_make_worksheet"]

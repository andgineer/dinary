"""New-row insertion tests for ``dinary.services.sheets``.

Pin the ``ensure_category_row`` contract on top of three layouts:

* the canonical flat-table grid (``SAMPLE_SHEET``);
* legacy copy-paste month blocks where a single month carries a
  full snapshot of the active categories (covered by
  ``TestEnsureCategoryRowLegacySheet``);
* the multi-year layout where row ordering is driven by an aligned
  ``years_by_row`` from a separate unformatted column-A read.

Read-side helpers live in :file:`test_sheets_read.py`; in-place
column writes (formula + comment append) live in
:file:`test_sheets_append.py`.
"""

from datetime import date

import allure

from dinary.services.sheets import ensure_category_row

from _sheets_helpers import HEADER, SAMPLE_SHEET, _make_worksheet


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
@allure.feature("Row Insertion (multi-year)")
class TestEnsureCategoryRowMultiYear:
    """``ensure_category_row`` must respect ``years_by_row`` so that a
    write for a different year does not smear onto an existing
    year's row. The grid mirrors :class:`TestYearAwareMatching`'s
    layout in :file:`test_sheets_read.py`."""

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

    def test_creates_new_year_block(self):
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

    def test_inserts_between_year_blocks(self):
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

    def test_inserts_oldest_year_at_bottom(self):

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

    def test_finds_existing_year_match(self):
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

    def test_inserts_when_only_other_year_exists(self):
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

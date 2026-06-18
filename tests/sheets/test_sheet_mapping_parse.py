"""Parsing tests for the ``map`` worksheet tab.

Covers ``parse_rows`` (validation, wildcards, did-you-mean hints).
Resolution, catalog loading, and reload pipeline live in sibling
``test_sheet_mapping_*.py`` files.
"""

import allure
import pytest

from dinary.sheets import sheet_mapping

from _sheet_mapping_helpers import (  # noqa: F401  (autouse + helpers)
    _catalog,
    db,
)


@allure.epic("Sheets Sync")
@allure.feature("Sheet mapping")
class TestParseRows:
    def test_happy_path(self):
        cats, events, tags = _catalog()
        rows = sheet_mapping.parse_rows(
            [
                ["food", "*", "*", "Food", "*"],
                ["car", "*", "*", "Car", "Transport"],
            ],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        assert len(rows) == 2
        assert [r.row_order for r in rows] == [1, 2]
        assert rows[0].category_id == 1
        assert rows[0].sheet_category == "Food"
        assert rows[0].sheet_group == "*"
        assert rows[1].sheet_category == "Car"
        assert rows[1].sheet_group == "Transport"

    def test_wildcards_and_blanks_are_equivalent(self):
        """Blank cells and ``*`` collapse to the same wildcard sentinel
        across every column — A/B/C (catalog dimensions) and D/E
        (output columns). Operators can leave cells empty for
        readability without changing resolver behaviour.
        """
        cats, events, tags = _catalog()
        rows = sheet_mapping.parse_rows(
            [
                ["*", "", "", "*", "travel"],
                ["food", "", "", "", ""],
            ],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        assert len(rows) == 2
        assert rows[0].category_id is None
        assert rows[0].event_id is None
        assert rows[0].tag_ids == ()
        assert rows[0].sheet_category == "*"
        assert rows[0].sheet_group == "travel"
        # Second row: empty D / E must become WILDCARD so the resolver
        # skips that row for both output columns (matches the module
        # docstring and the envelope-inheritance semantics the
        # operator authoring the tab expects).
        assert rows[1].sheet_category == "*"
        assert rows[1].sheet_group == "*"

    def test_all_wildcard_row_skipped(self):
        """A row that wildcards *everything* contributes nothing and
        would only burn a ``row_order``. parse_rows must skip it so
        reload diagnostics reflect meaningful rules only."""
        cats, events, tags = _catalog()
        rows = sheet_mapping.parse_rows(
            [
                ["food", "*", "*", "Food", "*"],
                ["*", "*", "*", "*", "*"],
                ["car", "*", "*", "Car", "*"],
            ],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        assert [r.row_order for r in rows] == [1, 2]
        assert [r.sheet_category for r in rows] == ["Food", "Car"]

    def test_tags_cell_parsed(self):
        cats, events, tags = _catalog()
        rows = sheet_mapping.parse_rows(
            [["food", "*", "dog, anna", "FoodPet", "*"]],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        assert rows[0].tag_ids == (1, 2)

    def test_event_resolved_by_name(self):
        cats, events, tags = _catalog()
        rows = sheet_mapping.parse_rows(
            [["*", "vacation-2026", "*", "*", "travel"]],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        assert rows[0].event_id == 1

    def test_blank_rows_skipped(self):
        cats, events, tags = _catalog()
        rows = sheet_mapping.parse_rows(
            [
                ["food", "*", "*", "Food", "*"],
                ["", "", "", "", ""],
                ["car", "*", "*", "Car", "*"],
            ],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        assert [r.row_order for r in rows] == [1, 2]
        assert rows[1].category_id == 2

    def test_unknown_category_raises(self):
        cats, events, tags = _catalog()
        with pytest.raises(sheet_mapping.MapTabError, match="category"):
            sheet_mapping.parse_rows(
                [["ghost", "*", "*", "X", "*"]],
                cat_id_by_name=cats,
                event_id_by_name=events,
                tag_id_by_name=tags,
            )

    def test_unknown_event_raises(self):
        cats, events, tags = _catalog()
        with pytest.raises(sheet_mapping.MapTabError, match="event"):
            sheet_mapping.parse_rows(
                [["*", "ghost_event", "*", "X", "*"]],
                cat_id_by_name=cats,
                event_id_by_name=events,
                tag_id_by_name=tags,
            )

    def test_unknown_tag_raises(self):
        cats, events, tags = _catalog()
        with pytest.raises(sheet_mapping.MapTabError, match="tag"):
            sheet_mapping.parse_rows(
                [["food", "*", "ghost_tag", "Food", "*"]],
                cat_id_by_name=cats,
                event_id_by_name=events,
                tag_id_by_name=tags,
            )

    def test_case_only_mismatch_on_category_surfaces_did_you_mean(self):
        cats, events, tags = _catalog()
        with pytest.raises(sheet_mapping.MapTabError) as excinfo:
            sheet_mapping.parse_rows(
                [["Food", "*", "*", "Food", "*"]],
                cat_id_by_name=cats,
                event_id_by_name=events,
                tag_id_by_name=tags,
            )
        msg = str(excinfo.value)
        assert "did you mean" in msg
        assert "'food'" in msg

    def test_case_only_mismatch_on_tag_surfaces_did_you_mean(self):
        cats, events, tags = _catalog()
        with pytest.raises(sheet_mapping.MapTabError) as excinfo:
            sheet_mapping.parse_rows(
                [["food", "*", "Anna", "Food", "*"]],
                cat_id_by_name=cats,
                event_id_by_name=events,
                tag_id_by_name=tags,
            )
        msg = str(excinfo.value)
        assert "did you mean" in msg
        assert "'anna'" in msg

    def test_unrelated_missing_category_has_no_hint(self):
        cats, events, tags = _catalog()
        with pytest.raises(sheet_mapping.MapTabError) as excinfo:
            sheet_mapping.parse_rows(
                [["ghost", "*", "*", "X", "*"]],
                cat_id_by_name=cats,
                event_id_by_name=events,
                tag_id_by_name=tags,
            )
        assert "did you mean" not in str(excinfo.value)

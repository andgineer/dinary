"""Tests for ``db.logging_projection``. Pins: first non-``*`` wins per column; tag
matching requires the row's tag set to be a subset of the expense's; partial matches
keep the resolved column rather than being discarded; unknown ``category_id`` returns
None so the drain worker poisons the row; no rule firing falls back to
``(categories.name, "")``."""

import allure
import pytest

from dinary.db import storage
from dinary.db.catalog import logging_projection

from _ledger_repo_helpers import (  # noqa: F401  (autouse + fixtures)
    data_dir,
    fresh_db,
)


@allure.epic("Sheets Sync")
@allure.feature("Sheet logging")
class TestLoggingProjection:
    @pytest.fixture
    def logging_setup(self, fresh_db):
        """Three ``sheet_mapping`` rows: (1) tag1-gated envelope, (2) event-gated
        envelope, (3) catch-all category — exercises "first non-``*`` wins"."""
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'food', 1)",
            )
            con.execute("INSERT INTO tags (id, name) VALUES (1, 'tag1')")
            con.execute("INSERT INTO tags (id, name) VALUES (2, 'tag2')")
            con.execute(
                "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled)"
                " VALUES (1, 'evt', '2026-01-01', '2026-12-31', 1)",
            )
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group) VALUES (1, 1, NULL, '*', 'WithTag')",
            )
            con.execute("INSERT INTO sheet_mapping_tags VALUES (1, 1)")
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group) VALUES (2, 1, 1, '*', 'WithEvt')",
            )
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group) VALUES (3, 1, NULL, 'CatA', '*')",
            )
            con.commit()
        finally:
            con.close()

    def test_event_row_wins_sheet_group(self, logging_setup):
        con = storage.get_connection()
        try:
            # No tags: row_order=1 skipped; row_order=2 matches on event_id
            # and fills sheet_group=WithEvt; row_order=3 supplies sheet_category=CatA.
            result = logging_projection(
                con,
                category_id=1,
                event_id=1,
                tag_ids=[],
            )
            assert result == ("CatA", "WithEvt")
        finally:
            con.close()

    def test_tag_row_wins_sheet_group(self, logging_setup):
        con = storage.get_connection()
        try:
            # No event, tag 'tag1' present: row_order=1 fills sheet_group=WithTag;
            # row_order=3 supplies sheet_category=CatA.
            result = logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[1],
            )
            assert result == ("CatA", "WithTag")
        finally:
            con.close()

    def test_partial_resolution_keeps_resolved_column(self, logging_setup):
        con = storage.get_connection()
        try:
            # Only row 3 matches; the resolved sheet_category=CatA is kept and
            # sheet_group falls back to empty string rather than dropping CatA too.
            result = logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[2],
            )
            assert result == ("CatA", "")
        finally:
            con.close()

    def test_unknown_category_returns_none(self, logging_setup):
        con = storage.get_connection()
        try:
            result = logging_projection(
                con,
                category_id=999,
                event_id=None,
                tag_ids=[],
            )
            assert result is None
        finally:
            con.close()

    def test_no_event_no_tags_fills_envelope_with_empty_string(self, logging_setup):
        con = storage.get_connection()
        try:
            result = logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[],
            )
            assert result == ("CatA", "")
        finally:
            con.close()

    def test_no_mapping_rule_falls_back_to_category_name(self, fresh_db):
        """No sheet_mapping rule fires: falls back to (categories.name, '')."""
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'food', 1)",
            )
            con.commit()
            assert logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[],
            ) == ("food", "")
        finally:
            con.close()

    def test_both_columns_resolved_when_wildcard_row_fills_sheet_group(self, fresh_db):
        """A dedicated envelope-fill row + a category row together resolve
        both columns and produce a non-None result."""
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'food', 1)",
            )
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group)"
                " VALUES (1, NULL, NULL, '*', 'envelope')",
            )
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group)"
                " VALUES (2, 1, NULL, 'CatA', '*')",
            )
            con.commit()
            assert logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[],
            ) == ("CatA", "envelope")
        finally:
            con.close()

    def test_event_wildcard_row_matches_specific_event(self, fresh_db):
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'food', 1)",
            )
            con.execute(
                "INSERT INTO events (id, name, date_from, date_to,"
                " auto_attach_enabled) VALUES (1, 'vacation-2026',"
                " '2026-01-01', '2026-04-20', 1)",
            )
            # Row 1 (specific event) must win for event_id=1; row 2 for event_id=None.
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group)"
                " VALUES (1, 1, 1, 'Trips', '*')",
            )
            con.execute(
                "INSERT INTO sheet_mapping (row_order, category_id, event_id,"
                " sheet_category, sheet_group)"
                " VALUES (2, 1, NULL, 'Default', '*')",
            )
            con.commit()
            assert logging_projection(
                con,
                category_id=1,
                event_id=1,
                tag_ids=[],
            ) == ("Trips", "")
            assert logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[],
            ) == ("Default", "")
        finally:
            con.close()

"""Tests for ``ledger_repo.logging_projection``.

The drain worker calls this helper to decide where each expense
lands on the logging sheet. The contract pins:

* "First non-``*`` wins per column" applied independently across
  ``sheet_category`` and ``sheet_group``.
* Tag matching requires the row's tag set to be a *subset* of the
  expense tags.
* Partial matches keep the resolved column instead of being
  discarded — dropping a column we already populated would be
  strictly worse than the empty-string fallback.
* Unknown ``category_id`` returns ``None`` so the drain worker
  poisons the queued row instead of logging to a bogus target.
* When no rule fires at all, both columns fall back to
  ``(categories.name, "")`` — the in-helper default that replaced
  the old "return None and let the caller fall back" contract.

Sibling :file:`test_ledger_repo_catalog.py` covers connection
lifecycle, ``catalog_version``, ``list_categories``, sheet-mapping
3D resolution, and ``get_category_name``.
"""

import allure
import pytest

from dinary.services import ledger_repo

from _ledger_repo_helpers import (  # noqa: F401  (autouse + fixtures)
    _tmp_data_dir,
    fresh_db,
)


@allure.epic("Ledger repo")
@allure.feature("Logging projection (sheet_mapping)")
class TestLoggingProjection:
    @pytest.fixture
    def logging_setup(self, fresh_db):
        """Seed one category with three ``sheet_mapping`` rows exercising
        the "first non-``*`` wins per column" resolver:

        row_order=1: category=еда, tag ``tag1`` required, Расходы=``*``,
                     Конверт=``WithTag``
        row_order=2: category=еда, event=``evt``, Расходы=``*``,
                     Конверт=``WithEvt``
        row_order=3: category=еда (catch-all), Расходы=``CatA``,
                     Конверт=``*``
        """
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'еда', 1)",
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

    def test_event_row_wins_конверт(self, logging_setup):
        con = ledger_repo.get_connection()
        try:
            # No tags: row_order=1 skipped; row_order=2 matches on event_id
            # and fills Конверт=WithEvt; row_order=3 supplies Расходы=CatA.
            result = ledger_repo.logging_projection(
                con,
                category_id=1,
                event_id=1,
                tag_ids=[],
            )
            assert result == ("CatA", "WithEvt")
        finally:
            con.close()

    def test_tag_row_wins_конверт(self, logging_setup):
        con = ledger_repo.get_connection()
        try:
            # No event, tag 'tag1' present: row_order=1 fills Конверт=WithTag;
            # row_order=3 supplies Расходы=CatA.
            result = ledger_repo.logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[1],
            )
            assert result == ("CatA", "WithTag")
        finally:
            con.close()

    def test_partial_resolution_keeps_resolved_column(self, logging_setup):
        con = ledger_repo.get_connection()
        try:
            # tag 'tag2' is not required by any row, no event: rows 1 and 2
            # are skipped, row 3 fills Расходы=CatA but leaves Конверт as
            # ``*``. The resolver keeps the partial match and fills the
            # missing ``sheet_group`` with the empty-string fallback —
            # dropping CatA would be strictly worse since we already
            # picked a better-than-default value for that column.
            result = ledger_repo.logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[2],
            )
            assert result == ("CatA", "")
        finally:
            con.close()

    def test_unknown_category_returns_none(self, logging_setup):
        con = ledger_repo.get_connection()
        try:
            # category_id=999 has no rows at all and no canonical name —
            # the projection returns None so the drain worker can poison
            # the queued row rather than logging to a bogus target.
            result = ledger_repo.logging_projection(
                con,
                category_id=999,
                event_id=None,
                tag_ids=[],
            )
            assert result is None
        finally:
            con.close()

    def test_no_event_no_tags_fills_envelope_with_empty_string(self, logging_setup):
        con = ledger_repo.get_connection()
        try:
            # Same shape as ``test_partial_resolution_keeps_resolved_column``
            # but with no tags at all; rows 1 and 2 require tag/event and
            # are skipped, row 3 assigns Расходы and leaves Конверт as
            # ``*``. The resolver fills Конверт with the empty-string
            # fallback while keeping the explicit ``CatA`` mapping.
            result = ledger_repo.logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[],
            )
            assert result == ("CatA", "")
        finally:
            con.close()

    def test_no_mapping_rule_falls_back_to_category_name(self, fresh_db):
        """When no sheet_mapping rule fires at all, both columns fall
        back: ``sheet_category`` = categories.name, ``sheet_group`` = ''.
        This replaces the old "return None and let the caller fall
        back" contract with an in-helper default so partial matches
        never get discarded.
        """
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'еда', 1)",
            )
            con.commit()
            assert ledger_repo.logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[],
            ) == ("еда", "")
        finally:
            con.close()

    def test_both_columns_resolved_when_wildcard_row_fills_конверт(self, fresh_db):
        """A dedicated envelope-fill row + a category row together resolve
        both columns and produce a non-None result."""
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'еда', 1)",
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
            assert ledger_repo.logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[],
            ) == ("CatA", "envelope")
        finally:
            con.close()

    def test_event_wildcard_row_matches_specific_event(self, fresh_db):
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'еда', 1)",
            )
            con.execute(
                "INSERT INTO events (id, name, date_from, date_to,"
                " auto_attach_enabled) VALUES (1, 'отпуск-2026',"
                " '2026-01-01', '2026-04-20', 1)",
            )
            # Row 1 (specific event) must win over the wildcard row for
            # event_id=1; row 2 must still fire for event_id=None.
            # ``sheet_group`` is left wildcard and therefore falls back
            # to the empty-string sentinel in both cases.
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
            assert ledger_repo.logging_projection(
                con,
                category_id=1,
                event_id=1,
                tag_ids=[],
            ) == ("Trips", "")
            assert ledger_repo.logging_projection(
                con,
                category_id=1,
                event_id=None,
                tag_ids=[],
            ) == ("Default", "")
        finally:
            con.close()

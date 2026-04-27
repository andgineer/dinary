"""Resolution + DB-projection tests for ``sheet_mapping``.

Covers:

* ``resolve_projection`` — pure "first non-``*`` wins per column"
  algorithm, applied independently across sheet_category and
  sheet_group.
* ``_atomic_swap`` — DB transaction: wipes the previous map and
  inserts the new one inside a single connection so a mid-way crash
  cannot leave a half-applied mapping.
* ``_load_catalog`` — must ignore ``is_active = FALSE`` so that
  hiding a single tag from the PWA picker does not wedge map-tab
  reload.
* event ``auto_tags`` JSON helpers — id resolution, malformed JSON
  tolerance, inactive-tag survival.

Parsing lives in :file:`test_sheet_mapping_parse.py`; the reload /
``ensure_fresh`` pipeline lives in
:file:`test_sheet_mapping_reload.py`.
"""

import allure

from dinary.services import ledger_repo, sheet_mapping

from _sheet_mapping_helpers import (  # noqa: F401  (autouse + helpers)
    _catalog,
    _tmp_db,
)


@allure.epic("SheetMapping")
@allure.feature("resolve_projection")
class TestResolveProjection:
    def test_first_non_star_wins_per_column_independently(self):
        """Column A sets Расходы; column B sets Конверт; the resolver
        must take the first non-``*`` for each column independently
        even when they come from different rows."""
        rows = [
            sheet_mapping.MapRow(
                row_order=1,
                category_id=None,
                event_id=None,
                tag_ids=(3,),
                sheet_category=sheet_mapping.WILDCARD,
                sheet_group="путешествия",
            ),
            sheet_mapping.MapRow(
                row_order=2,
                category_id=1,
                event_id=None,
                tag_ids=(),
                sheet_category="Food",
                sheet_group=sheet_mapping.WILDCARD,
            ),
        ]
        result = sheet_mapping.resolve_projection(
            rows,
            category_id=1,
            event_id=None,
            tag_ids={3},
            default_sheet_category="еда",
        )
        assert result == ("Food", "путешествия")

    def test_falls_back_to_default_when_no_row_decides(self):
        result = sheet_mapping.resolve_projection(
            [],
            category_id=1,
            event_id=None,
            tag_ids=set(),
            default_sheet_category="еда",
        )
        assert result == ("еда", "")

    def test_category_mismatch_skips_row(self):
        rows = [
            sheet_mapping.MapRow(
                row_order=1,
                category_id=2,
                event_id=None,
                tag_ids=(),
                sheet_category="Car",
                sheet_group="Transport",
            ),
        ]
        result = sheet_mapping.resolve_projection(
            rows,
            category_id=1,
            event_id=None,
            tag_ids=set(),
            default_sheet_category="еда",
        )
        assert result == ("еда", "")

    def test_tag_subset_required(self):
        """Row requires both ``собака`` AND ``аня``; an expense with
        only ``собака`` must not match — the resolver falls through
        to the default."""
        rows = [
            sheet_mapping.MapRow(
                row_order=1,
                category_id=None,
                event_id=None,
                tag_ids=(1, 2),
                sheet_category=sheet_mapping.WILDCARD,
                sheet_group="dog+kid",
            ),
        ]
        result = sheet_mapping.resolve_projection(
            rows,
            category_id=1,
            event_id=None,
            tag_ids={1},
            default_sheet_category="еда",
        )
        assert result == ("еда", "")


@allure.epic("SheetMapping")
@allure.feature("_atomic_swap")
class TestAtomicSwap:
    def test_swap_replaces_sheet_mapping(self):
        cats, events, tags = _catalog()
        rows = sheet_mapping.parse_rows(
            [["еда", "*", "*", "Food", "Ess"]],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        con = ledger_repo.get_connection()
        try:
            sheet_mapping._atomic_swap(con, rows)
            result = con.execute(
                "SELECT row_order, category_id, sheet_category, sheet_group"
                " FROM sheet_mapping ORDER BY row_order",
            ).fetchall()
        finally:
            con.close()
        assert result == [(1, 1, "Food", "Ess")]

    def test_swap_wipes_previous_rows(self):
        cats, events, tags = _catalog()
        first = sheet_mapping.parse_rows(
            [["еда", "*", "*", "Food", "*"]],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        second = sheet_mapping.parse_rows(
            [["машина", "*", "*", "Car", "*"]],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        con = ledger_repo.get_connection()
        try:
            sheet_mapping._atomic_swap(con, first)
            sheet_mapping._atomic_swap(con, second)
            rows = con.execute(
                "SELECT category_id, sheet_category FROM sheet_mapping",
            ).fetchall()
        finally:
            con.close()
        assert rows == [(2, "Car")]

    def test_swap_survives_preexisting_tag_rows(self):
        """When a prior swap inserted ``sheet_mapping_tags`` rows, a
        follow-up swap must successfully DELETE both them and their
        parent ``sheet_mapping`` rows. This test exercises the
        sequence ``swap with tags → swap without tags`` on the same
        cursor to pin that behaviour.
        """
        cats, events, tags = _catalog()
        first = sheet_mapping.parse_rows(
            [["еда", "*", "собака, аня", "Food", "dog+kid"]],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        second = sheet_mapping.parse_rows(
            [["машина", "*", "путешествия", "Car", "путешествия"]],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        con = ledger_repo.get_connection()
        try:
            sheet_mapping._atomic_swap(con, first)
            first_tags = con.execute(
                "SELECT mapping_row_order, tag_id FROM sheet_mapping_tags"
                " ORDER BY mapping_row_order, tag_id",
            ).fetchall()
            sheet_mapping._atomic_swap(con, second)
            rows = con.execute(
                "SELECT category_id, sheet_category, sheet_group FROM sheet_mapping",
            ).fetchall()
            new_tags = con.execute(
                "SELECT mapping_row_order, tag_id FROM sheet_mapping_tags"
                " ORDER BY mapping_row_order, tag_id",
            ).fetchall()
        finally:
            con.close()
        assert first_tags == [(1, 1), (1, 2)]
        assert rows == [(2, "Car", "путешествия")]
        assert new_tags == [(1, 3)]


@allure.epic("SheetMapping")
@allure.feature("_load_catalog (inactive-tolerant)")
class TestLoadCatalog:
    """``_load_catalog`` must include inactive categories / events /
    tags. ``is_active = FALSE`` is a "hide from the ручной пикер"
    affordance and must not break map-tab reload — otherwise the
    operator hiding a single auto-tag (e.g. "отпуск") wedges the
    whole reload pipeline with a ``MapTabError`` on every tick.
    """

    def test_loads_inactive_tags(self):
        con = ledger_repo.get_connection()
        try:
            con.execute("UPDATE tags SET is_active = FALSE WHERE id = 3")
            _, _, tag_id_by_name = sheet_mapping._load_catalog(con)
        finally:
            con.close()
        assert tag_id_by_name.get("путешествия") == 3

    def test_loads_inactive_categories(self):
        con = ledger_repo.get_connection()
        try:
            con.execute("UPDATE categories SET is_active = FALSE WHERE id = 2")
            cat_id_by_name, _, _ = sheet_mapping._load_catalog(con)
        finally:
            con.close()
        assert cat_id_by_name.get("машина") == 2

    def test_loads_inactive_events(self):
        con = ledger_repo.get_connection()
        try:
            con.execute("UPDATE events SET is_active = FALSE WHERE id = 1")
            _, event_id_by_name, _ = sheet_mapping._load_catalog(con)
        finally:
            con.close()
        assert event_id_by_name.get("отпуск-2026") == 1

    def test_parse_rows_accepts_inactive_tag_reference(self):
        """End-to-end: when the catalog loader picks up an inactive tag,
        ``parse_rows`` must accept a map-tab row that references it.
        This pins the exact user-reported failure mode — a map-tab
        row ``*,*,отпуск,*,путешествия`` surviving the operator
        deactivating "отпуск" via the PWA "Управлять" list.
        """
        con = ledger_repo.get_connection()
        try:
            con.execute("UPDATE tags SET is_active = FALSE WHERE id = 3")
            cats, events, tags = sheet_mapping._load_catalog(con)
        finally:
            con.close()
        rows = sheet_mapping.parse_rows(
            [["*", "*", "путешествия", "*", "путешествия"]],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        assert len(rows) == 1
        assert rows[0].tag_ids == (3,)


@allure.epic("SheetMapping")
@allure.feature("event auto_tags helpers")
class TestEventAutoTags:
    def test_resolve_returns_active_tag_ids(self):
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "UPDATE events SET auto_tags = '[\"путешествия\"]' WHERE id = 1",
            )
            ids = sheet_mapping.resolve_event_auto_tag_ids(con, 1)
        finally:
            con.close()
        assert ids == [3]

    def test_missing_event_returns_empty(self):
        con = ledger_repo.get_connection()
        try:
            assert sheet_mapping.resolve_event_auto_tag_ids(con, 999) == []
        finally:
            con.close()

    def test_malformed_json_is_treated_as_empty(self):
        con = ledger_repo.get_connection()
        try:
            con.execute("UPDATE events SET auto_tags = 'not-json' WHERE id = 1")
            assert sheet_mapping.resolve_event_auto_tag_ids(con, 1) == []
        finally:
            con.close()

    def test_unknown_tag_names_are_dropped(self):
        con = ledger_repo.get_connection()
        try:
            con.execute(
                'UPDATE events SET auto_tags = \'["путешествия", "missing"]\' WHERE id = 1',
            )
            ids = sheet_mapping.resolve_event_auto_tag_ids(con, 1)
        finally:
            con.close()
        assert ids == [3]

    def test_inactive_tag_name_still_resolves(self):
        """``tags.is_active = FALSE`` means "hide from the ручной
        пикер", not "retire from event-driven auto-attach". An event
        whose ``auto_tags`` names an inactive tag must still get that
        tag attached on expense write — otherwise hiding a
        vacation-only tag like "отпуск" silently breaks the
        event-based auto-attach pipeline (the direct complaint behind
        this regression test)."""
        con = ledger_repo.get_connection()
        try:
            con.execute("UPDATE tags SET is_active = FALSE WHERE id = 3")
            con.execute(
                "UPDATE events SET auto_tags = '[\"путешествия\"]' WHERE id = 1",
            )
            ids = sheet_mapping.resolve_event_auto_tag_ids(con, 1)
        finally:
            con.close()
        assert ids == [3]

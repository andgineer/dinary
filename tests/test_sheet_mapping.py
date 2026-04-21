"""Tests for ``sheet_mapping`` — parsing + atomic swap of the ``map`` tab.

The drain loop reads from ``sheet_mapping`` to decide where each
expense lands in the logging sheet. Those rows are derived from a
human-edited ``map`` worksheet tab via this module.

Tests pin:

* Row validation: unknown category / event / tag names raise
  ``MapTabError``; blank rows become visual separators (skipped
  without error); blank cells + ``*`` are both wildcards.
* ``_atomic_swap`` wipes and repopulates the DB tables inside a
  single transaction — a mid-way crash leaves the previous mapping
  in place.
* ``parse_rows`` ``row_order`` is contiguous starting from 1,
  matching the "first non-``*`` wins per column" resolver contract.
* ``reload_now`` captures ``modifiedTime`` both before and after the
  reload; if the value shifts during the read, the cache is not
  advanced so the next ``ensure_fresh`` retries.
* The pure ``resolve_projection`` implements "first non-``*`` wins
  per column" independently across sheet_category and sheet_group.
"""

from unittest.mock import MagicMock, patch

import allure
import pytest

from dinary.config import settings
from dinary.services import duckdb_repo, sheet_mapping


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "DB_PATH", tmp_path / "dinary.duckdb")
    duckdb_repo.init_db()
    con = duckdb_repo.get_connection()
    try:
        con.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active)"
            " VALUES (1, 'g', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'еда', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (2, 'машина', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled, is_active)"
            " VALUES (1, 'отпуск-2026', '2026-01-01', '2026-04-20', TRUE, TRUE)",
        )
        con.execute("INSERT INTO tags (id, name, is_active) VALUES (1, 'собака', TRUE)")
        con.execute("INSERT INTO tags (id, name, is_active) VALUES (2, 'аня', TRUE)")
        con.execute(
            "INSERT INTO tags (id, name, is_active) VALUES (3, 'путешествия', TRUE)",
        )
    finally:
        con.close()


def _catalog():
    return (
        {"еда": 1, "машина": 2},
        {"отпуск-2026": 1},
        {"собака": 1, "аня": 2, "путешествия": 3},
    )


@allure.epic("SheetMapping")
@allure.feature("parse_rows")
class TestParseRows:
    def test_happy_path(self):
        cats, events, tags = _catalog()
        rows = sheet_mapping.parse_rows(
            [
                ["еда", "*", "*", "Food", "*"],
                ["машина", "*", "*", "Car", "Transport"],
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
                ["*", "", "", "*", "путешествия"],
                ["еда", "", "", "", ""],
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
        assert rows[0].sheet_group == "путешествия"
        # Second row: empty D / E must become WILDCARD so the resolver
        # skips that row for both output columns (matches the module
        # docstring and the Конверт-inheritance semantics the
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
                ["еда", "*", "*", "Food", "*"],
                ["*", "*", "*", "*", "*"],
                ["машина", "*", "*", "Car", "*"],
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
            [["еда", "*", "собака, аня", "FoodPet", "*"]],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        assert rows[0].tag_ids == (1, 2)

    def test_event_resolved_by_name(self):
        cats, events, tags = _catalog()
        rows = sheet_mapping.parse_rows(
            [["*", "отпуск-2026", "*", "*", "путешествия"]],
            cat_id_by_name=cats,
            event_id_by_name=events,
            tag_id_by_name=tags,
        )
        assert rows[0].event_id == 1

    def test_blank_rows_skipped(self):
        cats, events, tags = _catalog()
        rows = sheet_mapping.parse_rows(
            [
                ["еда", "*", "*", "Food", "*"],
                ["", "", "", "", ""],
                ["машина", "*", "*", "Car", "*"],
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
                [["еда", "*", "ghost_tag", "Food", "*"]],
                cat_id_by_name=cats,
                event_id_by_name=events,
                tag_id_by_name=tags,
            )

    def test_case_only_mismatch_on_category_surfaces_did_you_mean(self):
        cats, events, tags = _catalog()
        with pytest.raises(sheet_mapping.MapTabError) as excinfo:
            sheet_mapping.parse_rows(
                [["Еда", "*", "*", "Food", "*"]],
                cat_id_by_name=cats,
                event_id_by_name=events,
                tag_id_by_name=tags,
            )
        msg = str(excinfo.value)
        assert "did you mean" in msg
        assert "'еда'" in msg

    def test_case_only_mismatch_on_tag_surfaces_did_you_mean(self):
        cats, events, tags = _catalog()
        with pytest.raises(sheet_mapping.MapTabError) as excinfo:
            sheet_mapping.parse_rows(
                [["еда", "*", "Аня", "Food", "*"]],
                cat_id_by_name=cats,
                event_id_by_name=events,
                tag_id_by_name=tags,
            )
        msg = str(excinfo.value)
        assert "did you mean" in msg
        assert "'аня'" in msg

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
        con = duckdb_repo.get_connection()
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
        con = duckdb_repo.get_connection()
        try:
            sheet_mapping._atomic_swap(con, first)
            sheet_mapping._atomic_swap(con, second)
            rows = con.execute(
                "SELECT category_id, sheet_category FROM sheet_mapping",
            ).fetchall()
        finally:
            con.close()
        assert rows == [(2, "Car")]


def _fake_worksheet(raw_rows_including_header):
    ws = MagicMock()
    ws.get_all_values.return_value = raw_rows_including_header
    return ws


def _fake_sheet(ws):
    sh = MagicMock()
    sh.worksheet.return_value = ws
    return sh


@allure.epic("SheetMapping")
@allure.feature("reload_now modifiedTime ordering")
class TestReloadNowOrdering:
    def test_cache_updated_when_modified_time_stable(self, monkeypatch):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "SSID")
        sheet_mapping._reset_cache()

        ws = _fake_worksheet(
            [
                ["category", "event", "tags", "Расходы", "Конверт"],
                ["еда", "*", "*", "Food", "*"],
            ],
        )
        sh = _fake_sheet(ws)

        with (
            patch.object(sheet_mapping, "get_sheet", return_value=sh),
            patch.object(
                sheet_mapping,
                "drive_get_modified_time",
                return_value="2026-04-20T10:00:00Z",
            ),
        ):
            summary = sheet_mapping.reload_now()

        assert summary["modified_time_cached"] is True
        assert sheet_mapping._cache_state() == "2026-04-20T10:00:00Z"

    def test_cache_not_updated_when_modified_time_shifts_during_read(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "SSID")
        sheet_mapping._reset_cache()

        ws = _fake_worksheet(
            [
                ["category", "event", "tags", "Расходы", "Конверт"],
                ["еда", "*", "*", "Food", "*"],
            ],
        )
        sh = _fake_sheet(ws)

        modified_times = iter(["2026-04-20T10:00:00Z", "2026-04-20T10:00:05Z"])
        with (
            patch.object(sheet_mapping, "get_sheet", return_value=sh),
            patch.object(
                sheet_mapping,
                "drive_get_modified_time",
                side_effect=lambda _ssid: next(modified_times),
            ),
        ):
            summary = sheet_mapping.reload_now()

        assert summary["modified_time_cached"] is False
        assert sheet_mapping._cache_state() is None

    def test_check_after_false_skips_second_drive_get_and_caches_eagerly(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "SSID")
        sheet_mapping._reset_cache()

        ws = _fake_worksheet(
            [
                ["category", "event", "tags", "Расходы", "Конверт"],
                ["еда", "*", "*", "Food", "*"],
            ],
        )
        sh = _fake_sheet(ws)
        drive_mock = MagicMock(return_value="2026-04-20T10:00:00Z")

        with (
            patch.object(sheet_mapping, "get_sheet", return_value=sh),
            patch.object(sheet_mapping, "drive_get_modified_time", drive_mock),
        ):
            summary = sheet_mapping.reload_now(check_after=False)

        assert drive_mock.call_count == 1
        assert summary["modified_time_cached"] is True
        assert summary["modified_time"] == "2026-04-20T10:00:00Z"
        assert sheet_mapping._cache_state() == "2026-04-20T10:00:00Z"


@allure.epic("SheetMapping")
@allure.feature("ensure_fresh skips when modifiedTime unchanged")
class TestEnsureFresh:
    def test_ensure_fresh_is_noop_when_cache_matches_drive(self, monkeypatch):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "SSID")
        sheet_mapping._reset_cache()

        ws = _fake_worksheet(
            [
                ["category", "event", "tags", "Расходы", "Конверт"],
                ["еда", "*", "*", "Food", "*"],
            ],
        )
        sh = _fake_sheet(ws)
        with (
            patch.object(sheet_mapping, "get_sheet", return_value=sh),
            patch.object(
                sheet_mapping,
                "drive_get_modified_time",
                return_value="2026-04-20T10:00:00Z",
            ),
        ):
            sheet_mapping.reload_now()

        assert sheet_mapping._cache_state() == "2026-04-20T10:00:00Z"

        get_sheet_mock = MagicMock()
        with (
            patch.object(sheet_mapping, "get_sheet", get_sheet_mock),
            patch.object(
                sheet_mapping,
                "drive_get_modified_time",
                return_value="2026-04-20T10:00:00Z",
            ),
        ):
            sheet_mapping.ensure_fresh()

        get_sheet_mock.assert_not_called()

    def test_ensure_fresh_triggers_reload_when_drive_reports_newer(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "SSID")
        sheet_mapping._reset_cache()

        ws = _fake_worksheet(
            [
                ["category", "event", "tags", "Расходы", "Конверт"],
                ["еда", "*", "*", "Food", "*"],
            ],
        )
        sh = _fake_sheet(ws)

        with (
            patch.object(sheet_mapping, "get_sheet", return_value=sh),
            patch.object(
                sheet_mapping,
                "drive_get_modified_time",
                return_value="2026-04-20T10:00:00Z",
            ),
        ):
            sheet_mapping.reload_now()

        sh.worksheet.reset_mock()
        ws.get_all_values.reset_mock()
        with (
            patch.object(sheet_mapping, "get_sheet", return_value=sh),
            patch.object(
                sheet_mapping,
                "drive_get_modified_time",
                return_value="2026-04-20T11:00:00Z",
            ),
        ):
            sheet_mapping.ensure_fresh()

        ws.get_all_values.assert_called_once()
        assert sheet_mapping._cache_state() == "2026-04-20T11:00:00Z"


@allure.epic("SheetMapping")
@allure.feature("event auto_tags helpers")
class TestEventAutoTags:
    def test_resolve_returns_active_tag_ids(self):
        con = duckdb_repo.get_connection()
        try:
            con.execute(
                "UPDATE events SET auto_tags = '[\"путешествия\"]' WHERE id = 1",
            )
            ids = sheet_mapping.resolve_event_auto_tag_ids(con, 1)
        finally:
            con.close()
        assert ids == [3]

    def test_missing_event_returns_empty(self):
        con = duckdb_repo.get_connection()
        try:
            assert sheet_mapping.resolve_event_auto_tag_ids(con, 999) == []
        finally:
            con.close()

    def test_malformed_json_is_treated_as_empty(self):
        con = duckdb_repo.get_connection()
        try:
            con.execute("UPDATE events SET auto_tags = 'not-json' WHERE id = 1")
            assert sheet_mapping.resolve_event_auto_tag_ids(con, 1) == []
        finally:
            con.close()

    def test_unknown_tag_names_are_dropped(self):
        con = duckdb_repo.get_connection()
        try:
            con.execute(
                'UPDATE events SET auto_tags = \'["путешествия", "missing"]\' WHERE id = 1',
            )
            ids = sheet_mapping.resolve_event_auto_tag_ids(con, 1)
        finally:
            con.close()
        assert ids == [3]

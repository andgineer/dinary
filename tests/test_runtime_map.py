"""Tests for ``runtime_map`` — parsing + atomic swap of the ``map`` tab.

The drain loop reads from ``runtime_mapping`` to decide where each
expense lands in the logging sheet. Those rows are derived from a
human-edited ``map`` worksheet tab via this module.

Tests pin:

* Row validation: unknown category / tag names raise ``MapTabError``;
  blank rows become visual separators (skipped without error);
  ``sheet_category`` (column D) is required.
* ``_atomic_swap`` wipes and repopulates the DB tables inside a
  single transaction — a mid-way crash leaves the previous mapping
  in place.
* ``parse_rows`` row_order is contiguous starting from 1, matching
  ``logging_projection``'s first-match-wins contract.
* ``reload_now`` captures ``modifiedTime`` both before and after the
  reload; if the value shifts during the read, the cache is not
  advanced so the next ``ensure_fresh`` retries.
"""

from unittest.mock import MagicMock, patch

import allure
import pytest

from dinary.config import settings
from dinary.services import duckdb_repo, runtime_map


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
        con.execute("INSERT INTO tags (id, name, is_active) VALUES (1, 'собака', TRUE)")
        con.execute("INSERT INTO tags (id, name, is_active) VALUES (2, 'аня', TRUE)")
    finally:
        con.close()


def _catalog():
    return (
        {"еда": 1, "машина": 2},
        {"собака": 1, "аня": 2},
    )


@allure.epic("RuntimeMap")
@allure.feature("parse_rows")
class TestParseRows:
    def test_happy_path(self):
        cats, tags = _catalog()
        rows = runtime_map.parse_rows(
            [
                ["еда", "", "", "Food", ""],
                ["машина", "", "", "Car", "Transport"],
            ],
            cat_id_by_name=cats,
            tag_id_by_name=tags,
        )
        assert len(rows) == 2
        assert [r.row_order for r in rows] == [1, 2]
        assert rows[0].category_id == 1
        assert rows[1].sheet_category == "Car"

    def test_tags_cell_parsed(self):
        cats, tags = _catalog()
        rows = runtime_map.parse_rows(
            [["еда", "", "собака, аня", "FoodPet", ""]],
            cat_id_by_name=cats,
            tag_id_by_name=tags,
        )
        assert rows[0].tag_ids == (1, 2)

    def test_blank_rows_skipped(self):
        cats, tags = _catalog()
        rows = runtime_map.parse_rows(
            [
                ["еда", "", "", "Food", ""],
                ["", "", "", "", ""],
                ["машина", "", "", "Car", ""],
            ],
            cat_id_by_name=cats,
            tag_id_by_name=tags,
        )
        assert [r.row_order for r in rows] == [1, 2]
        assert rows[1].category_id == 2

    def test_unknown_category_raises(self):
        cats, tags = _catalog()
        with pytest.raises(runtime_map.MapTabError, match="unknown|not an active"):
            runtime_map.parse_rows(
                [["ghost", "", "", "X", ""]],
                cat_id_by_name=cats,
                tag_id_by_name=tags,
            )

    def test_unknown_tag_raises(self):
        cats, tags = _catalog()
        with pytest.raises(runtime_map.MapTabError, match="tag"):
            runtime_map.parse_rows(
                [["еда", "", "ghost_tag", "Food", ""]],
                cat_id_by_name=cats,
                tag_id_by_name=tags,
            )

    def test_missing_sheet_category_raises(self):
        cats, tags = _catalog()
        with pytest.raises(runtime_map.MapTabError, match="sheet_category"):
            runtime_map.parse_rows(
                [["еда", "", "", "", ""]],
                cat_id_by_name=cats,
                tag_id_by_name=tags,
            )

    def test_event_pattern_preserved(self):
        cats, tags = _catalog()
        rows = runtime_map.parse_rows(
            [["машина", "отпуск-*", "", "Trips", ""]],
            cat_id_by_name=cats,
            tag_id_by_name=tags,
        )
        assert rows[0].event_pattern == "отпуск-*"

    def test_case_only_mismatch_on_category_surfaces_did_you_mean(self):
        """Case-only mismatches are the single most common operator
        failure mode on Cyrillic names (Google Sheets auto-capitalises
        the first letter of a cell on some layouts). The hint must
        point at the canonical spelling in the error message so the
        operator can see the diff without squinting at the Cyrillic
        character by character."""
        cats, tags = _catalog()
        with pytest.raises(runtime_map.MapTabError) as excinfo:
            runtime_map.parse_rows(
                [["Еда", "", "", "Food", ""]],
                cat_id_by_name=cats,
                tag_id_by_name=tags,
            )
        msg = str(excinfo.value)
        assert "did you mean" in msg
        assert "'еда'" in msg

    def test_case_only_mismatch_on_tag_surfaces_did_you_mean(self):
        cats, tags = _catalog()
        with pytest.raises(runtime_map.MapTabError) as excinfo:
            runtime_map.parse_rows(
                [["еда", "", "Аня", "Food", ""]],
                cat_id_by_name=cats,
                tag_id_by_name=tags,
            )
        msg = str(excinfo.value)
        assert "did you mean" in msg
        assert "'аня'" in msg

    def test_unrelated_missing_category_has_no_hint(self):
        """The hint is only emitted on a case-only match; a genuinely
        unknown name must not produce a misleading suggestion."""
        cats, tags = _catalog()
        with pytest.raises(runtime_map.MapTabError) as excinfo:
            runtime_map.parse_rows(
                [["ghost", "", "", "X", ""]],
                cat_id_by_name=cats,
                tag_id_by_name=tags,
            )
        assert "did you mean" not in str(excinfo.value)


@allure.epic("RuntimeMap")
@allure.feature("_atomic_swap")
class TestAtomicSwap:
    def test_swap_replaces_runtime_mapping(self):
        cats, tags = _catalog()
        rows = runtime_map.parse_rows(
            [["еда", "", "", "Food", "Ess"]],
            cat_id_by_name=cats,
            tag_id_by_name=tags,
        )
        con = duckdb_repo.get_connection()
        try:
            runtime_map._atomic_swap(con, rows)
            result = con.execute(
                "SELECT row_order, category_id, sheet_category, sheet_group"
                " FROM runtime_mapping ORDER BY row_order",
            ).fetchall()
        finally:
            con.close()
        assert result == [(1, 1, "Food", "Ess")]

    def test_swap_wipes_previous_rows(self):
        cats, tags = _catalog()
        first = runtime_map.parse_rows(
            [["еда", "", "", "Food", ""]],
            cat_id_by_name=cats,
            tag_id_by_name=tags,
        )
        second = runtime_map.parse_rows(
            [["машина", "", "", "Car", ""]],
            cat_id_by_name=cats,
            tag_id_by_name=tags,
        )
        con = duckdb_repo.get_connection()
        try:
            runtime_map._atomic_swap(con, first)
            runtime_map._atomic_swap(con, second)
            rows = con.execute(
                "SELECT category_id, sheet_category FROM runtime_mapping",
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


@allure.epic("RuntimeMap")
@allure.feature("reload_now modifiedTime ordering")
class TestReloadNowOrdering:
    def test_cache_updated_when_modified_time_stable(
        self,
        monkeypatch,
    ):
        """Happy path: modifiedTime is identical before and after the
        read → cache is advanced to that value."""
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "SSID")
        runtime_map._reset_cache()

        ws = _fake_worksheet(
            [
                ["category", "event", "tags", "sheet_category", "sheet_group"],
                ["еда", "", "", "Food", ""],
            ],
        )
        sh = _fake_sheet(ws)

        with (
            patch.object(runtime_map, "get_sheet", return_value=sh),
            patch.object(
                runtime_map,
                "drive_get_modified_time",
                return_value="2026-04-20T10:00:00Z",
            ),
        ):
            summary = runtime_map.reload_now()

        assert summary["modified_time_cached"] is True
        assert runtime_map._cache_state() == "2026-04-20T10:00:00Z"

    def test_cache_not_updated_when_modified_time_shifts_during_read(
        self,
        monkeypatch,
    ):
        """If an edit lands *during* the reload, the content we
        parsed is already stale. The cache must not be advanced, so
        the next ``ensure_fresh`` pass will retry rather than treat
        the partial snapshot as the new steady state."""
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "SSID")
        runtime_map._reset_cache()

        ws = _fake_worksheet(
            [
                ["category", "event", "tags", "sheet_category", "sheet_group"],
                ["еда", "", "", "Food", ""],
            ],
        )
        sh = _fake_sheet(ws)

        modified_times = iter(["2026-04-20T10:00:00Z", "2026-04-20T10:00:05Z"])
        with (
            patch.object(runtime_map, "get_sheet", return_value=sh),
            patch.object(
                runtime_map,
                "drive_get_modified_time",
                side_effect=lambda _ssid: next(modified_times),
            ),
        ):
            summary = runtime_map.reload_now()

        assert summary["modified_time_cached"] is False
        # Cache stays unset so ensure_fresh() reloads on the next tick.
        assert runtime_map._cache_state() is None

    def test_check_after_false_skips_second_drive_get_and_caches_eagerly(
        self,
        monkeypatch,
    ):
        """Admin-reload path: ``check_after=False`` must call
        ``drive_get_modified_time`` exactly once (pre-read only),
        cache that value unconditionally, and report
        ``modified_time_cached=True``. Guards the Drive-quota
        optimisation: the drain loop pays the two-GET cost, but the
        explicit admin button doesn't."""
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "SSID")
        runtime_map._reset_cache()

        ws = _fake_worksheet(
            [
                ["category", "event", "tags", "sheet_category", "sheet_group"],
                ["еда", "", "", "Food", ""],
            ],
        )
        sh = _fake_sheet(ws)
        drive_mock = MagicMock(return_value="2026-04-20T10:00:00Z")

        with (
            patch.object(runtime_map, "get_sheet", return_value=sh),
            patch.object(runtime_map, "drive_get_modified_time", drive_mock),
        ):
            summary = runtime_map.reload_now(check_after=False)

        assert drive_mock.call_count == 1
        assert summary["modified_time_cached"] is True
        assert summary["modified_time"] == "2026-04-20T10:00:00Z"
        assert runtime_map._cache_state() == "2026-04-20T10:00:00Z"


@allure.epic("RuntimeMap")
@allure.feature("ensure_fresh skips when modifiedTime unchanged")
class TestEnsureFresh:
    def test_ensure_fresh_is_noop_when_cache_matches_drive(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "SSID")
        runtime_map._reset_cache()

        # Prime the cache with a first successful reload.
        ws = _fake_worksheet(
            [
                ["category", "event", "tags", "sheet_category", "sheet_group"],
                ["еда", "", "", "Food", ""],
            ],
        )
        sh = _fake_sheet(ws)
        with (
            patch.object(runtime_map, "get_sheet", return_value=sh),
            patch.object(
                runtime_map,
                "drive_get_modified_time",
                return_value="2026-04-20T10:00:00Z",
            ),
        ):
            runtime_map.reload_now()

        assert runtime_map._cache_state() == "2026-04-20T10:00:00Z"

        # Now call ensure_fresh with Drive reporting the same modifiedTime:
        # no get_sheet call, no reparse.
        get_sheet_mock = MagicMock()
        with (
            patch.object(runtime_map, "get_sheet", get_sheet_mock),
            patch.object(
                runtime_map,
                "drive_get_modified_time",
                return_value="2026-04-20T10:00:00Z",
            ),
        ):
            runtime_map.ensure_fresh()

        get_sheet_mock.assert_not_called()

    def test_ensure_fresh_triggers_reload_when_drive_reports_newer(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "SSID")
        runtime_map._reset_cache()

        ws = _fake_worksheet(
            [
                ["category", "event", "tags", "sheet_category", "sheet_group"],
                ["еда", "", "", "Food", ""],
            ],
        )
        sh = _fake_sheet(ws)

        # First reload establishes the baseline.
        with (
            patch.object(runtime_map, "get_sheet", return_value=sh),
            patch.object(
                runtime_map,
                "drive_get_modified_time",
                return_value="2026-04-20T10:00:00Z",
            ),
        ):
            runtime_map.reload_now()

        # Drive advances; ensure_fresh must re-fetch the sheet.
        sh.worksheet.reset_mock()
        ws.get_all_values.reset_mock()
        with (
            patch.object(runtime_map, "get_sheet", return_value=sh),
            patch.object(
                runtime_map,
                "drive_get_modified_time",
                return_value="2026-04-20T11:00:00Z",
            ),
        ):
            runtime_map.ensure_fresh()

        ws.get_all_values.assert_called_once()
        assert runtime_map._cache_state() == "2026-04-20T11:00:00Z"

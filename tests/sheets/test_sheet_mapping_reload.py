"""Reload pipeline tests for ``sheet_mapping``.

Covers ``reload_now`` modifiedTime ordering — cache only advances
when the Drive ``modifiedTime`` brackets the read with a stable value
— and ``ensure_fresh`` short-circuiting when the cached
``modifiedTime`` matches Drive (so a cold tab does not pay for a
worksheet read on every drain tick).

Parsing lives in :file:`test_sheet_mapping_parse.py`; resolution and
DB projection helpers live in
:file:`test_sheet_mapping_resolve.py`.
"""

from unittest.mock import MagicMock, patch

import allure

from dinary.config import settings
from dinary.services import sheet_mapping

from _sheet_mapping_helpers import (  # noqa: F401  (autouse + helpers)
    _fake_sheet,
    _fake_worksheet,
    _tmp_db,
)


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
    def test_ensure_fresh_is_noop_when_cache_matches_drive(
        self,
        monkeypatch,
        real_ensure_fresh,
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
        real_ensure_fresh,
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

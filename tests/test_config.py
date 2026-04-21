"""Tests for config env-var helpers and file-backed import-sources loader."""

import dataclasses
import json
import os
import threading
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest

from dinary import config
from dinary.imports import expense_import


class TestDeprecatedEnvWarnings:
    @pytest.mark.parametrize(
        ("old_name", "new_name"),
        [
            (
                "DINARY_GOOGLE_SHEETS_SPREADSHEET_ID",
                "DINARY_SHEET_LOGGING_SPREADSHEET",
            ),
        ],
    )
    def test_warns_on_renamed_env_var(self, monkeypatch, old_name, new_name):
        """Renames (``_DEPRECATED_ENV_RENAMES``) point at a replacement."""
        monkeypatch.setenv(old_name, "configured")

        with pytest.warns(
            UserWarning,
            match=rf"{old_name} is deprecated and ignored; rename it to {new_name}\.",
        ):
            config._warn_deprecated_env_vars()

    @pytest.mark.parametrize(
        "old_name",
        [
            "DINARY_IMPORT_SOURCES_JSON",
            "DINARY_SHEET_IMPORT_SOURCES_JSON",
        ],
    )
    def test_warns_on_removed_import_sources_env_var(self, monkeypatch, old_name):
        """Fully removed env vars (``_DEPRECATED_ENV_REMOVED``) surface the
        new ``.deploy/import_sources.json`` location in the warning text."""
        monkeypatch.setenv(old_name, "[]")

        with pytest.warns(
            UserWarning,
            match=rf"{old_name} is no longer supported and is ignored: "
            r"move the list to \.deploy/import_sources\.json",
        ):
            config._warn_deprecated_env_vars()

    def test_no_warning_when_deprecated_env_vars_absent(self, monkeypatch):
        for name in (
            "DINARY_SHEET_IMPORT_SOURCES_JSON",
            "DINARY_IMPORT_SOURCES_JSON",
            "DINARY_GOOGLE_SHEETS_SPREADSHEET_ID",
        ):
            monkeypatch.delenv(name, raising=False)

        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")
            config._warn_deprecated_env_vars()

        assert len(record) == 0


@pytest.fixture
def _reset_import_sources_cache():
    """Clear the module-level mtime cache before and after each test.

    ``read_import_sources`` keeps a process-wide cache; these tests
    swap ``_IMPORT_SOURCES_PATH`` to ``tmp_path`` and would otherwise
    see each other's state through the cache.
    """
    config._import_sources_cache = None
    yield
    config._import_sources_cache = None


class TestReadImportSources:
    """Contract tests for the file-backed import-sources loader.

    These pin the behaviours the rest of the codebase relies on:

    * missing file is OK (non-import deployments),
    * malformed JSON / wrong shape is NOT OK (bug, not user choice),
    * placeholder-value ``spreadsheet_id`` is accepted as-is (user's
      choice — imports won't work until edited, but the runtime is
      unaffected),
    * mtime-keyed cache picks up edits without a process restart,
    * concurrent reads are safe under the internal lock.
    """

    def test_missing_file_returns_empty_list(
        self,
        tmp_path,
        monkeypatch,
        _reset_import_sources_cache,
    ):
        missing = tmp_path / "import_sources.json"
        monkeypatch.setattr(config, "_IMPORT_SOURCES_PATH", missing)
        assert config.read_import_sources() == []

    def test_malformed_json_raises_runtimeerror_with_doc_hint(
        self,
        tmp_path,
        monkeypatch,
        _reset_import_sources_cache,
    ):
        path = tmp_path / "import_sources.json"
        path.write_text("{not: json}", encoding="utf-8")
        monkeypatch.setattr(config, "_IMPORT_SOURCES_PATH", path)
        with pytest.raises(RuntimeError, match=r"imports/"):
            config.read_import_sources()

    def test_wrong_shape_raises_runtimeerror(
        self,
        tmp_path,
        monkeypatch,
        _reset_import_sources_cache,
    ):
        path = tmp_path / "import_sources.json"
        path.write_text('{"year": 2026}', encoding="utf-8")
        monkeypatch.setattr(config, "_IMPORT_SOURCES_PATH", path)
        with pytest.raises(RuntimeError, match=r"JSON array"):
            config.read_import_sources()

    def test_layout_key_defaults_by_year(
        self,
        tmp_path,
        monkeypatch,
        _reset_import_sources_cache,
    ):
        path = tmp_path / "import_sources.json"
        path.write_text(
            json.dumps(
                [
                    {"year": 2012, "spreadsheet_id": "id-2012"},
                    {"year": 2016, "spreadsheet_id": "id-2016"},
                    {"year": 2024, "spreadsheet_id": "id-2024"},
                ],
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(config, "_IMPORT_SOURCES_PATH", path)
        rows = {r.year: r.layout_key for r in config.read_import_sources()}
        assert rows[2012] == "rub_2012"
        assert rows[2016] == "rub_2016"
        assert rows[2024] == "default"

    def test_placeholder_spreadsheet_id_accepted(
        self,
        tmp_path,
        monkeypatch,
        _reset_import_sources_cache,
    ):
        """A file byte-equal to ``.deploy.example/import_sources.json``
        must load without error. Imports simply won't work until the
        operator edits it, but the loader is not in the business of
        pre-validating placeholder strings — the Google Sheets API
        returns a natural 404 / permission error later."""
        example_path = Path(__file__).resolve().parents[1] / ".deploy.example/import_sources.json"
        path = tmp_path / "import_sources.json"
        path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
        monkeypatch.setattr(config, "_IMPORT_SOURCES_PATH", path)
        rows = config.read_import_sources()
        assert rows
        assert all(r.spreadsheet_id for r in rows)

    def test_mtime_cache_invalidates_after_edit(
        self,
        tmp_path,
        monkeypatch,
        _reset_import_sources_cache,
    ):
        path = tmp_path / "import_sources.json"
        path.write_text(
            json.dumps([{"year": 2024, "spreadsheet_id": "first"}]),
            encoding="utf-8",
        )
        monkeypatch.setattr(config, "_IMPORT_SOURCES_PATH", path)
        first = config.read_import_sources()
        assert [r.spreadsheet_id for r in first] == ["first"]

        path.write_text(
            json.dumps([{"year": 2024, "spreadsheet_id": "second"}]),
            encoding="utf-8",
        )
        fresh_mtime = path.stat().st_mtime + 1
        os.utime(path, (fresh_mtime, fresh_mtime))

        second = config.read_import_sources()
        assert [r.spreadsheet_id for r in second] == ["second"]

    def test_concurrent_reads_are_safe(
        self,
        tmp_path,
        monkeypatch,
        _reset_import_sources_cache,
    ):
        path = tmp_path / "import_sources.json"
        path.write_text(
            json.dumps([{"year": 2024, "spreadsheet_id": "concurrent"}]),
            encoding="utf-8",
        )
        monkeypatch.setattr(config, "_IMPORT_SOURCES_PATH", path)

        results: list[list[config.ImportSourceRow]] = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                for _ in range(8):
                    results.append(config.read_import_sources())
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, errors
        assert len(results) == 64
        assert all(len(r) == 1 and r[0].spreadsheet_id == "concurrent" for r in results)

    def test_get_import_source_returns_row_or_none(
        self,
        tmp_path,
        monkeypatch,
        _reset_import_sources_cache,
    ):
        path = tmp_path / "import_sources.json"
        path.write_text(
            json.dumps(
                [
                    {"year": 2025, "spreadsheet_id": "sid-2025"},
                    {"year": 2026, "spreadsheet_id": "sid-2026"},
                ],
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(config, "_IMPORT_SOURCES_PATH", path)
        assert config.get_import_source(2025).spreadsheet_id == "sid-2025"
        assert config.get_import_source(1999) is None


class TestImportSourceRow:
    def test_is_frozen_dataclass(self):
        row = config.ImportSourceRow(year=2026, spreadsheet_id="x")
        with pytest.raises(dataclasses.FrozenInstanceError):
            row.year = 2027  # type: ignore[misc]

    def test_defaults(self):
        row = config.ImportSourceRow(year=2026, spreadsheet_id="x")
        assert row.worksheet_name == ""
        assert row.layout_key == ""
        assert row.notes is None
        assert row.income_worksheet_name == ""
        assert row.income_layout_key == ""


class TestFailLoudWhenSourcesMissing:
    """The import entry points must raise an actionable error when the
    operator tries to run an import task without
    ``.deploy/import_sources.json``. Runtime code paths do NOT hit
    this — they are exercised elsewhere.
    """

    def test_expense_import_iter_parsed_rows_points_at_imports_dir(
        self,
        tmp_path,
        monkeypatch,
        _reset_import_sources_cache,
    ):
        """``iter_parsed_sheet_rows`` is the common entry for every
        ``inv import-budget*`` flow — if the year is unknown AND the
        file is missing, the error message must mention the
        repo-root ``imports/`` directory so the operator knows where
        to look."""
        missing = tmp_path / "import_sources.json"
        monkeypatch.setattr(config, "_IMPORT_SOURCES_PATH", missing)

        with (
            patch.object(expense_import, "get_import_source", return_value=None),
            patch.object(expense_import, "read_import_sources", return_value=[]),
            pytest.raises(ValueError, match=r"imports/"),
        ):
            list(expense_import.iter_parsed_sheet_rows(2026))

"""Tests for config env-var helpers."""

import warnings

import pytest

from dinary import config


class TestDeprecatedEnvWarnings:
    @pytest.mark.parametrize(
        ("old_name", "new_name"),
        [
            ("DINARY_SHEET_IMPORT_SOURCES_JSON", "DINARY_IMPORT_SOURCES_JSON"),
            (
                "DINARY_GOOGLE_SHEETS_SPREADSHEET_ID",
                "DINARY_SHEET_LOGGING_SPREADSHEET",
            ),
        ],
    )
    def test_warns_on_deprecated_env_var(self, monkeypatch, old_name, new_name):
        monkeypatch.setenv(old_name, "configured")

        with pytest.warns(
            UserWarning,
            match=rf"{old_name} is deprecated and ignored; rename it to {new_name}\.",
        ):
            config._warn_deprecated_env_vars()

    def test_no_warning_when_deprecated_env_vars_absent(self, monkeypatch):
        monkeypatch.delenv("DINARY_SHEET_IMPORT_SOURCES_JSON", raising=False)
        monkeypatch.delenv("DINARY_GOOGLE_SHEETS_SPREADSHEET_ID", raising=False)

        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")
            config._warn_deprecated_env_vars()

        assert len(record) == 0

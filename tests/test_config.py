"""Tests for config env-var helpers."""

import warnings

import allure
import pytest

from dinary import config


@allure.epic("Infrastructure")
@allure.feature("Config")
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

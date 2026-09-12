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

    def test_warns_on_removed_llm_providers_file(self, monkeypatch):
        """Settings ignore unknown keys, so an operator pinning the old provider-file
        var would otherwise get no signal that it is now inert."""
        monkeypatch.setenv("DINARY_LLM_PROVIDERS_FILE", "/srv/llms.toml")

        with pytest.warns(UserWarning, match="DINARY_LLM_PROVIDERS_FILE is no longer supported"):
            config._warn_deprecated_env_vars()

    def test_warns_on_a_removed_var_set_only_in_the_env_file(self, tmp_path, monkeypatch):
        """The documented place to set these is .deploy/.env, which pydantic reads
        without exporting — so checking the process environment alone would stay
        silent exactly where an operator configured it."""
        monkeypatch.delenv("DINARY_LLM_PROVIDERS_FILE", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("DINARY_LLM_PROVIDERS_FILE=/srv/llms.toml\n")
        monkeypatch.setattr(config, "_ENV_FILE", env_file)

        with pytest.warns(UserWarning, match="DINARY_LLM_PROVIDERS_FILE is no longer supported"):
            config._warn_deprecated_env_vars()

    def test_no_warning_when_deprecated_env_vars_absent(self, tmp_path, monkeypatch):
        for name in (
            "DINARY_SHEET_IMPORT_SOURCES_JSON",
            "DINARY_IMPORT_SOURCES_JSON",
            "DINARY_GOOGLE_SHEETS_SPREADSHEET_ID",
            "DINARY_LLM_PROVIDERS_FILE",
        ):
            monkeypatch.delenv(name, raising=False)
        # The operator's real env file must not decide whether this test passes.
        monkeypatch.setattr(config, "_ENV_FILE", tmp_path / "absent.env")

        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")
            config._warn_deprecated_env_vars()

        assert len(record) == 0

"""Tests for deploy helpers in :mod:`tasks.deploy`."""

import sys
from unittest.mock import MagicMock, patch

import allure
import pytest

import tasks  # noqa: F401  # populates sys.modules['tasks.deploy']

# tasks/__init__ re-exports the `deploy` Task under the name `tasks.deploy`,
# shadowing the submodule on the package object.  sys.modules always holds
# the real module regardless of that attribute override.
deploy_mod = sys.modules["tasks.deploy"]

from tasks.deploy import (  # noqa: E402
    _migrations_to_rollback,
    _server_applied_migrations,
    _target_migration_head,
)


@allure.epic("Infrastructure")
@allure.feature("Deploy")
class TestDeployRefRequired:
    def test_exits_when_ref_is_missing(self, capsys):

        with pytest.raises(SystemExit) as exc:
            tasks.deploy.body(MagicMock(), ref="", no_start=False)
        assert exc.value.code == 1
        assert "--ref is required" in capsys.readouterr().err


@allure.epic("Infrastructure")
@allure.feature("Deploy")
@allure.story("Migrations check")
class TestMigrationsToRollback:
    def test_no_rollback_when_server_at_target(self):
        applied = ["0001_initial_schema", "0002_exchange_rates"]
        assert _migrations_to_rollback(applied, "0002_exchange_rates") == []

    def test_no_rollback_when_server_behind_target(self):
        applied = ["0001_initial_schema"]
        assert _migrations_to_rollback(applied, "0002_exchange_rates") == []

    def test_single_extra_migration(self):
        applied = ["0001_initial_schema", "0002_exchange_rates", "0003_app_currencies"]
        assert _migrations_to_rollback(applied, "0002_exchange_rates") == ["0003_app_currencies"]

    def test_multiple_extra_migrations(self):
        applied = [
            "0001_initial_schema",
            "0002_exchange_rates",
            "0003_app_currencies",
            "0004_receipt_pipeline",
        ]
        result = _migrations_to_rollback(applied, "0002_exchange_rates")
        assert result == ["0003_app_currencies", "0004_receipt_pipeline"]

    def test_result_is_sorted(self):
        applied = ["0004_receipt_pipeline", "0001_initial_schema", "0003_app_currencies"]
        result = _migrations_to_rollback(applied, "0001_initial_schema")
        assert result == ["0003_app_currencies", "0004_receipt_pipeline"]

    def test_empty_applied_list(self):
        assert _migrations_to_rollback([], "0002_exchange_rates") == []


@allure.epic("Infrastructure")
@allure.feature("Deploy")
@allure.story("Migrations check")
class TestTargetMigrationHead:
    def _ls_tree_output(self, names):
        return "\n".join(f"src/dinary/db/migrations/{n}" for n in names)

    def test_returns_last_sql_stem(self):
        output = self._ls_tree_output(
            [
                "0001_initial_schema.rollback.sql",
                "0001_initial_schema.sql",
                "0002_exchange_rates.rollback.sql",
                "0002_exchange_rates.sql",
            ]
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=output, returncode=0)
            assert _target_migration_head("v0.2.0") == "0002_exchange_rates"

    def test_excludes_rollback_files(self):
        output = self._ls_tree_output(
            [
                "0001_initial_schema.rollback.sql",
                "0001_initial_schema.sql",
            ]
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=output, returncode=0)
            assert _target_migration_head("HEAD") == "0001_initial_schema"

    def test_returns_none_when_no_migrations(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            assert _target_migration_head("HEAD") is None

    def test_passes_ref_to_git_command(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            _target_migration_head("v0.3.1")
        args = mock_run.call_args[0][0]
        assert args[0] == "git"
        assert "v0.3.1" in args


@allure.epic("Infrastructure")
@allure.feature("Deploy")
@allure.story("Migrations check")
class TestServerAppliedMigrations:
    def test_parses_newline_separated_ids(self, monkeypatch):
        monkeypatch.setattr(
            deploy_mod,
            "ssh_capture",
            lambda _c, _cmd: "0001_initial_schema\n0002_exchange_rates\n0003_app_currencies\n",
        )
        result = _server_applied_migrations(MagicMock())
        assert result == ["0001_initial_schema", "0002_exchange_rates", "0003_app_currencies"]

    def test_returns_empty_list_when_no_migrations(self, monkeypatch):
        monkeypatch.setattr(deploy_mod, "ssh_capture", lambda _c, _cmd: "")
        assert _server_applied_migrations(MagicMock()) == []

    def test_strips_surrounding_whitespace(self, monkeypatch):
        monkeypatch.setattr(
            deploy_mod,
            "ssh_capture",
            lambda _c, _cmd: "  0001_initial_schema  \n  0002_exchange_rates  \n",
        )
        result = _server_applied_migrations(MagicMock())
        assert result == ["0001_initial_schema", "0002_exchange_rates"]

    def test_ignores_blank_lines(self, monkeypatch):
        monkeypatch.setattr(
            deploy_mod,
            "ssh_capture",
            lambda _c, _cmd: "\n0001_initial_schema\n\n0002_exchange_rates\n\n",
        )
        result = _server_applied_migrations(MagicMock())
        assert result == ["0001_initial_schema", "0002_exchange_rates"]

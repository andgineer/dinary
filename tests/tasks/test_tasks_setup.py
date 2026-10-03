"""Tests for :mod:`tasks.setup`."""

from unittest.mock import MagicMock

import allure

import tasks.setup


def _stub_remote(monkeypatch):
    remote: list[str] = []
    monkeypatch.setattr(tasks.setup, "sync_remote_env", lambda c: None)
    monkeypatch.setattr(tasks.setup, "sync_remote_deploy_files", lambda c: None)
    monkeypatch.setattr(tasks.setup, "ssh_run", lambda c, cmd: remote.append(cmd))
    return remote


@allure.epic("Infrastructure")
@allure.feature("Setup")
class TestSetupCredentials:
    def test_a_server_without_sheet_logging_needs_no_google_credentials(
        self,
        monkeypatch,
        tmp_path,
    ):
        remote = _stub_remote(monkeypatch)
        monkeypatch.setattr(tasks.setup, "LOCAL_GSPREAD_CREDENTIALS", tmp_path / "absent.json")
        c = MagicMock()

        tasks.setup._setup_credentials(c, "ubuntu@203.0.113.10")

        c.run.assert_not_called()
        assert remote == []

    def test_existing_google_credentials_are_uploaded_private(self, monkeypatch, tmp_path):
        remote = _stub_remote(monkeypatch)
        credentials = tmp_path / "service_account.json"
        credentials.write_text("{}")
        monkeypatch.setattr(tasks.setup, "LOCAL_GSPREAD_CREDENTIALS", credentials)
        c = MagicMock()

        tasks.setup._setup_credentials(c, "ubuntu@203.0.113.10")

        c.run.assert_called_once_with(
            f"scp {credentials} ubuntu@203.0.113.10:~/.config/gspread/service_account.json",
        )
        assert remote[-1] == "chmod 600 ~/.config/gspread/service_account.json"


@allure.epic("Infrastructure")
@allure.feature("Setup")
class TestSetupServerOrder:
    def test_tailscale_is_up_before_the_service_that_waits_for_it_starts(self, monkeypatch):
        steps: list[str] = []
        monkeypatch.setattr(tasks.setup, "host", lambda: "ubuntu@203.0.113.10")
        monkeypatch.setattr(tasks.setup, "tunnel", lambda: "tailscale")
        monkeypatch.setattr(
            tasks.setup,
            "_setup_system_packages",
            lambda c, no_swap: steps.append("packages"),
        )
        monkeypatch.setattr(
            tasks.setup,
            "_setup_credentials",
            lambda c, h: steps.append("credentials"),
        )
        monkeypatch.setattr(
            tasks.setup,
            "_setup_tunnel",
            lambda c, t, ts: steps.append("tunnel"),
        )
        monkeypatch.setattr(
            tasks.setup,
            "create_service",
            lambda c, name, content: steps.append("service"),
        )
        monkeypatch.setattr(tasks.setup, "ssh_run", lambda c, cmd: steps.append(cmd))

        tasks.setup.setup_server.body(MagicMock())

        assert steps[:4] == ["packages", "credentials", "tunnel", "service"]
        assert steps[-1] == tasks.setup.HEALTH_POLL_SCRIPT

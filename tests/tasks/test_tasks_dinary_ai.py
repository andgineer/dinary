"""Tests for the dinary-ai service install/setup/uninstall tasks."""

import os
import plistlib
import re
import sys
from pathlib import Path, PurePosixPath
from unittest.mock import MagicMock

import allure
import pytest

import tasks.dinary_ai as dinary_ai


class _FakeContext:
    """Minimal stand-in for an Invoke ``Context`` that records commands and scripts responses."""

    def __init__(self) -> None:
        self.commands: list[str] = []
        self._handlers: list[tuple[str, object]] = []

    def on(self, prefix, handler) -> None:
        """Register a ``handler(cmd) -> Result`` callback for commands matching ``prefix``."""
        self._handlers.append((prefix, handler))

    def run(self, cmd, **_kwargs):
        self.commands.append(cmd)
        for prefix, handler in self._handlers:
            if cmd.startswith(prefix):
                return handler(cmd)
        return MagicMock(ok=True, failed=False, stdout="")


def _result(stdout: str = "") -> MagicMock:
    return MagicMock(ok=True, failed=False, stdout=stdout)


@pytest.fixture
def _stub_service_lookup(monkeypatch):
    monkeypatch.setattr(dinary_ai, "_uv_path", lambda: "/opt/homebrew/bin/uv")
    monkeypatch.setattr(dinary_ai, "_repo_root", lambda: PurePosixPath("/repo/dinary"))


def _windows_status_csv(status: str) -> str:
    return f'"TaskName","Next Run Time","Status"\r\n"\\{dinary_ai._TASK_NAME}","N/A","{status}"\r\n'


@allure.epic("Analytics")
@allure.feature("Dinary AI Service")
def test_install_writes_plist(monkeypatch, tmp_path, _stub_service_lookup):
    monkeypatch.setattr(sys, "platform", "darwin")
    plist_path = tmp_path / f"{dinary_ai._LABEL}.plist"
    monkeypatch.setattr(dinary_ai, "_plist_path", lambda: plist_path)
    ctx = _FakeContext()

    dinary_ai.install_dinary_ai.body(ctx)

    assert plist_path.exists()
    data = plistlib.loads(plist_path.read_bytes())
    assert data["Label"] == dinary_ai._LABEL
    assert data["WorkingDirectory"] == "/repo/dinary"
    assert data["KeepAlive"] is True
    assert data["RunAtLoad"] is True
    assert data["ProgramArguments"][0] == "/opt/homebrew/bin/uv"
    assert any(cmd.startswith("launchctl load") and str(plist_path) in cmd for cmd in ctx.commands)


@allure.epic("Analytics")
@allure.feature("Dinary AI Service")
def test_uninstall_removes_plist(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    plist_path = tmp_path / f"{dinary_ai._LABEL}.plist"
    plist_path.write_bytes(plistlib.dumps({"Label": dinary_ai._LABEL}))
    monkeypatch.setattr(dinary_ai, "_plist_path", lambda: plist_path)
    ctx = _FakeContext()

    dinary_ai.uninstall_dinary_ai.body(ctx)

    assert not plist_path.exists()
    assert any(
        cmd.startswith("launchctl unload") and str(plist_path) in cmd for cmd in ctx.commands
    )


@allure.epic("Analytics")
@allure.feature("Dinary AI Service")
def test_setup_dinary_ai_idempotent(monkeypatch, tmp_path, _stub_service_lookup):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(os, "getuid", lambda: 501, raising=False)
    plist_path = tmp_path / f"{dinary_ai._LABEL}.plist"
    monkeypatch.setattr(dinary_ai, "_plist_path", lambda: plist_path)
    ctx = _FakeContext()

    dinary_ai.setup_dinary_ai.body(ctx)
    dinary_ai.setup_dinary_ai.body(ctx)

    assert plist_path.exists()
    assert plistlib.loads(plist_path.read_bytes())["Label"] == dinary_ai._LABEL
    assert sum(cmd.startswith("launchctl load") for cmd in ctx.commands) == 1
    assert sum(cmd.startswith("launchctl kickstart") for cmd in ctx.commands) == 2


@allure.epic("Analytics")
@allure.feature("Dinary AI Service")
def test_install_writes_xml_windows(monkeypatch, _stub_service_lookup):
    monkeypatch.setattr(sys, "platform", "win32")
    captured: dict[str, object] = {}

    def capture_xml(cmd):
        match = re.search(r'/xml\s+"([^"]+)"', cmd)
        path = Path(match.group(1))
        captured["path"] = path
        captured["xml"] = path.read_text(encoding="utf-16")
        return _result()

    ctx = _FakeContext()
    ctx.on("schtasks /create", capture_xml)

    dinary_ai.install_dinary_ai.body(ctx)

    assert "<LogonTrigger>" in captured["xml"]
    assert "WorkingDirectory" in captured["xml"]
    assert "<Count>3</Count>" in captured["xml"]
    assert not captured["path"].exists()
    assert any(cmd.startswith(f"schtasks /run /tn {dinary_ai._TASK_NAME}") for cmd in ctx.commands)


@allure.epic("Analytics")
@allure.feature("Dinary AI Service")
def test_uninstall_removes_task_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    ctx = _FakeContext()

    dinary_ai.uninstall_dinary_ai.body(ctx)

    assert any(cmd.startswith(f"schtasks /end /tn {dinary_ai._TASK_NAME}") for cmd in ctx.commands)
    assert any(
        cmd.startswith(f"schtasks /delete /tn {dinary_ai._TASK_NAME}") and "/f" in cmd
        for cmd in ctx.commands
    )


@allure.epic("Analytics")
@allure.feature("Dinary AI Service")
def test_setup_dinary_ai_runs_stopped_task_windows(monkeypatch, _stub_service_lookup):
    monkeypatch.setattr(sys, "platform", "win32")
    ctx = _FakeContext()
    ctx.on(
        f"schtasks /query /tn {dinary_ai._TASK_NAME} /fo csv",
        lambda _cmd: _result(_windows_status_csv("Ready")),
    )

    dinary_ai.setup_dinary_ai.body(ctx)

    assert not any(cmd.startswith("schtasks /create") for cmd in ctx.commands)
    assert any(cmd.startswith(f"schtasks /run /tn {dinary_ai._TASK_NAME}") for cmd in ctx.commands)


@allure.epic("Analytics")
@allure.feature("Dinary AI Service")
def test_task_status_windows_reads_status_column():
    ctx = _FakeContext()
    ctx.on(
        f"schtasks /query /tn {dinary_ai._TASK_NAME} /fo csv",
        lambda _cmd: _result(_windows_status_csv("Running")),
    )

    assert dinary_ai._task_status_windows(ctx) == "Running"

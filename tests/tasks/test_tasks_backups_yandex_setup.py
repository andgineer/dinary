"""Tests for the interactive Yandex.Disk rclone bootstrap (VM2 and local variants)."""

import base64
import re as _stdlib_re
import subprocess
from unittest.mock import MagicMock

import allure
import pytest

import tasks.backups.backups_yandex


@allure.epic("Infrastructure")
@allure.feature("Backup")
@allure.story("Cloud setup")
class TestEnsureYandexRcloneConfigured:
    """Breaking either the "already configured" or "prompt and install" branch
    turns the daily timer into a silent failure (no remote -> rclone errors)."""

    @pytest.fixture(autouse=True)
    def _pin_replica_host(self, monkeypatch):
        monkeypatch.setattr(
            tasks.backups.backups_yandex, "replica_host", lambda: "ubuntu@dinary-replica"
        )

    def test_skips_when_remote_already_exists_and_works(self, monkeypatch):
        """Re-running on a working replica must not re-prompt for credentials."""
        monkeypatch.setattr(
            tasks.backups.backups_yandex, "replica_has_working_yandex_remote", lambda: True
        )

        def boom(*_a, **_kw):
            raise AssertionError("must not prompt when remote exists")

        monkeypatch.setattr(tasks.backups.backups_yandex, "prompt_yandex_credentials", boom)
        monkeypatch.setattr(tasks.backups.backups_yandex, "install_yandex_rclone_remote", boom)
        tasks.backups.backups_yandex.ensure_yandex_rclone_configured(MagicMock())

    def test_prompts_and_installs_when_remote_missing_or_broken(self, monkeypatch):
        """Covers both fresh-VM2 and recovery-from-broken-config: probe False
        -> prompt -> install."""
        monkeypatch.setattr(
            tasks.backups.backups_yandex, "replica_has_working_yandex_remote", lambda: False
        )
        events: list[str] = []

        def fake_prompt():
            events.append("prompt")
            return ("mylogin", "hunter2-app-pw")

        captured: dict[str, str] = {}

        def fake_install(login: str, pw: str) -> None:
            events.append("install")
            captured["login"] = login
            captured["pw"] = pw

        monkeypatch.setattr(tasks.backups.backups_yandex, "prompt_yandex_credentials", fake_prompt)
        monkeypatch.setattr(
            tasks.backups.backups_yandex, "install_yandex_rclone_remote", fake_install
        )
        tasks.backups.backups_yandex.ensure_yandex_rclone_configured(MagicMock())
        assert events == ["prompt", "install"]
        assert captured == {"login": "mylogin", "pw": "hunter2-app-pw"}

    def test_probe_uses_exact_line_match_not_substring(self, monkeypatch):
        """``grep -qx 'yandex:'`` exact match, so ``yandex-old:`` doesn't falsely
        mask the absence of the real remote."""
        calls: list[list[str]] = []

        def fake_run(cmd, *, capture_output, text, check):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr(tasks.backups.backups_yandex.subprocess, "run", fake_run)
        assert tasks.backups.backups_yandex.replica_has_working_yandex_remote() is False
        assert calls
        probe = calls[0][-1]
        assert "grep -qx 'yandex:'" in probe

    def test_probe_smoke_tests_with_rclone_lsd(self, monkeypatch):
        """A remote in ``listremotes`` that fails ``rclone lsd`` must be treated
        as absent, or subsequent mkdir/copyto fail forever."""
        calls: list[str] = []

        def fake_run(cmd, *, capture_output, text, check):
            calls.append(cmd[-1])
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr(tasks.backups.backups_yandex.subprocess, "run", fake_run)
        tasks.backups.backups_yandex.replica_has_working_yandex_remote()
        assert any("rclone lsd yandex:" in c for c in calls)

    def test_probe_rolls_back_broken_remote_inline(self, monkeypatch):
        """A failed smoke test must delete the broken remote so the next call
        re-prompts instead of seeing the same broken entry."""
        calls: list[str] = []

        def fake_run(cmd, *, capture_output, text, check):
            calls.append(cmd[-1])
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr(tasks.backups.backups_yandex.subprocess, "run", fake_run)
        tasks.backups.backups_yandex.replica_has_working_yandex_remote()
        assert any("rclone config delete yandex" in c for c in calls)

    def test_probe_returns_true_when_remote_works(self, monkeypatch):
        """Positive counterpart: a probe that exits 0 (listremotes
        matched + lsd succeeded) must short-circuit the prompt.
        """

        def fake_run(cmd, *, capture_output, text, check):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(tasks.backups.backups_yandex.subprocess, "run", fake_run)
        assert tasks.backups.backups_yandex.replica_has_working_yandex_remote() is True

    def test_install_does_not_leak_password_in_argv(self, monkeypatch):
        """The plaintext must travel only through ssh stdin, never argv (where
        it would leak to ``ps`` on both sides)."""
        seen: dict[str, object] = {}

        def fake_run(cmd, *, input, text, check):
            seen["cmd"] = cmd
            seen["input"] = input
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(tasks.backups.backups_yandex.subprocess, "run", fake_run)
        tasks.backups.backups_yandex.install_yandex_rclone_remote("joe", "super-secret-pw")
        # Plaintext is in stdin payload, never in argv.
        assert "super-secret-pw" in seen["input"]
        assert all("super-secret-pw" not in a for a in seen["cmd"])

    def test_install_uses_obscure_and_webdav_shape(self, monkeypatch):
        """Must use space-separated key/value syntax, not ``key=value`` — the
        latter silently drops ``url`` and produces an unusable remote."""
        seen: dict[str, object] = {}

        def fake_run(cmd, *, input, text, check):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(tasks.backups.backups_yandex.subprocess, "run", fake_run)
        tasks.backups.backups_yandex.install_yandex_rclone_remote("joe", "pw")
        outer = " ".join(seen["cmd"])
        match = _stdlib_re.search(r"echo ([A-Za-z0-9+/=]+) \| base64 -d", outer)
        assert match is not None
        inner = base64.b64decode(match.group(1)).decode()
        assert "rclone obscure -" in inner
        assert "rclone config create --no-obscure yandex webdav" in inner
        assert "url https://webdav.yandex.ru" in inner
        assert "vendor other" in inner
        assert "rclone lsd yandex:" in inner
        assert "rclone config delete yandex" in inner

    def test_install_propagates_ssh_failure(self, monkeypatch):
        """A non-zero exit must abort the whole orchestrator — partial state is
        worse than a clean, retryable failure."""

        def fake_run(cmd, *, input, text, check):
            return subprocess.CompletedProcess(cmd, 5)

        monkeypatch.setattr(tasks.backups.backups_yandex.subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as excinfo:
            tasks.backups.backups_yandex.install_yandex_rclone_remote("joe", "pw")
        assert excinfo.value.code == 5


@allure.epic("Infrastructure")
@allure.feature("Backup")
@allure.story("Cloud setup")
class TestEnsureLocalYandexRcloneConfigured:
    """Local-machine counterpart of :class:`TestEnsureYandexRcloneConfigured`.

    The contract is identical (skip when working, prompt+install when
    missing) but all rclone commands run as local subprocesses — no SSH.
    """

    def test_skips_when_remote_already_works(self, monkeypatch):
        """Re-running ``inv setup-yadisk`` on a machine with a healthy
        remote must not re-prompt for credentials.
        """
        monkeypatch.setattr(
            tasks.backups.backups_yandex, "local_has_working_yandex_remote", lambda: True
        )
        called: list[str] = []
        monkeypatch.setattr(
            tasks.backups.backups_yandex,
            "prompt_yandex_credentials",
            lambda: called.append("prompt") or ("u", "p"),
        )
        monkeypatch.setattr(
            tasks.backups.backups_yandex,
            "install_local_yandex_rclone_remote",
            lambda l, p: called.append("install"),
        )
        tasks.backups.backups_yandex.ensure_local_yandex_rclone_configured()
        assert called == []

    def test_prompts_and_installs_when_remote_missing(self, monkeypatch):
        """Fresh machine or broken config: probe returns False → prompt → install."""
        monkeypatch.setattr(
            tasks.backups.backups_yandex, "local_has_working_yandex_remote", lambda: False
        )
        events: list[str] = []
        captured: dict[str, str] = {}

        def fake_prompt():
            events.append("prompt")
            return ("mylogin", "mypassword")

        def fake_install(login: str, pw: str) -> None:
            events.append("install")
            captured["login"] = login
            captured["pw"] = pw

        monkeypatch.setattr(tasks.backups.backups_yandex, "prompt_yandex_credentials", fake_prompt)
        monkeypatch.setattr(
            tasks.backups.backups_yandex, "install_local_yandex_rclone_remote", fake_install
        )
        tasks.backups.backups_yandex.ensure_local_yandex_rclone_configured()
        assert events == ["prompt", "install"]
        assert captured == {"login": "mylogin", "pw": "mypassword"}

    def test_local_probe_uses_exact_line_match_not_substring(self, monkeypatch):
        """``yandex-old:`` in listremotes must not satisfy the check for
        ``yandex:`` — only an exact line match counts.
        """
        call_count = [0]

        def fake_run(cmd, **kwargs):
            call_count[0] += 1
            stdout = "yandex-old:\n" if "listremotes" in cmd else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(tasks.backups.backups_yandex.subprocess, "run", fake_run)
        result = tasks.backups.backups_yandex.local_has_working_yandex_remote()
        assert result is False
        assert call_count[0] == 1  # lsd must not be reached

    def test_local_probe_smoke_tests_with_rclone_lsd(self, monkeypatch):
        """After ``yandex:`` appears in listremotes, the probe must also
        run ``rclone lsd yandex:`` to verify the credentials actually work.
        """
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            stdout = "yandex:\n" if "listremotes" in cmd else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(tasks.backups.backups_yandex.subprocess, "run", fake_run)
        tasks.backups.backups_yandex.local_has_working_yandex_remote()
        flat = [" ".join(c) for c in calls]
        assert any("rclone lsd yandex:" in s for s in flat)

    def test_local_probe_rolls_back_broken_remote(self, monkeypatch):
        """If lsd fails the probe must delete the broken remote inline
        so the next call re-prompts for fresh credentials.
        """
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            listed = "yandex:\n" if "listremotes" in cmd else ""
            rc = 1 if "lsd" in cmd else 0
            return subprocess.CompletedProcess(cmd, rc, stdout=listed, stderr="")

        monkeypatch.setattr(tasks.backups.backups_yandex.subprocess, "run", fake_run)
        result = tasks.backups.backups_yandex.local_has_working_yandex_remote()
        assert result is False
        flat = [" ".join(c) for c in calls]
        assert any("rclone config delete yandex" in s for s in flat)

    def test_local_probe_returns_true_when_working(self, monkeypatch):
        """Positive counterpart: listremotes + lsd both succeed → True."""

        def fake_run(cmd, **kwargs):
            stdout = "yandex:\n" if "listremotes" in cmd else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(tasks.backups.backups_yandex.subprocess, "run", fake_run)
        assert tasks.backups.backups_yandex.local_has_working_yandex_remote() is True

    def test_install_local_does_not_leak_password_in_argv(self, monkeypatch):
        """Plaintext app-password must travel only via stdin of
        ``rclone obscure -``, never in any subprocess argv.
        """
        seen: list[dict] = []

        def fake_run(cmd, **kwargs):
            seen.append({"cmd": cmd, "input": kwargs.get("input")})
            return subprocess.CompletedProcess(cmd, 0, stdout="OBS123\n", stderr="")

        monkeypatch.setattr(tasks.backups.backups_yandex.subprocess, "run", fake_run)
        tasks.backups.backups_yandex.install_local_yandex_rclone_remote("joe", "super-secret")
        for call in seen:
            if call["input"] != "super-secret":
                assert all("super-secret" not in str(a) for a in call["cmd"])

    def test_install_local_uses_obscure_and_webdav_shape(self, monkeypatch):
        """The install must call ``rclone obscure``, then ``rclone config
        create --no-obscure yandex webdav`` with the WebDAV URL + vendor,
        then smoke-test with ``rclone lsd yandex:``.
        """
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="OBS123\n", stderr="")

        monkeypatch.setattr(tasks.backups.backups_yandex.subprocess, "run", fake_run)
        tasks.backups.backups_yandex.install_local_yandex_rclone_remote("joe", "pw")
        flat = [" ".join(c) for c in calls]
        assert any("rclone obscure -" in s for s in flat)
        assert any("rclone config create --no-obscure yandex webdav" in s for s in flat)
        assert any("url https://webdav.yandex.ru" in s for s in flat)
        assert any("vendor other" in s for s in flat)
        assert any("rclone lsd yandex:" in s for s in flat)

    def test_install_local_rolls_back_on_smoke_test_failure(self, monkeypatch):
        """If ``rclone lsd yandex:`` fails after config create, the remote
        must be deleted and the task must exit 1 — leaving a broken remote
        in place would cause every subsequent rclone call to fail silently.
        """
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            rc = 1 if "lsd" in cmd else 0
            return subprocess.CompletedProcess(cmd, rc, stdout="OBS\n", stderr="")

        monkeypatch.setattr(tasks.backups.backups_yandex.subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as excinfo:
            tasks.backups.backups_yandex.install_local_yandex_rclone_remote("joe", "pw")
        assert excinfo.value.code == 1
        flat = [" ".join(c) for c in calls]
        assert any("rclone config delete yandex" in s for s in flat)

"""Tests for the interactive Yandex.Disk rclone bootstrap.

Covers ``ensure_yandex_rclone_configured`` and the underlying probe +
install helpers: ``replica_has_working_yandex_remote``,
``prompt_yandex_credentials``, ``install_yandex_rclone_remote``.
"""

import base64
import re as _stdlib_re
import subprocess
from unittest.mock import MagicMock

import allure
import pytest

import tasks.backups_yandex


@allure.epic("Deploy")
@allure.feature("backup-cloud-setup: yandex rclone bootstrap")
class TestEnsureYandexRcloneConfigured:
    """The interactive Yandex bootstrap replaces the previous "run
    ``rclone config`` manually on VM2" step. The contract is:

    1. If ``yandex:`` already exists — no prompt, no network.
    2. If it's missing — prompt operator for login+password, then
       install the remote via ``rclone obscure`` + ``rclone config create``
       without putting plaintext in argv or on disk.

    Breaking either branch turns the daily timer into silent failure
    (no remote → rclone errors → retention prunes nothing new), so
    each invariant below guards a real failure mode.
    """

    @pytest.fixture(autouse=True)
    def _pin_replica_host(self, monkeypatch):
        monkeypatch.setattr(tasks.backups_yandex, "replica_host", lambda: "ubuntu@dinary-replica")

    def test_skips_when_remote_already_exists_and_works(self, monkeypatch):
        """Re-running ``backup-cloud-setup`` on a working replica
        must not re-prompt for credentials — the second-run UX is
        ``inv pre`` + redeploy, not "now re-enter your Yandex
        password".
        """
        monkeypatch.setattr(tasks.backups_yandex, "replica_has_working_yandex_remote", lambda: True)

        def boom(*_a, **_kw):
            raise AssertionError("must not prompt when remote exists")

        monkeypatch.setattr(tasks.backups_yandex, "prompt_yandex_credentials", boom)
        monkeypatch.setattr(tasks.backups_yandex, "install_yandex_rclone_remote", boom)
        tasks.backups_yandex.ensure_yandex_rclone_configured(MagicMock())

    def test_prompts_and_installs_when_remote_missing_or_broken(self, monkeypatch):
        """The happy path on a fresh VM2 AND the recovery path from a
        previously-broken config both land here: probe returns False
        → prompt → install. A silent skip here would hide setup
        failures until the first timer fires a day later.
        """
        monkeypatch.setattr(
            tasks.backups_yandex, "replica_has_working_yandex_remote", lambda: False
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

        monkeypatch.setattr(tasks.backups_yandex, "prompt_yandex_credentials", fake_prompt)
        monkeypatch.setattr(tasks.backups_yandex, "install_yandex_rclone_remote", fake_install)
        tasks.backups_yandex.ensure_yandex_rclone_configured(MagicMock())
        assert events == ["prompt", "install"]
        assert captured == {"login": "mylogin", "pw": "hunter2-app-pw"}

    def test_probe_uses_exact_line_match_not_substring(self, monkeypatch):
        """The probe runs on VM2 as a single ssh'd shell script; it
        must grep ``listremotes`` with ``grep -qx 'yandex:'`` so a
        differently-named remote (``yandex-old:``) does not falsely
        mask the absence of the real one.
        """
        calls: list[list[str]] = []

        def fake_run(cmd, *, capture_output, text, check):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr(tasks.backups_yandex.subprocess, "run", fake_run)
        assert tasks.backups_yandex.replica_has_working_yandex_remote() is False
        assert calls
        probe = calls[0][-1]
        assert "grep -qx 'yandex:'" in probe

    def test_probe_smoke_tests_with_rclone_lsd(self, monkeypatch):
        """A remote that shows up in ``listremotes`` but fails
        ``rclone lsd`` (missing url, wrong creds) must be treated as
        absent — otherwise the previous broken-config bug re-surfaces
        where subsequent ``mkdir`` / ``copyto`` fail forever.
        """
        calls: list[str] = []

        def fake_run(cmd, *, capture_output, text, check):
            calls.append(cmd[-1])
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr(tasks.backups_yandex.subprocess, "run", fake_run)
        tasks.backups_yandex.replica_has_working_yandex_remote()
        assert any("rclone lsd yandex:" in c for c in calls)

    def test_probe_rolls_back_broken_remote_inline(self, monkeypatch):
        """If smoke-test fails the probe must delete the broken
        remote server-side so the next call re-prompts for fresh
        credentials rather than seeing the same broken yandex:
        entry again.
        """
        calls: list[str] = []

        def fake_run(cmd, *, capture_output, text, check):
            calls.append(cmd[-1])
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr(tasks.backups_yandex.subprocess, "run", fake_run)
        tasks.backups_yandex.replica_has_working_yandex_remote()
        assert any("rclone config delete yandex" in c for c in calls)

    def test_probe_returns_true_when_remote_works(self, monkeypatch):
        """Positive counterpart: a probe that exits 0 (listremotes
        matched + lsd succeeded) must short-circuit the prompt.
        """

        def fake_run(cmd, *, capture_output, text, check):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(tasks.backups_yandex.subprocess, "run", fake_run)
        assert tasks.backups_yandex.replica_has_working_yandex_remote() is True

    def test_install_does_not_leak_password_in_argv(self, monkeypatch):
        """The plaintext app-password must travel only through
        ssh stdin (encrypted channel) and then die inside
        ``rclone obscure -``. Any ssh argument carrying the
        plaintext would leak it to ``ps`` on both sides.
        """
        seen: dict[str, object] = {}

        def fake_run(cmd, *, input, text, check):
            seen["cmd"] = cmd
            seen["input"] = input
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(tasks.backups_yandex.subprocess, "run", fake_run)
        tasks.backups_yandex.install_yandex_rclone_remote("joe", "super-secret-pw")
        # Plaintext is in stdin payload, never in argv.
        assert "super-secret-pw" in seen["input"]
        assert all("super-secret-pw" not in a for a in seen["cmd"])

    def test_install_uses_obscure_and_webdav_shape(self, monkeypatch):
        """The inner script must call ``rclone obscure`` on the
        password (never write plaintext to the rclone config) and
        must pin the WebDAV url + vendor with the **space-separated**
        key/value syntax rclone actually parses. An earlier
        ``key=value`` form silently dropped ``url`` and produced a
        broken remote that failed every operation with
        ``unsupported protocol scheme ""``.
        """
        seen: dict[str, object] = {}

        def fake_run(cmd, *, input, text, check):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(tasks.backups_yandex.subprocess, "run", fake_run)
        tasks.backups_yandex.install_yandex_rclone_remote("joe", "pw")
        outer = " ".join(seen["cmd"])
        match = _stdlib_re.search(r"echo ([A-Za-z0-9+/=]+) \| base64 -d", outer)
        assert match is not None
        inner = base64.b64decode(match.group(1)).decode()
        assert "rclone obscure -" in inner
        # Space-separated key/value, NOT key=value. --no-obscure
        # prevents rclone from re-obscuring our already-obscured
        # pass value, which would render it unusable.
        assert "rclone config create --no-obscure yandex webdav" in inner
        assert "url https://webdav.yandex.ru" in inner
        assert "vendor other" in inner
        # Smoke-test that verifies creds actually work.
        assert "rclone lsd yandex:" in inner
        # Rollback on smoke-test failure: the broken remote must be
        # deleted server-side so the next run re-prompts.
        assert "rclone config delete yandex" in inner

    def test_install_propagates_ssh_failure(self, monkeypatch):
        """A non-zero exit (wrong app-password, unreachable Yandex
        WebDAV, etc.) MUST abort the whole orchestrator — partial
        state here (packages installed, remote missing) is worse
        than a clean failure the operator can retry.
        """

        def fake_run(cmd, *, input, text, check):
            return subprocess.CompletedProcess(cmd, 5)

        monkeypatch.setattr(tasks.backups_yandex.subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as excinfo:
            tasks.backups_yandex.install_yandex_rclone_remote("joe", "pw")
        assert excinfo.value.code == 5

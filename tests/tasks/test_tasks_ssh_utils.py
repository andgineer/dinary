"""Tests for the small helpers in :mod:`tasks.ssh_utils`.

Covers ``systemd_quote``, ``remote_snapshot_cmd``, and the
``ssh_capture_bytes`` SSH transport (UTF-8 chunk-boundary safety).

The three pure-shell builders (litestream install, setup-swap,
ssh-tailscale-only) live in :file:`test_tasks_ssh_utils_scripts.py`.
"""

import shutil
import subprocess

import allure
import pytest

import tasks.ssh_utils
from tasks.ssh_utils import remote_snapshot_cmd, systemd_quote


def _real_bash_available():
    """Whether a real, executable POSIX ``bash`` is on PATH.

    ``shutil.which("bash")`` alone is not enough on Windows: the
    runner ships a ``bash.exe`` WSL stub even without an installed
    distribution, and that stub prints a UTF-16 banner ("Windows
    Subsystem for Linux has no installed distributions") and exits
    1 for every command. Probe with a trivial ``echo ok`` so the
    skip-condition tracks actual capability, not just PATH presence.
    """
    bash = shutil.which("bash")
    if bash is None:
        return False
    try:
        result = subprocess.run(
            [bash, "-c", "echo ok"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "ok"


_BASH_AVAILABLE = _real_bash_available()


@allure.epic("Deploy")
@allure.feature("systemd EnvironmentFile quoting")
class TestSystemdQuote:
    def test_bare_alphanumeric_unquoted(self):
        assert systemd_quote("abc123") == "abc123"

    def test_url_safe_chars_unquoted(self):
        assert systemd_quote("ubuntu@1.2.3.4") == "ubuntu@1.2.3.4"
        assert systemd_quote("/home/ubuntu/.creds.json") == "/home/ubuntu/.creds.json"

    def test_empty_value_emitted_bare(self):
        # Trailing `KEY=` is the documented "unset" form for systemd.
        assert systemd_quote("") == ""
        assert systemd_quote(None) == ""

    def test_value_with_space_is_quoted(self):
        assert systemd_quote("hello world") == '"hello world"'

    def test_value_with_double_quote_is_escaped(self):
        # JSON values like {"year": 2025} round-trip via backslash escaping.
        assert systemd_quote('{"year": 2025}') == '"{\\"year\\": 2025}"'

    def test_value_with_dollar_is_escaped(self):
        # Without the $ escape systemd would try to expand $X as a variable.
        assert systemd_quote("price=$5") == '"price=\\$5"'

    def test_value_with_backslash_is_escaped(self):
        assert systemd_quote("a\\b") == '"a\\\\b"'

    def test_url_with_query_string_is_quoted(self):
        # ? is not in the safe set so we get a quoted form.
        result = systemd_quote("https://docs.google.com/spreadsheets/d/abc?usp=sharing")
        assert result.startswith('"') and result.endswith('"')
        assert "https://docs.google.com/spreadsheets/d/abc?usp=sharing" in result


@allure.epic("Deploy")
@allure.feature("Remote report snapshot wrapper")
class TestRemoteSnapshotCmd:
    """``inv report-income --remote`` / ``inv report-expenses --remote``
    / ``inv import-report-2d-3d --remote`` cannot safely open the
    primary prod SQLite file directly — WAL would let the reader in,
    but the reader would race with in-flight checkpoints and
    Litestream replication and could surface an ephemeral
    inconsistency. ``remote_snapshot_cmd`` wraps the report
    invocation in a ``sqlite3 .backup`` prologue so the read-only
    module runs against a transactionally consistent ``/tmp``
    snapshot instead. These tests pin the exact shape of the emitted
    command so a future refactor cannot silently drop the snapshot
    step.
    """

    def test_takes_sqlite_backup_of_primary_db(self):
        cmd = remote_snapshot_cmd("dinary.reports.income", [])
        # Online backup via ``sqlite3 .backup`` is the only
        # transactionally consistent way to snapshot a live WAL file.
        assert 'sqlite3 "/home/ubuntu/dinary/data/dinary.db"' in cmd
        assert '.backup \\"$SNAP\\"' in cmd

    def test_points_data_path_at_snapshot_not_primary_db(self):
        cmd = remote_snapshot_cmd("dinary.reports.expenses", ["--csv"])
        assert 'DINARY_DATA_PATH="$SNAP"' in cmd
        # Belt-and-suspenders: the emitted command must NEVER point the
        # report module at the live primary file.
        assert "DINARY_DATA_PATH=/home/ubuntu/dinary/data/dinary.db " not in cmd

    def test_passes_flags_through_to_module(self):
        cmd = remote_snapshot_cmd(
            "dinary.reports.expenses",
            ["--year", "2026", "--csv"],
        )
        assert "uv run python -m dinary.reports.expenses --year 2026 --csv" in cmd

    @pytest.mark.skipif(
        not _BASH_AVAILABLE,
        reason=(
            "regression for 527623d62 must run through a real bash because the "
            "remote transport executes the generated cmd via "
            "``... | base64 -d | bash`` — Windows runners typically ship only "
            "the WSL ``bash.exe`` stub which exits non-zero without an installed "
            "distribution, so we probe with ``bash -c 'echo ok'`` and skip when "
            "no usable bash is on PATH"
        ),
    )
    def test_remote_sql_flags_survive_real_bash_tokenization(self):
        """Regression for 527623d62: ``remote_snapshot_cmd`` used to splice
        ``flags`` with ``' '.join``, so a flag value containing spaces or
        single-quotes (typical SQL) was re-tokenized by the remote bash
        into several argv entries and ``dinary.tools.sql`` received a
        truncated ``--query``.

        Verify the fix end-to-end: feed the generated suffix to an actual
        ``bash`` (the same shell the prod transport uses) and observe the
        argv it would hand to ``python -m dinary.tools.sql``.
        """
        module = "dinary.tools.sql"
        flags = ["--query", "SELECT 1 WHERE x = 'y'", "--json"]
        cmd = remote_snapshot_cmd(module, flags)

        needle = f"python -m {module}"
        suffix = cmd[cmd.index(needle) + len(needle) :]

        # ``set --`` makes bash apply its real word-splitting + quote-removal
        # rules to the suffix, exactly as it does for the post-``python -m``
        # tail on the remote host. ``printf '%s\n' "$@"`` then writes one
        # line per resulting argv token.
        script = f'set -- {suffix}\nprintf "%s\\n" "$@"'
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.splitlines() == flags

    def test_flagless_invocation_has_no_trailing_space(self):
        cmd = remote_snapshot_cmd("dinary.reports.income", [])
        assert "uv run python -m dinary.reports.income" in cmd
        # Avoid a trailing space that would render as an empty argv
        # token in the remote shell.
        assert "dinary.reports.income " not in cmd or "dinary.reports.income --" in cmd

    def test_accepts_non_reports_module_paths(self):
        """The same wrapper serves ``inv import-report-2d-3d --remote``
        (which lives under ``dinary.imports.*``, not ``dinary.reports.*``).
        Regression pin: the earlier ``_remote_report_cmd`` hardcoded
        the ``dinary.reports.`` prefix and could not be reused for the
        2D→3D diagnostic."""
        cmd = remote_snapshot_cmd(
            "dinary.imports.report_2d_3d",
            ["--json"],
        )
        assert "uv run python -m dinary.imports.report_2d_3d --json" in cmd

    def test_snapshot_is_pid_scoped_for_parallel_runs(self):
        """Two operators running ``inv report-income --remote`` at the
        same time must not clobber each other's ``/tmp`` file. ``$$``
        expands to the remote shell PID and is how we keep them
        isolated without coordinating via a lock file.
        """
        cmd = remote_snapshot_cmd("dinary.reports.income", [])
        assert "$$" in cmd
        assert "/tmp/dinary-report-snapshot-$$" in cmd

    def test_trap_cleans_up_snapshot_on_exit(self):
        """A failing report (or ``Ctrl-C``) must not leak a
        multi-hundred-MB ``.db`` snapshot in ``/tmp``. The trap is
        registered before the ``.backup`` so even an interrupt
        between registration and completion cannot orphan the file.

        ``sqlite3 .backup`` writes a single self-contained DB file —
        there are no ``-wal`` / ``-shm`` sidecars attached to the
        snapshot output, so the trap does not need to mention them.
        """
        cmd = remote_snapshot_cmd("dinary.reports.income", [])
        assert 'trap "rm -f \\"$SNAP\\"" EXIT' in cmd
        # trap must come BEFORE the sqlite3 .backup so an interrupt
        # between the backup and the trap-registration cannot leak.
        trap_pos = cmd.index("trap")
        backup_pos = cmd.index("sqlite3")
        assert trap_pos < backup_pos

    def test_uses_set_e_so_backup_failure_is_visible(self):
        """If the ``sqlite3 .backup`` of the primary DB fails, the
        operator must see the error immediately — otherwise the
        subsequent ``DINARY_DATA_PATH="$SNAP"`` would run against a
        missing / empty file and emit a confusing "DB not found"
        downstream.
        """
        cmd = remote_snapshot_cmd("dinary.reports.income", [])
        assert cmd.startswith("set -e; ")


@allure.epic("Deploy")
@allure.feature("SSH bytes-capture (UTF-8 chunk-boundary safety)")
class TestSshCaptureBytes:
    """Remote ``inv report-*`` runs must preserve UTF-8 across SSH
    chunk boundaries. Decoding each read_proc_stdout chunk
    independently with ``errors='replace'`` (what ``invoke.c.run``
    does) corrupts multi-byte characters like ``─`` (``E2 94 80``)
    and Cyrillic letters into U+FFFD when the split lands mid-code
    point. The remote-capture helper therefore collects bytes via
    raw subprocess and decodes once at the end. These tests pin that
    contract: the helper uses ``subprocess.run`` (not invoke),
    returns bytes, and round-trips a realistic UTF-8 payload without
    any replacement characters.
    """

    @pytest.fixture(autouse=True)
    def _stub_host(self, monkeypatch):
        """Every ``_ssh_capture_bytes`` call resolves the SSH target via
        ``_host()`` → ``_env()`` → ``.deploy/.env``. That file is a
        developer-workstation artifact and is (correctly) absent on
        CI runners, so without this stub every test in this class
        would ``SystemExit(1)`` before reaching the mocked
        ``subprocess.run``. We scope the stub to the class because
        the real ``_host`` / ``_env`` path is covered elsewhere
        (``TestDeploy`` / manual smoke) and isn't what this class
        is exercising.
        """
        monkeypatch.setattr(tasks.ssh_utils, "host", lambda: "ubuntu@test.invalid")

    def test_returns_raw_bytes_not_decoded_str(self, monkeypatch):
        captured = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b'{"k": "v"}\n',
            stderr=b"",
        )
        monkeypatch.setattr(tasks.ssh_utils.subprocess, "run", lambda *a, **kw: captured)
        out = tasks.ssh_utils.ssh_capture_bytes("whoami")
        assert isinstance(out, bytes)
        assert out == b'{"k": "v"}\n'

    def test_invokes_ssh_with_host_and_base64_wrapped_cmd(self, monkeypatch):
        """The transport is ssh + a base64 envelope around the real
        command (same shape as ``_ssh`` / ``_ssh_capture``) so a
        command carrying single quotes doesn't need manual escaping.
        Pin that shape so a future refactor cannot silently break
        quoting for every remote report / verify call at once.
        """
        seen = {}

        def fake_run(args, **kwargs):
            seen["args"] = args
            seen["kwargs"] = kwargs
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=b"",
                stderr=b"",
            )

        monkeypatch.setattr(tasks.ssh_utils.subprocess, "run", fake_run)
        monkeypatch.setattr(tasks.ssh_utils, "host", lambda: "ubuntu@203.0.113.1")
        tasks.ssh_utils.ssh_capture_bytes("echo hello")

        args = seen["args"]
        assert args[0] == "ssh"
        assert args[1] == "ubuntu@203.0.113.1"
        # Remote shell gets ``echo <b64> | base64 -d | bash`` so it can
        # execute an arbitrary original command without nested quoting.
        assert "base64 -d | bash" in args[2]

    def test_roundtrips_cyrillic_and_box_drawing_bytes_intact(self, monkeypatch):
        """Realistic payload carrying Cyrillic (``путешествия``) and
        box-drawing (``─ ┼``). Through the new bytes-first path we
        must see them come back *byte-identical* — any ``\\ufffd``
        would signal a regression to the chunk-boundary-corruption
        codepath.
        """
        payload = "путешествия — ├─┼ ┃ 2026".encode()
        captured = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=payload,
            stderr=b"",
        )
        monkeypatch.setattr(tasks.ssh_utils.subprocess, "run", lambda *a, **kw: captured)
        out = tasks.ssh_utils.ssh_capture_bytes("whatever")
        decoded = out.decode("utf-8")
        assert "\ufffd" not in decoded
        assert "путешествия" in decoded
        assert "─" in decoded

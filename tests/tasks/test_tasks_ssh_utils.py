"""Tests for the small helpers in :mod:`tasks.ssh_utils`.

Covers ``systemd_quote``, ``remote_snapshot_cmd``, and the
``ssh_capture_bytes`` SSH transport (UTF-8 chunk-boundary safety).

The three pure-shell builders (litestream install, setup-swap,
ssh-tailscale-only) live in :file:`test_tasks_ssh_utils_scripts.py`.
"""

import subprocess
import sys

import allure
import pytest

import tasks.ssh_utils
from tasks.ssh_utils import remote_snapshot_cmd, systemd_quote


@allure.epic("Infrastructure")
@allure.feature("Deploy")
@allure.story("SSH utils")
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


@allure.epic("Infrastructure")
@allure.feature("Deploy")
@allure.story("SSH utils")
class TestRemoteSnapshotCmd:
    """Reading the primary prod SQLite file directly could race with in-flight
    checkpoints/Litestream replication; ``remote_snapshot_cmd`` wraps the report
    in a ``sqlite3 .backup`` prologue so it runs against a consistent snapshot."""

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
        sys.platform == "win32",
        reason=(
            "regression for 527623d62 simulates a Linux remote that executes "
            "the generated cmd via ``... | base64 -d | bash``. Windows runners "
            "either lack bash entirely (the WSL ``bash.exe`` stub) or expose "
            "Git-Bash/MSYS, which mangles argv (path translation, env quirks) "
            "in ways the actual deploy targets (Ubuntu VM1/VM2 + macOS "
            "operator laptop) never see — so reproducing the regression on "
            "Windows is both impossible and pointless"
        ),
    )
    def test_remote_sql_flags_survive_real_bash_tokenization(self):
        """A flag value with spaces/quotes (typical SQL) must survive real bash
        word-splitting on the remote, not just Python string joining."""
        module = "dinary.tools.sql"
        flags = ["--query", "SELECT 1 WHERE x = 'y'", "--json"]
        cmd = remote_snapshot_cmd(module, flags)

        needle = f"python -m {module}"
        suffix = cmd[cmd.index(needle) + len(needle) :]

        # `set --` applies bash's real word-splitting to the suffix, same as
        # the remote host would for the post-`python -m` tail.
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
        """The same wrapper must serve modules outside ``dinary.reports.*`` too
        (e.g. ``dinary.imports.*``) — no hardcoded prefix."""
        cmd = remote_snapshot_cmd(
            "dinary.imports.report_2d_3d",
            ["--json"],
        )
        assert "uv run python -m dinary.imports.report_2d_3d --json" in cmd

    def test_snapshot_is_pid_scoped_for_parallel_runs(self):
        """``$$`` (remote shell PID) keeps two concurrent operators' snapshots
        from clobbering each other without needing a lock file."""
        cmd = remote_snapshot_cmd("dinary.reports.income", [])
        assert "$$" in cmd
        assert "/tmp/dinary-report-snapshot-$$" in cmd

    def test_trap_cleans_up_snapshot_on_exit(self):
        """The trap is registered before ``.backup`` so an interrupt between
        registration and completion can't orphan a multi-hundred-MB /tmp file."""
        cmd = remote_snapshot_cmd("dinary.reports.income", [])
        assert 'trap "rm -f \\"$SNAP\\"" EXIT' in cmd
        # trap must come BEFORE the sqlite3 .backup so an interrupt
        # between the backup and the trap-registration cannot leak.
        trap_pos = cmd.index("trap")
        backup_pos = cmd.index("sqlite3")
        assert trap_pos < backup_pos

    def test_uses_set_e_so_backup_failure_is_visible(self):
        """Without ``set -e``, a failed backup would let the report run against
        a missing/empty file and emit a confusing "DB not found" downstream."""
        cmd = remote_snapshot_cmd("dinary.reports.income", [])
        assert cmd.startswith("set -e; ")


@allure.epic("Infrastructure")
@allure.feature("Deploy")
@allure.story("SSH utils")
class TestSshCaptureBytes:
    """``invoke.c.run`` decodes each chunk independently with ``errors='replace'``,
    corrupting multi-byte UTF-8 (Cyrillic, box-drawing) when a split lands
    mid-codepoint — so this helper collects raw bytes via subprocess instead."""

    @pytest.fixture(autouse=True)
    def _stub_host(self, monkeypatch):
        """Without this, every test would SystemExit(1) resolving ``.deploy/.env``,
        which is correctly absent on CI runners."""
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
        """Same base64-envelope shape as ``ssh_run``/``ssh_capture`` so a command
        with single quotes needs no manual escaping."""
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
        """Must come back byte-identical — any ``\\ufffd`` signals a regression
        to the chunk-boundary-corruption codepath."""
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

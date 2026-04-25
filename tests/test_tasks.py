"""Tests for helpers in ``tasks.py`` (deploy/operator orchestration).

We only cover pure helpers here; tasks themselves run shell commands
against a real server and are exercised via the deploy flow.
"""
import base64
import datetime
import io
import json
import re as _stdlib_re
import shlex
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime as _dt
from datetime import timezone as _tz
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import allure
import pytest

import tasks as _tasks
import tasks._backup as _tasks_backup
import tasks._common as _tasks_common
import tasks._import as _tasks_import
import tasks._local as _tasks_local
import tasks._reports as _tasks_reports
from dinary.tools.backup_retention import _make_pattern as _backup_make_pattern
from dinary.tools.backup_retention import pick_keepers as _pick_keepers

_systemd_quote = _tasks_common._systemd_quote
_remote_snapshot_cmd = _tasks_common._remote_snapshot_cmd


@allure.epic("Deploy")
@allure.feature("systemd EnvironmentFile quoting")
class TestSystemdQuote:
    def test_bare_alphanumeric_unquoted(self):
        assert _systemd_quote("abc123") == "abc123"

    def test_url_safe_chars_unquoted(self):
        assert _systemd_quote("ubuntu@1.2.3.4") == "ubuntu@1.2.3.4"
        assert _systemd_quote("/home/ubuntu/.creds.json") == "/home/ubuntu/.creds.json"

    def test_empty_value_emitted_bare(self):
        # Trailing `KEY=` is the documented "unset" form for systemd.
        assert _systemd_quote("") == ""
        assert _systemd_quote(None) == ""

    def test_value_with_space_is_quoted(self):
        assert _systemd_quote("hello world") == '"hello world"'

    def test_value_with_double_quote_is_escaped(self):
        # JSON values like {"year": 2025} round-trip via backslash escaping.
        assert _systemd_quote('{"year": 2025}') == '"{\\"year\\": 2025}"'

    def test_value_with_dollar_is_escaped(self):
        # Without the $ escape systemd would try to expand $X as a variable.
        assert _systemd_quote("price=$5") == '"price=\\$5"'

    def test_value_with_backslash_is_escaped(self):
        assert _systemd_quote("a\\b") == '"a\\\\b"'

    def test_url_with_query_string_is_quoted(self):
        # ? is not in the safe set so we get a quoted form.
        result = _systemd_quote("https://docs.google.com/spreadsheets/d/abc?usp=sharing")
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
    inconsistency. ``_remote_snapshot_cmd`` wraps the report
    invocation in a ``sqlite3 .backup`` prologue so the read-only
    module runs against a transactionally consistent ``/tmp``
    snapshot instead. These tests pin the exact shape of the emitted
    command so a future refactor cannot silently drop the snapshot
    step.
    """

    def test_takes_sqlite_backup_of_primary_db(self):
        cmd = _remote_snapshot_cmd("dinary.reports.income", [])
        # Online backup via ``sqlite3 .backup`` is the only
        # transactionally consistent way to snapshot a live WAL file.
        assert 'sqlite3 "/home/ubuntu/dinary/data/dinary.db"' in cmd
        assert '.backup \\"$SNAP\\"' in cmd

    def test_points_data_path_at_snapshot_not_primary_db(self):
        cmd = _remote_snapshot_cmd("dinary.reports.expenses", ["--csv"])
        assert 'DINARY_DATA_PATH="$SNAP"' in cmd
        # Belt-and-suspenders: the emitted command must NEVER point the
        # report module at the live primary file.
        assert "DINARY_DATA_PATH=/home/ubuntu/dinary/data/dinary.db " not in cmd

    def test_passes_flags_through_to_module(self):
        cmd = _remote_snapshot_cmd(
            "dinary.reports.expenses",
            ["--year", "2026", "--csv"],
        )
        assert "uv run python -m dinary.reports.expenses --year 2026 --csv" in cmd

    def test_flagless_invocation_has_no_trailing_space(self):
        cmd = _remote_snapshot_cmd("dinary.reports.income", [])
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
        cmd = _remote_snapshot_cmd(
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
        cmd = _remote_snapshot_cmd("dinary.reports.income", [])
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
        cmd = _remote_snapshot_cmd("dinary.reports.income", [])
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
        cmd = _remote_snapshot_cmd("dinary.reports.income", [])
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
        monkeypatch.setattr(_tasks_common, "_host", lambda: "ubuntu@test.invalid")

    def test_returns_raw_bytes_not_decoded_str(self, monkeypatch):
        captured = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b'{"k": "v"}\n', stderr=b"",
        )
        monkeypatch.setattr(_tasks_common.subprocess, "run", lambda *a, **kw: captured)
        out = _tasks_common._ssh_capture_bytes("whoami")
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
                args=args, returncode=0, stdout=b"", stderr=b"",
            )

        monkeypatch.setattr(_tasks_common.subprocess, "run", fake_run)
        monkeypatch.setattr(_tasks_common, "_host", lambda: "ubuntu@203.0.113.1")
        _tasks_common._ssh_capture_bytes("echo hello")

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
            args=[], returncode=0, stdout=payload, stderr=b"",
        )
        monkeypatch.setattr(_tasks_common.subprocess, "run", lambda *a, **kw: captured)
        out = _tasks_common._ssh_capture_bytes("whatever")
        decoded = out.decode("utf-8")
        assert "\ufffd" not in decoded
        assert "путешествия" in decoded
        assert "─" in decoded


@allure.epic("Deploy")
@allure.feature("report-* --remote: fetch JSON, render locally")
class TestRunReportModuleRemote:
    """End-to-end architectural contract for ``inv report-<mod> --remote``:

    1. Remote side executes the report in ``--json`` mode and only
       ships raw row data over SSH (no rich text).
    2. Transport captures bytes, decodes UTF-8 *once*, parses JSON.
    3. Local process calls the module's ``render`` on the resulting
       rows, so the final rendered output matches what the operator
       would see from a local run against the same data.

    These tests assert that contract with a mocked
    ``_ssh_capture_bytes`` so we never touch the network.
    """

    @pytest.fixture
    def _fake_ssh_bytes(self, monkeypatch):
        """Return a handle that lets each test set the remote JSON payload."""

        class Spy:
            payload: bytes = b"[]\n"
            last_cmd: str | None = None

        spy = Spy()

        def fake(cmd):
            spy.last_cmd = cmd
            return spy.payload

        monkeypatch.setattr(_tasks_reports, "_ssh_capture_bytes", fake)
        return spy

    def test_income_remote_uses_json_transport_regardless_of_user_format(
        self, _fake_ssh_bytes,
    ):
        """The user may ask for ``--csv`` / ``--json`` / rich — the
        wire format is always JSON, because that's the only
        byte-exact, chunk-boundary-safe transport.
        """
        _fake_ssh_bytes.payload = b"[]\n"
        c = MagicMock()
        _tasks_reports._run_report_module(c, "income", [], remote=True)
        # The remote cmd must ask for ``--json`` so we get structured
        # data back, not a rendered rich table.
        assert "--json" in _fake_ssh_bytes.last_cmd

    def test_income_remote_csv_still_pulls_json_locally_renders_csv(
        self, _fake_ssh_bytes, monkeypatch, capsys,
    ):
        _fake_ssh_bytes.payload = json.dumps(
            [{"year": 2026, "months": 3, "total": "1779756.00", "avg_month": "593252.00"}],
        ).encode()
        c = MagicMock()
        _tasks_reports._run_report_module(c, "income", ["--csv"], remote=True)
        out = capsys.readouterr().out
        assert out.splitlines()[0] == "year,months,total,avg_month"
        assert "1779756.00" in out
        # Remote never rendered CSV — the server emitted JSON only.
        assert "--csv" not in (_fake_ssh_bytes.last_cmd or "")
        assert "--json" in (_fake_ssh_bytes.last_cmd or "")

    def test_income_remote_json_forwards_raw_bytes_without_re_rendering(
        self, _fake_ssh_bytes, capsysbinary,
    ):
        """``--json --remote`` is the piping-into-jq case. We should
        pass the server's bytes straight through — no ``rows_from_json``
        + ``render_json`` round-trip (which would re-format and
        re-shape whitespace / key order).
        """
        payload = b'[{"year": 2025, "tags": "\xd0\xbf\xd1\x83"}]\n'
        _fake_ssh_bytes.payload = payload
        c = MagicMock()
        _tasks_reports._run_report_module(c, "income", ["--json"], remote=True)
        out = capsysbinary.readouterr().out
        assert out == payload

    def test_income_remote_rich_cyrillic_tags_survive_roundtrip(
        self, _fake_ssh_bytes, capsys,
    ):
        """The original bug: Cyrillic text corrupted into ``�`` on the
        way back. With JSON transport + single-shot decode + local
        rich render, the tag / event names must appear intact in the
        operator's terminal.
        """
        _fake_ssh_bytes.payload = json.dumps(
            [
                {
                    "year": 2025,
                    "months": 10,
                    "total": "5899845.00",
                    "avg_month": "589984.50",
                },
            ],
        ).encode()
        c = MagicMock()
        _tasks_reports._run_report_module(c, "income", [], remote=True)
        out = capsys.readouterr().out
        # No replacement characters anywhere in the rendered output.
        assert "\ufffd" not in out
        assert "2025" in out
        assert "5,899,845.00" in out or "5899845.00" in out

    def test_expenses_remote_rich_preserves_cyrillic_category(
        self, _fake_ssh_bytes, capsys,
    ):
        _fake_ssh_bytes.payload = json.dumps(
            [
                {
                    "category": "путешествия",
                    "event": "",
                    "tags": "",
                    "rows": 3,
                    "total": "42000.00",
                },
            ],
            ensure_ascii=False,
        ).encode()
        c = MagicMock()
        _tasks_reports._run_report_module(c, "expenses", [], remote=True)
        out = capsys.readouterr().out
        assert "\ufffd" not in out
        assert "путешествия" in out

    def test_expenses_remote_forwards_filter_flags_but_not_format_flags(
        self, _fake_ssh_bytes, capsys,
    ):
        _fake_ssh_bytes.payload = b"[]\n"
        c = MagicMock()
        _tasks_reports._run_report_module(
            c, "expenses", ["--year", "2026", "--csv"], remote=True,
        )
        cmd = _fake_ssh_bytes.last_cmd or ""
        # Filters go to remote (they affect the query result).
        assert "--year 2026" in cmd
        # Format flags do NOT — wire format is always JSON.
        assert "--csv" not in cmd
        assert "--json" in cmd


@allure.epic("Deploy")
@allure.feature("import-report-2d-3d: local / remote transport")
class TestImportReport2d3dTransport:
    """CLI surface of ``inv import-report-2d-3d`` mirrors ``inv report-*``:

    * default runs locally via ``c.run``; no SSH,
    * ``--remote`` always asks the server for ``--json`` and renders
      on the local terminal (the only way to keep Cyrillic /
      box-drawing glyphs intact across the SSH pipe),
    * ``--json --remote`` forwards the server's bytes verbatim so
      stdout can be piped into ``jq`` without a round-trip through
      ``rows_from_json``.
    """

    @pytest.fixture
    def _spy_transports(self, monkeypatch):
        class Spy:
            ssh_bytes_cmd: str | None = None
            ssh_bytes_payload: bytes = b""
            local_cmd: str | None = None

        spy = Spy()

        def fake_bytes(cmd: str) -> bytes:
            spy.ssh_bytes_cmd = cmd
            return spy.ssh_bytes_payload

        monkeypatch.setattr(_tasks_import, "_ssh_capture_bytes", fake_bytes)
        return spy

    @staticmethod
    def _run(c, **kwargs):
        return _tasks.import_report_2d_3d.body(c, **kwargs)

    def _sample_payload(self) -> bytes:
        return json.dumps(
            {
                "detail": False,
                "columns": [
                    "category", "event", "tags", "rows", "sheet_category",
                    "sheet_group", "resolution_kind", "years", "amount", "comment",
                ],
                "rows": [
                    {
                        "category": "путешествия",
                        "event": "",
                        "tags": "",
                        "rows": 3,
                        "sheet_category": "путешествия",
                        "sheet_group": "",
                        "resolution_kind": "mapping",
                        "years": "2024-2026",
                        "amount": "42000.00",
                        "comment": "Бали",
                    },
                ],
            },
            ensure_ascii=False,
        ).encode()

    def test_default_runs_locally(self, _spy_transports):
        c = MagicMock()
        self._run(c)
        c.run.assert_called_once()
        cmd = c.run.call_args[0][0]
        assert cmd.startswith("uv run python -m dinary.imports.report_2d_3d")
        assert _spy_transports.ssh_bytes_cmd is None

    def test_remote_rich_uses_json_transport_and_preserves_cyrillic(
        self, _spy_transports, capsys,
    ):
        _spy_transports.ssh_bytes_payload = self._sample_payload()
        c = MagicMock()
        self._run(c, remote=True)

        cmd = _spy_transports.ssh_bytes_cmd or ""
        assert "--json" in cmd
        assert "--csv" not in cmd
        c.run.assert_not_called()

        out = capsys.readouterr().out
        assert "\ufffd" not in out
        assert "путешествия" in out

    def test_remote_uses_snapshot_wrapper_not_live_db(self, _spy_transports):
        """Even though SQLite WAL would technically let a reader open
        the live ``data/dinary.db`` concurrently with the writer, the
        reader can race with in-flight checkpoints and Litestream
        replication and surface ephemeral inconsistencies.
        ``import-report-2d-3d --remote`` must go through the same
        ``sqlite3 .backup`` snapshot wrapper as ``inv report-*``.
        """
        _spy_transports.ssh_bytes_payload = self._sample_payload()
        c = MagicMock()
        self._run(c, remote=True)

        cmd = _spy_transports.ssh_bytes_cmd or ""
        # Snapshot wrapper invariants: must snapshot the live DB,
        # run the report against the snapshot, and set up the trap
        # before the backup.
        assert "SNAP=/tmp/dinary-report-snapshot-$$.db" in cmd
        assert 'sqlite3 "/home/ubuntu/dinary/data/dinary.db"' in cmd
        assert '.backup \\"$SNAP\\"' in cmd
        assert 'DINARY_DATA_PATH="$SNAP"' in cmd
        assert 'trap "rm -f \\"$SNAP\\"" EXIT' in cmd
        assert cmd.index("trap") < cmd.index("sqlite3")

    def test_remote_csv_renders_locally(self, _spy_transports, capsys):
        _spy_transports.ssh_bytes_payload = self._sample_payload()
        c = MagicMock()
        self._run(c, csv=True, remote=True)

        # Remote always runs in JSON mode; ``--csv`` is applied locally.
        assert "--json" in (_spy_transports.ssh_bytes_cmd or "")
        assert "--csv" not in (_spy_transports.ssh_bytes_cmd or "")

        out = capsys.readouterr().out
        assert "путешествия" in out
        # CSV output is line-based with commas, not rich's box-drawing.
        assert "━" not in out

    def test_remote_json_forwards_server_bytes_verbatim(
        self, _spy_transports, capsysbinary,
    ):
        payload = (
            b'{"detail": false, "columns": ["category"], '
            b'"rows": [{"category": "\xd0\xbf\xd1\x83"}]}\n'
        )
        _spy_transports.ssh_bytes_payload = payload
        c = MagicMock()
        self._run(c, json=True, remote=True)
        out = capsysbinary.readouterr().out
        assert out == payload

    def test_csv_and_json_are_mutually_exclusive(self, _spy_transports):
        c = MagicMock()
        with pytest.raises(SystemExit) as excinfo:
            self._run(c, csv=True, json=True)
        assert excinfo.value.code == 1


@allure.epic("Deploy")
@allure.feature("Litestream install script: arch-to-asset mapping")
class TestLitestreamInstallScript:
    """``inv litestream-setup`` downloads a Litestream ``.deb`` whose
    filename suffix depends on the remote VM's CPU. Oracle Free Tier
    ships both x86_64 Micro and Ampere (arm64) shapes, so a typo or
    silent drift between the pinned version and the published asset
    names would only surface at the next VM bootstrap — weeks or
    months after the change lands. These tests pin:

    * the pinned ``LITESTREAM_VERSION`` that the release URL interpolates,
    * the canonical ``uname -m`` → asset-suffix mapping (Litestream's
      release assets use ``x86_64`` / ``arm64``, which are NOT the
      dpkg ``amd64`` / ``arm64`` spellings),
    * a clean, actionable failure on unsupported architectures.
    """

    def test_default_version_matches_pinned_constant(self):
        script = _tasks_common._litestream_install_script()
        assert f"litestream-{_tasks_common.LITESTREAM_VERSION}-linux-x86_64.deb" in script
        assert f"litestream-{_tasks_common.LITESTREAM_VERSION}-linux-arm64.deb" in script

    def test_x86_64_and_amd64_both_map_to_x86_64_asset(self):
        """``uname -m`` historically varies: Linux kernels on Intel
        report ``x86_64``, but some embedded userlands and Debian
        dpkg spelling use ``amd64``. Both must route to the same
        Litestream asset.
        """
        script = _tasks_common._litestream_install_script()
        assert (
            f"x86_64|amd64) ASSET=litestream-{_tasks_common.LITESTREAM_VERSION}-linux-x86_64.deb"
            in script
        )

    def test_aarch64_and_arm64_both_map_to_arm64_asset(self):
        """Same double-spelling problem on Ampere / Graviton:
        Linux kernels report ``aarch64``, Debian userland prefers
        ``arm64``. Both must pick the arm64 asset.
        """
        script = _tasks_common._litestream_install_script()
        assert (
            f"aarch64|arm64) ASSET=litestream-{_tasks_common.LITESTREAM_VERSION}-linux-arm64.deb"
            in script
        )

    def test_unsupported_arch_exits_with_actionable_error(self):
        """An unsupported ``uname -m`` (e.g. ``riscv64``) must error
        out loudly with the offending arch and the pinned version,
        not silently ``curl 404`` a non-existent asset.
        """
        script = _tasks_common._litestream_install_script()
        assert f'Unsupported arch $ARCH for litestream {_tasks_common.LITESTREAM_VERSION}' in script
        assert "*) echo" in script
        assert "exit 1" in script

    def test_download_url_uses_github_release_path_for_pinned_version(self):
        """The asset URL is ``<.../releases/download/v<ver>/$ASSET>``
        (upstream's canonical layout) — a typo in the ``v`` prefix or
        the path layout here is invisible until bootstrap day.
        """
        script = _tasks_common._litestream_install_script()
        assert (
            "https://github.com/benbjohnson/litestream/releases/download/"
            f"v{_tasks_common.LITESTREAM_VERSION}/$ASSET"
            in script
        )

    def test_script_is_idempotent_when_litestream_already_installed(self):
        """Re-running ``inv litestream-setup`` must be cheap: no new
        download when the binary is already on PATH. The outer
        ``if command -v litestream`` gate is the only thing
        preserving that property — pin it.
        """
        script = _tasks_common._litestream_install_script()
        assert "if ! command -v litestream >/dev/null" in script

    def test_version_parameter_allows_future_upgrade(self):
        """Pure-helper ergonomics: passing a different version
        interpolates cleanly into every line that mentions it, so a
        future upgrade is a one-line constant bump rather than a
        string-surgery PR.
        """
        script = _tasks_common._litestream_install_script(version="0.6.0")
        assert "litestream-0.6.0-linux-x86_64.deb" in script
        assert "litestream-0.6.0-linux-arm64.deb" in script
        assert "/releases/download/v0.6.0/$ASSET" in script
        # Sanity: the pinned-default version is NOT leaking into a
        # caller-overridden script.
        assert f"litestream-{_tasks_common.LITESTREAM_VERSION}" not in script


@allure.epic("Deploy")
@allure.feature("litestream-setup: /etc/litestream.yml permissions")
class TestLitestreamSetupPermissions:
    """Regression for a sudo-scope bug in ``inv litestream-setup``:
    a naive ``sudo chown root:root ... && chmod 644 ...`` escalates
    only the ``chown``, leaving ``chmod`` to run as ``ubuntu`` and
    fail with ``Operation not permitted`` on ``/etc/litestream.yml``.
    The fix is to wrap both commands in ``bash -c`` so the outer
    ``sudo`` covers the whole pipeline atomically.

    These tests pin the contract at the outgoing-SSH boundary so a
    future refactor cannot silently reintroduce the split-scope
    shape.
    """

    @pytest.fixture
    def _spy(self, monkeypatch, tmp_path):
        calls: list[tuple[str, str]] = []

        def fake_ssh(_c, cmd: str) -> None:
            calls.append(("ssh", cmd))

        def fake_ssh_sudo(_c, cmd: str) -> None:
            calls.append(("sudo", cmd))

        def fake_write_remote_file(_c, _path: str, _content: str) -> None:
            calls.append(("write", _path))

        def fake_create_service(*_args, **_kwargs) -> None:
            calls.append(("service", "litestream"))

        config = tmp_path / "litestream.yml"
        config.write_text("snapshot: {interval: 1h, retention: 168h}\n")

        monkeypatch.setattr(_tasks_backup, "_ssh", fake_ssh)
        monkeypatch.setattr(_tasks_backup, "_ssh_sudo", fake_ssh_sudo)
        monkeypatch.setattr(_tasks_backup, "_write_remote_file", fake_write_remote_file)
        monkeypatch.setattr(_tasks_backup, "_create_service", fake_create_service)
        monkeypatch.setattr(_tasks_backup, "LOCAL_LITESTREAM_CONFIG_PATH", str(config))
        return calls

    def test_chown_and_chmod_run_inside_a_single_sudo_bash_c(self, _spy):
        """The compound command must be ``bash -c '... && ...'`` so
        the outer ``sudo`` (prepended by :func:`_ssh_sudo`) elevates
        the entire bash invocation, not just the first word of the
        pipeline. A bare ``&&`` chain would leave ``chmod`` running
        as the SSH user.
        """
        _tasks.litestream_setup.body(MagicMock())
        perm_call = next(
            (cmd for kind, cmd in _spy if kind == "sudo" and "chmod 644" in cmd),
            None,
        )
        assert perm_call is not None, "litestream_setup must emit a chmod call"
        assert perm_call.startswith("bash -c '"), (
            "permissions fix must be wrapped in bash -c so outer sudo "
            f"covers both chown and chmod; got: {perm_call!r}"
        )
        assert "chown root:root /etc/litestream.yml" in perm_call
        assert "chmod 644 /etc/litestream.yml" in perm_call
        assert perm_call.rstrip().endswith("'"), (
            "bash -c payload must be fully quoted"
        )

    def test_permissions_target_the_canonical_config_path(self, _spy):
        """Pin that the permissions fix addresses
        ``REMOTE_LITESTREAM_CONFIG_PATH`` specifically — a regression
        that renamed the constant but forgot to update the permissions
        step would leave the uploaded file at whatever ``sudo tee``
        left on disk (0664 with UMASK 002), readable by group
        ``ubuntu`` on a multi-user VM.
        """
        _tasks.litestream_setup.body(MagicMock())
        perm_call = next(
            (cmd for kind, cmd in _spy if kind == "sudo" and "chmod" in cmd),
            None,
        )
        assert perm_call is not None
        assert _tasks_common.REMOTE_LITESTREAM_CONFIG_PATH in perm_call


@allure.epic("Deploy")
@allure.feature("setup-swap: persistent swapfile provisioner")
class TestSetupSwapScript:
    """``inv setup-swap`` is the only mechanism that provisions swap
    on the Oracle Free Tier VMs, which ship with zero swap and
    ~956 MiB of RAM. A silent regression here (wrong size, forgotten
    fstab entry, broken idempotency) would surface weeks later as an
    OOM-killed ``dinary.service`` during a heavy import. These tests
    pin the script's observable contract so the next reviewer does
    not have to re-derive it.
    """

    def test_default_allocates_one_gigabyte(self):
        """Default swap size is 1 GB — matches the Always Free VM
        profile (enough headroom for ``uv sync`` / bulk import
        spikes without eating meaningful disk on a 45 GB root fs).
        """
        script = _tasks_common._build_setup_swap_script(size_gb=1)
        assert "fallocate -l 1G /swapfile" in script

    def test_size_parameter_interpolates_into_fallocate(self):
        """Operators on a fatter shape can opt up; the size must
        land verbatim in the ``fallocate`` line, not just a format
        placeholder.
        """
        script = _tasks_common._build_setup_swap_script(size_gb=4)
        assert "fallocate -l 4G /swapfile" in script
        assert "fallocate -l 1G" not in script

    def test_rejects_nonpositive_size(self):
        """``fallocate -l 0G`` silently succeeds with a zero-byte
        file that ``mkswap`` then rejects — the error message from
        ``mkswap`` is cryptic. Fail fast with a clear local error
        before we even build the script.
        """
        with pytest.raises(ValueError, match="size_gb must be a positive integer"):
            _tasks_common._build_setup_swap_script(size_gb=0)
        with pytest.raises(ValueError, match="size_gb must be a positive integer"):
            _tasks_common._build_setup_swap_script(size_gb=-1)

    def test_idempotent_on_reapply(self):
        """The swapon-check short-circuits allocation when
        ``/swapfile`` is already active. Without this, a second
        ``inv setup`` run would ``fallocate`` a fresh file on top
        of the live one and ``mkswap`` would corrupt the signature
        of the currently-swapped backing store.
        """
        script = _tasks_common._build_setup_swap_script(size_gb=1)
        assert "swapon --show=NAME --noheadings" in script
        assert "grep -qx /swapfile" in script
        assert "/swapfile already active, skipping allocation" in script

    def test_fstab_line_is_deduplicated(self):
        """The fstab append uses ``grep -qxF || echo >>`` so
        re-running never accumulates duplicate entries — otherwise
        every ``inv setup`` would grow ``/etc/fstab`` by a line and
        the system would eventually refuse to mount.
        """
        script = _tasks_common._build_setup_swap_script(size_gb=1)
        assert "/swapfile none swap sw 0 0" in script
        assert 'grep -qxF "$FSTAB_LINE" /etc/fstab || echo "$FSTAB_LINE" >> /etc/fstab' in script

    def test_elevation_wraps_entire_block_not_just_first_command(self):
        """Every step (``fallocate`` / ``chmod`` / ``mkswap`` /
        ``swapon`` / fstab edit) needs root. ``sudo bash <<HEREDOC``
        elevates the whole block in one call; a plain semicolon
        chain prefixed with ``sudo`` would only elevate the first
        command and the rest would fail with a permission error.
        """
        script = _tasks_common._build_setup_swap_script(size_gb=1)
        assert script.startswith("sudo bash <<'DINARY_SWAP_EOF'\n")
        assert script.rstrip().endswith("DINARY_SWAP_EOF")

    def test_quoted_heredoc_prevents_local_variable_expansion(self):
        """Without ``<<'EOF'`` (quoted delimiter), the local shell
        would expand ``$FSTAB_LINE`` to an empty string *before*
        the script ever reached the remote, so the fstab would
        get ``grep -qxF "" /etc/fstab`` — a silent match that
        never appends the real entry.
        """
        script = _tasks_common._build_setup_swap_script(size_gb=1)
        assert "<<'DINARY_SWAP_EOF'" in script
        assert "$FSTAB_LINE" in script


@allure.epic("Deploy")
@allure.feature("ssh-tailscale-only: rebind sshd to tailnet ingress")
class TestSshTailscaleOnlyScript:
    """``inv ssh-tailscale-only`` closes the public TCP/22 attack
    surface by rebinding sshd to the Tailscale IPv4 + loopback. A
    regression here is a lockout risk (operator cannot reach the VM
    except via Oracle Cloud's Serial Console), so these tests pin the
    script's observable contract: the pre-flight checks, the atomic
    sshd -t gate with rollback on failure, and the idempotent drop-in
    file layout.
    """

    def test_refuses_when_tailscale_is_not_installed(self):
        """Binding sshd to a non-existent tailscaled IP would silently
        kill inbound SSH entirely. Gate the flip on ``command -v
        tailscale`` before touching any config file.
        """
        script = _tasks_common._build_ssh_tailscale_only_script()
        assert "command -v tailscale" in script
        assert "tailscale is not installed" in script

    def test_refuses_when_tailscaled_has_no_ipv4(self):
        """``tailscale`` binary being present is not enough — the
        daemon may still be logged out or starting. Require a
        non-empty ``tailscale ip -4`` output before the flip.
        """
        script = _tasks_common._build_ssh_tailscale_only_script()
        assert 'TS_IP="$(tailscale ip -4 2>/dev/null | head -1)"' in script
        assert 'if [ -z "$TS_IP" ]; then' in script
        assert "tailscaled is not up" in script

    def test_keeps_loopback_listen_address(self):
        """Loopback must stay bound so operators who reach the box via
        the Oracle Cloud Serial Console can still ``ssh 127.0.0.1``
        locally to trigger ``systemctl reload`` after rolling back a
        bad config.
        """
        script = _tasks_common._build_ssh_tailscale_only_script()
        assert "ListenAddress 127.0.0.1:22" in script

    def test_binds_to_live_tailscale_ip_not_a_hardcoded_value(self):
        """The drop-in file must interpolate the *current* tailscale
        IPv4, not a stale value baked into the script. This guards
        against a subtle regression where a refactor replaces
        ``${TS_IP}`` with an IPv4 literal and the task stops
        self-healing after a Tailscale IP rotation.
        """
        script = _tasks_common._build_ssh_tailscale_only_script()
        assert "ListenAddress ${TS_IP}:22" in script

    def test_inner_heredoc_is_unquoted_so_tsip_expands(self):
        """The inner ``cat >"$DROPIN" <<EOC`` delimiter is unquoted on
        purpose: bash must expand ``${TS_IP}`` when writing the file,
        otherwise the literal string ``${TS_IP}`` lands in
        ``sshd_config.d/`` and ``sshd -t`` rejects it. Complementary
        to the outer heredoc being quoted (checked below).
        """
        script = _tasks_common._build_ssh_tailscale_only_script()
        assert 'cat >"$DROPIN" <<EOC\n' in script
        assert "<<'EOC'" not in script

    def test_sshd_t_validates_before_reload(self):
        """``sshd -t`` must run *before* ``systemctl reload ssh``.
        Reloading on an invalid config would leave the service
        refusing new connections, and — combined with the public IP
        being closed — trap the operator outside the box.
        """
        script = _tasks_common._build_ssh_tailscale_only_script()
        t_idx = script.index("sshd -t")
        reload_idx = script.index("systemctl reload ssh")
        assert t_idx < reload_idx

    def test_rejected_config_is_rolled_back(self):
        """If ``sshd -t`` fails, the drop-in must be removed — a
        persistent broken config would survive reboot and kill sshd
        on next service start. Without rollback the only recovery
        path is the Oracle Cloud Serial Console.
        """
        script = _tasks_common._build_ssh_tailscale_only_script()
        assert 'rm -f "$DROPIN"' in script
        assert "sshd -t rejected the new config" in script

    def test_drop_in_path_and_idempotent_overwrite(self):
        """The canonical Ubuntu drop-in directory is honored, and the
        file is rewritten (``cat >``) on every run rather than
        appended — so a Tailscale IP rotation is absorbed by a simple
        replay.
        """
        script = _tasks_common._build_ssh_tailscale_only_script()
        assert "DROPIN=/etc/ssh/sshd_config.d/10-tailscale-only.conf" in script
        assert 'cat >"$DROPIN" <<EOC' in script
        assert 'cat >>"$DROPIN"' not in script

    def test_elevation_wraps_the_whole_block(self):
        """Writing into ``/etc/ssh/sshd_config.d/``, running
        ``sshd -t``, and ``systemctl reload ssh`` all require root;
        the outer ``sudo bash <<HEREDOC`` is the single elevation
        boundary that keeps these atomic (no partial apply if the
        operator's sudo timestamp expires mid-script).
        """
        script = _tasks_common._build_ssh_tailscale_only_script()
        assert script.startswith("sudo bash <<'DINARY_SSH_TS_EOF'\n")
        assert script.rstrip().endswith("DINARY_SSH_TS_EOF")

    def test_outer_heredoc_is_quoted_so_remote_vars_dont_expand_locally(self):
        """``<<'DINARY_SSH_TS_EOF'`` (quoted delimiter) means the local
        shell leaves ``$TS_IP`` / ``$DROPIN`` literal while it ships
        the script to the remote. Without the quotes both would
        expand to the empty string *before* ``_ssh`` even base64-
        encodes the payload, which would end up silently writing a
        file with ``ListenAddress :22`` (sshd rejects with a clear
        error — but we would still have lost the pre-flight checks
        along the way).
        """
        script = _tasks_common._build_ssh_tailscale_only_script()
        assert "<<'DINARY_SSH_TS_EOF'" in script
        assert "$TS_IP" in script
        assert "$DROPIN" in script


@allure.epic("Deploy")
@allure.feature("setup-replica: replica apt + litestream dir builders")
class TestSetupReplicaScripts:
    """``inv setup-replica`` wires four pure-shell builders together;
    two of them (swap, ssh-tailscale-only) are pinned in their own
    classes above, the remaining two (apt, litestream dir) are pinned
    here. A regression in either silently corrupts the replica's
    ability to receive Litestream WAL segments: the apt step blocks
    forever on a debconf prompt, or the directory lands with wrong
    perms and ``sftp`` cannot write the ``generations/`` tree.
    """

    def test_apt_runs_noninteractive_so_debconf_cannot_hang(self):
        """On a fresh Ubuntu cloud image ``apt-get install`` can block
        on a postfix/grub debconf dialog. ``DEBIAN_FRONTEND=
        noninteractive`` is what keeps the bootstrap hands-off — a
        refactor that dropped it would reintroduce a class of
        "inv setup-replica hangs forever" incidents.
        """
        script = _tasks_backup._build_setup_replica_packages_script()
        assert "export DEBIAN_FRONTEND=noninteractive" in script

    def test_apt_installs_unattended_upgrades(self):
        """Unattended security patches are the only automated channel
        replica VMs have for CVE coverage — nobody runs ``inv deploy``
        on the replica. Pin the package name so a rename in the apt
        step doesn't quietly remove the patch cadence.
        """
        script = _tasks_backup._build_setup_replica_packages_script()
        assert "apt-get install -y -qq unattended-upgrades" in script

    def test_apt_refreshes_package_index_before_install(self):
        """``apt-get update`` must run before ``apt-get install`` —
        without it, a cloud image with a stale package index fails
        ``install`` with ``Unable to locate package`` on any
        newly-mirrored dependency.
        """
        script = _tasks_backup._build_setup_replica_packages_script()
        update_idx = script.index("apt-get update -qq")
        install_idx = script.index("apt-get install -y -qq unattended-upgrades")
        assert update_idx < install_idx

    def test_apt_script_elevates_whole_block(self):
        """``apt`` steps each need root — the outer
        ``sudo bash <<HEREDOC`` is the elevation boundary; a
        semicolon-chain ``sudo apt-get update; apt-get install`` would
        only elevate the first command and the install would fail
        with EACCES.
        """
        script = _tasks_backup._build_setup_replica_packages_script()
        assert script.startswith("sudo bash <<'DINARY_REPLICA_PKG_EOF'\n")
        assert script.rstrip().endswith("DINARY_REPLICA_PKG_EOF")

    def test_litestream_dir_path_is_the_canonical_constant(self):
        """The path baked into the bootstrap script MUST match
        :data:`REPLICA_LITESTREAM_DIR` — the ``inv litestream-setup``
        replica URL on VM1 (``sftp://.../var/lib/litestream``) points
        at the same string. A silent drift here would let the
        bootstrap succeed and the first WAL push fail with "No such
        file or directory" on the remote end.
        """
        script = _tasks_backup._build_setup_replica_litestream_dir_script()
        assert _tasks_common.REPLICA_LITESTREAM_DIR == "/var/lib/litestream"
        assert f"mkdir -p {_tasks_common.REPLICA_LITESTREAM_DIR}" in script

    def test_litestream_dir_mode_is_0750_not_world_readable(self):
        """The replica stream contains full pre-compaction row data
        (amounts, descriptions) — we do NOT want it world-readable
        on a shared VM. ``0750`` lets the ``ubuntu`` group members
        read for diagnostics while keeping "other" out.
        """
        script = _tasks_backup._build_setup_replica_litestream_dir_script()
        assert f"chmod 750 {_tasks_common.REPLICA_LITESTREAM_DIR}" in script
        assert "chmod 755" not in script
        assert "chmod 777" not in script

    def test_litestream_dir_owned_by_ubuntu(self):
        """Litestream on VM1 connects as ``ubuntu`` over SFTP; the
        receive directory on VM2 must be ``ubuntu``-owned or the very
        first WAL segment write fails with EPERM. Pin ``ubuntu:ubuntu``
        so a refactor to ``root:root`` is caught at review time.
        """
        script = _tasks_backup._build_setup_replica_litestream_dir_script()
        assert f"chown ubuntu:ubuntu {_tasks_common.REPLICA_LITESTREAM_DIR}" in script

    def test_litestream_dir_script_elevates_whole_block(self):
        """``mkdir -p /var/lib/litestream`` and ``chown ubuntu:ubuntu``
        both require root. The outer ``sudo bash <<HEREDOC`` is the
        single elevation boundary; a bare ``mkdir`` would fail with
        EACCES on ``/var/lib/``.
        """
        script = _tasks_backup._build_setup_replica_litestream_dir_script()
        assert script.startswith("sudo bash <<'DINARY_REPLICA_DIR_EOF'\n")
        assert script.rstrip().endswith("DINARY_REPLICA_DIR_EOF")

    def test_litestream_dir_script_verifies_final_state(self):
        """A trailing ``ls -ld`` on the provisioned directory surfaces
        the mode/owner in ``inv setup-replica`` output. If a silent
        umask on the remote rewrote the perms, the operator sees the
        drift immediately instead of discovering it later when the
        first SFTP write fails.
        """
        script = _tasks_backup._build_setup_replica_litestream_dir_script()
        assert f"ls -ld {_tasks_common.REPLICA_LITESTREAM_DIR}" in script


@allure.epic("Deploy")
@allure.feature("setup-replica: bootstrap orchestration")
class TestSetupReplicaTask:
    """The ``setup-replica`` task is a linear composition of the four
    builders pinned above: apt, litestream dir, swap, ssh-tailscale-
    only. The composition itself is the contract — the order matters
    (packages before swap so ``unattended-upgrades`` is the first
    unit installed, ssh-tailscale-only strictly last because it is
    the only step that can lock the operator out if a predecessor
    has failed silently). These tests pin the composition without
    executing any shell.
    """

    @pytest.fixture
    def _spy(self, monkeypatch):
        """Capture every ``_ssh_replica`` payload in order so we can
        assert the exact sequence the task emits. ``DINARY_REPLICA_HOST``
        is stubbed so ``_replica_host`` does not read
        ``.deploy/.env``.
        """

        class Spy:
            calls: list[str]

            def __init__(self) -> None:
                self.calls = []

        spy = Spy()

        def fake_ssh_replica(_c, cmd: str) -> None:
            spy.calls.append(cmd)

        monkeypatch.setattr(_tasks_backup, "_ssh_replica", fake_ssh_replica)
        monkeypatch.setattr(
            _tasks_backup,
            "_replica_host",
            lambda: "ubuntu@dinary-replica",
        )
        return spy

    def test_runs_all_four_bootstrap_steps(self, _spy):
        """The task must dispatch all four steps — dropping any one
        would leave the replica in a half-configured state (e.g. no
        ``/var/lib/litestream`` → Litestream push fails; no
        ssh-tailscale-only → public 22 stays exposed).
        """
        _tasks.setup_replica.body(MagicMock())
        assert len(_spy.calls) == 4

    def test_packages_first_swap_third_ssh_lock_last(self, _spy):
        """Order is load-bearing:

        1. ``apt`` first so ``unattended-upgrades`` is active before
           anything else sits on the box.
        2. litestream dir second (pure FS, no network, no lockout risk).
        3. swap third (needed for ``unattended-upgrades`` dpkg spikes
           on a 956 MiB RAM VM to avoid OOM).
        4. ssh-tailscale-only LAST — any earlier failure must be
           diagnosable over the still-open public 22 path; once this
           step lands, only tailnet/serial-console works.
        """
        _tasks.setup_replica.body(MagicMock())
        pkg_script = _tasks_backup._build_setup_replica_packages_script()
        dir_script = _tasks_backup._build_setup_replica_litestream_dir_script()
        swap_script = _tasks_common._build_setup_swap_script(size_gb=1)
        ssh_script = _tasks_common._build_ssh_tailscale_only_script()
        assert _spy.calls == [pkg_script, dir_script, swap_script, ssh_script]

    def test_swap_size_is_forwarded(self, _spy):
        """A replica on a fatter shape should be able to opt up; the
        ``--swap-size-gb`` flag must reach ``_build_setup_swap_script``
        unchanged, not get silently coerced back to 1.
        """
        _tasks.setup_replica.body(MagicMock(), swap_size_gb=4)
        swap_script = next(
            (c for c in _spy.calls if "fallocate" in c),
            None,
        )
        assert swap_script is not None
        assert "fallocate -l 4G /swapfile" in swap_script

    def test_swap_size_defaults_to_one_gigabyte(self, _spy):
        """The Always Free VM2 shape (E2.1.Micro, 956 MiB RAM) needs
        a 1 GB swap minimum to survive ``apt-get upgrade`` under
        concurrent Litestream SFTP sessions. Pinning the default
        guards against a refactor that drops the kwarg default.
        """
        _tasks.setup_replica.body(MagicMock())
        swap_script = next(
            (c for c in _spy.calls if "fallocate" in c),
            None,
        )
        assert swap_script is not None
        assert "fallocate -l 1G /swapfile" in swap_script

    def test_reuses_the_same_ssh_tailscale_only_script_as_the_app_server(
        self,
        _spy,
    ):
        """VM2 and VM1 must apply *byte-identical* ssh-tailscale-only
        payloads; a divergent copy on the replica path would let a
        hardening change land on one host and silently skip the
        other. The task must call the shared builder, not inline a
        parallel implementation.
        """
        _tasks.setup_replica.body(MagicMock())
        assert _spy.calls[-1] == _tasks_common._build_ssh_tailscale_only_script()


@allure.epic("Deploy")
@allure.feature("verify-db: integrity_check + foreign_key_check gate")
class TestVerifyDbLocal:
    """``inv verify-db`` runs SQLite's two ship-blocker pragmas against
    ``data/dinary.db`` (local) or a snapshot of the prod DB (remote).
    The remote path is shell-only and tested via the snapshot-wrapper
    assertions elsewhere; these tests cover the local happy path,
    the hard-failure path (FK violation), and the ``no DB`` guard.

    The fixture builds real SQLite files on ``tmp_path`` so the test
    runs both pragmas through the stdlib bindings that
    ``tasks.verify_db`` uses — a pure mock would not catch a
    regression that, e.g., reordered the two ``PRAGMA`` statements
    or dropped the output-line check.
    """

    @staticmethod
    def _verify_db(c, *, remote: bool = False) -> None:
        return _tasks.verify_db.body(c, remote=remote)

    @pytest.fixture
    def _cwd(self, tmp_path, monkeypatch):
        """``verify_db`` reads ``data/dinary.db`` relative to cwd."""
        (tmp_path / "data").mkdir()
        monkeypatch.chdir(tmp_path)
        return tmp_path

    def test_passes_on_healthy_db(self, _cwd, capsys):
        db_path = _cwd / "data" / "dinary.db"

        with sqlite3.connect(db_path) as con:
            con.executescript(
                "PRAGMA foreign_keys=ON;"
                "CREATE TABLE parent (id INTEGER PRIMARY KEY);"
                "CREATE TABLE child ("
                "  id INTEGER PRIMARY KEY,"
                "  parent_id INTEGER NOT NULL REFERENCES parent(id)"
                ");"
                "INSERT INTO parent (id) VALUES (1);"
                "INSERT INTO child (id, parent_id) VALUES (1, 1);"
            )
        c = MagicMock()
        self._verify_db(c)
        out = capsys.readouterr().out
        assert "ok" in out
        assert "=== verify-db OK ===" in out

    def test_fails_on_foreign_key_violation(self, _cwd, capsys):
        """Disabling FK enforcement at write time lets us create a
        deliberately-orphaned row, which is precisely what
        ``PRAGMA foreign_key_check`` is designed to catch. Verify
        must refuse to pass on that file.
        """
        db_path = _cwd / "data" / "dinary.db"

        with sqlite3.connect(db_path) as con:
            con.executescript(
                "CREATE TABLE parent (id INTEGER PRIMARY KEY);"
                "CREATE TABLE child ("
                "  id INTEGER PRIMARY KEY,"
                "  parent_id INTEGER NOT NULL REFERENCES parent(id)"
                ");"
                # FKs are OFF by default on a fresh connection, so
                # this orphan insert succeeds even though parent_id=42
                # does not exist.
                "INSERT INTO child (id, parent_id) VALUES (1, 42);"
            )
        c = MagicMock()
        with pytest.raises(SystemExit) as excinfo:
            self._verify_db(c)
        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "=== verify-db FAILED ===" in captured.err
        # The orphaned row must be in the reported output — otherwise
        # the test is passing for the wrong reason.
        assert "child" in captured.out

    def test_fails_cleanly_when_db_is_missing(self, _cwd, capsys):
        """First-run UX: an operator who never ran ``inv dev`` or
        ``inv backup`` has no local DB. The task must exit 1 with a
        pointer to what to run next, not a cryptic sqlite3 error.
        """
        # Note: _cwd already created ``data/`` but not ``dinary.db``.
        c = MagicMock()
        with pytest.raises(SystemExit) as excinfo:
            self._verify_db(c)
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "No local DB" in err
        assert "inv dev" in err or "inv backup" in err


@allure.epic("Deploy")
@allure.feature("verify-db --remote command shape")
class TestVerifyDbRemote:
    """``inv verify-db --remote`` takes a ``sqlite3 .backup`` of the
    live prod DB into ``/tmp``, then runs
    ``PRAGMA integrity_check; PRAGMA foreign_key_check;`` against the
    snapshot. The exact emitted shell command is the contract —
    reordering or dropping either pragma silently hides a class of
    post-migration data-corruption regressions, so pin both pragmas
    explicitly. A shell-only test is enough here because the Python
    side just forwards the output through the same
    ``lines == ["ok"]`` check as the local path (already covered by
    ``TestVerifyDbLocal``).
    """

    @pytest.fixture
    def _spy(self, monkeypatch):
        class Spy:
            cmd: str | None = None
            payload: bytes = b"ok\n"

        spy = Spy()

        def fake_bytes(cmd: str) -> bytes:
            spy.cmd = cmd
            return spy.payload

        monkeypatch.setattr(_tasks_local, "_ssh_capture_bytes", fake_bytes)
        return spy

    def test_remote_snapshots_live_db_before_pragma_checks(self, _spy):
        _tasks.verify_db.body(MagicMock(), remote=True)
        cmd = _spy.cmd or ""
        # Snapshot prologue: ``sqlite3 .backup`` against the prod
        # path, trap before the backup, set -e so a failed backup
        # doesn't silently run pragmas on whatever ``/tmp`` residue
        # may exist from an earlier run.
        assert cmd.startswith("set -e; ")
        assert "SNAP=/tmp/dinary-verify-db-$$.db" in cmd
        assert 'sqlite3 "/home/ubuntu/dinary/data/dinary.db"' in cmd
        assert '.backup \\"$SNAP\\"' in cmd
        assert 'trap "rm -f \\"$SNAP\\"" EXIT' in cmd
        assert cmd.index("trap") < cmd.index("sqlite3")

    def test_remote_runs_both_pragma_checks_against_snapshot(self, _spy):
        _tasks.verify_db.body(MagicMock(), remote=True)
        cmd = _spy.cmd or ""
        # Both pragmas must target ``$SNAP``, not the live DB path —
        # a regression that shortened this to ``sqlite3 "$DB" "..."``
        # would race with WAL checkpoints on a busy server.
        assert (
            'sqlite3 "$SNAP" "PRAGMA integrity_check; PRAGMA foreign_key_check;"'
            in cmd
        )

    def test_remote_propagates_pragma_failure_as_exit_1(self, _spy, capsys):
        """When the remote snapshot reports any issue, the local side
        must still honour the ``lines == ["ok"]`` contract and exit 1
        with the pragma output visible to the operator.
        """
        _spy.payload = b"ok\nchild|1|parent|0\n"
        with pytest.raises(SystemExit) as excinfo:
            _tasks.verify_db.body(MagicMock(), remote=True)
        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "child|1|parent|0" in captured.out
        assert "=== verify-db FAILED ===" in captured.err

    def test_remote_reports_ok_when_snapshot_is_healthy(self, _spy, capsys):
        _spy.payload = b"ok\n"
        _tasks.verify_db.body(MagicMock(), remote=True)
        out = capsys.readouterr().out
        assert "=== verify-db OK ===" in out


@allure.epic("Deploy")
@allure.feature("backup-cloud-setup: script builders")
class TestSetupBackupScripts:
    """``inv backup-cloud-setup`` composes four pure-string builders: the
    apt step, the backup bash pipeline, the GFS retention Python
    script, and the systemd unit pair. A regression in any of them
    either silently corrupts the backup (wrong remote path, wrong
    replica source) or locks the timer in a failed state. These
    tests pin the observable contract without booting SSH.
    """

    def test_packages_script_is_noninteractive(self):
        """``DEBIAN_FRONTEND=noninteractive`` is mandatory — without
        it apt can block on a postfix/grub debconf prompt on a fresh
        cloud image, silently hanging ``inv backup-cloud-setup``.
        """
        script = _tasks_backup._build_setup_replica_backup_packages_script()
        assert "export DEBIAN_FRONTEND=noninteractive" in script

    def test_packages_script_installs_rclone_sqlite3_zstd(self):
        """Pipeline depends on all three: rclone uploads, sqlite3
        validates, zstd compresses. Dropping one silently breaks
        the daily timer a day later with a shell "command not found".
        """
        script = _tasks_backup._build_setup_replica_backup_packages_script()
        assert (
            "apt-get install -y -qq rclone sqlite3 zstd" in script
        )

    def test_packages_script_elevates_whole_block(self):
        """Every apt step needs root. A bare ``sudo apt-get update &&
        apt-get install`` would only elevate the update and fail the
        install with EACCES.
        """
        script = _tasks_backup._build_setup_replica_backup_packages_script()
        assert script.startswith("sudo bash <<'DINARY_BACKUP_PKG_EOF'\n")
        assert script.rstrip().endswith("DINARY_BACKUP_PKG_EOF")

    def test_backup_script_has_safety_flags(self):
        """``set -euo pipefail`` + trap-based cleanup is the contract
        that distinguishes a "failed backup" from "leaked a half-GB
        corrupt .db into /tmp". Drop any of these and a failure mid-
        run silently leaves trash on VM2.
        """
        script = _tasks_backup._build_backup_script()
        assert "set -euo pipefail" in script
        assert "trap 'rm -rf \"$WORKDIR\"' EXIT" in script

    def test_backup_script_sources_replica_from_canonical_path(self):
        """The Litestream replica tree is materialized at
        ``<REPLICA_LITESTREAM_DIR>/<REPLICA_DB_NAME>``. Silent drift
        here would make ``inv backup-cloud-setup`` restore from an empty
        directory and upload an empty .db every day.
        """
        script = _tasks_backup._build_backup_script()
        expected = f"{_tasks_common.REPLICA_LITESTREAM_DIR}/{_tasks_common.REPLICA_DB_NAME}"
        assert f"path: {expected}" in script
        assert "/var/lib/litestream/dinary" == expected

    def test_backup_script_refuses_to_upload_corrupt_snapshot(self):
        """``PRAGMA integrity_check`` MUST gate the upload — without
        it, a torn-page restore from a broken replica would overwrite
        the last known-good Yandex snapshot with garbage.
        """
        script = _tasks_backup._build_backup_script()
        integrity_idx = script.index(
            "sqlite3 \"$SNAP\" 'PRAGMA integrity_check'"
        )
        upload_idx = script.index("rclone copyto")
        assert integrity_idx < upload_idx
        assert "integrity_check FAILED" in script
        assert "exit 1" in script

    def test_backup_script_uploads_under_canonical_filename(self):
        """Filename is ``dinary-<UTC-ISO>.db.zst`` — both the
        retention script and the restore task's date-prefix lookup
        rely on it. A rename here silently orphans every historical
        snapshot.
        """
        script = _tasks_backup._build_backup_script()
        assert "TS=$(date -u +%Y-%m-%dT%H%MZ)" in script
        assert (
            f'REMOTE="{_tasks_common.BACKUP_RCLONE_REMOTE}:'
            f'{_tasks_common.BACKUP_RCLONE_PATH}/{_tasks_common.BACKUP_FILENAME_PREFIX}'
            f'$TS{_tasks_common.BACKUP_FILENAME_SUFFIX}"'
        ) in script

    def test_backup_script_calls_retention_after_upload(self):
        """Retention must run AFTER the upload succeeds — pruning
        before upload would race a failed upload and delete the
        snapshot we were about to miss anyway.
        """
        script = _tasks_backup._build_backup_script()
        upload_idx = script.index("rclone copyto")
        retention_idx = script.index(_tasks_common.BACKUP_RETENTION_SCRIPT_PATH)
        assert upload_idx < retention_idx


@allure.epic("Deploy")
@allure.feature("backup-cloud-setup: retention (GFS policy)")
class TestBackupRetentionScript:
    """GFS retention policy: 7 daily / 4 weekly / 12 monthly / all yearly.

    Tests import ``pick_keepers`` directly from
    ``dinary.tools.backup_retention``.
    """

    _D = _tasks_common.BACKUP_RETENTION_DAILY
    _W = _tasks_common.BACKUP_RETENTION_WEEKLY
    _M = _tasks_common.BACKUP_RETENTION_MONTHLY

    def _pk(self, snaps):
        return _pick_keepers(snaps, daily=self._D, weekly=self._W, monthly=self._M)

    def test_pattern_matches_canonical_filename_shape(self):
        """Pin filename format so any change (suffix, time precision)
        that breaks restore also breaks this test.
        """
        pattern = _backup_make_pattern(
            _tasks_common.BACKUP_FILENAME_PREFIX, _tasks_common.BACKUP_FILENAME_SUFFIX
        )
        m = pattern.match("dinary-2026-04-22T0317Z.db.zst")
        assert m is not None
        assert m.group(1) == "2026-04-22"
        assert pattern.match("dinary-2026-04-22.db.zst") is None
        assert pattern.match("not-a-backup.txt") is None

    @staticmethod
    def _synth(days_back_from, *, end):
        snaps = []
        for i in range(days_back_from):
            d = end - datetime.timedelta(days=i)
            name = f"dinary-{d.isoformat()}T0317Z.db.zst"
            snaps.append((d, name))
        snaps.sort()
        return snaps

    def test_keeps_exactly_daily_count_on_short_history(self):
        """Under DAILY_KEEP days of history, everything is a daily keeper."""
        end = datetime.date(2026, 4, 22)
        snaps = self._synth(self._D, end=end)
        assert len(self._pk(snaps)) == self._D

    def test_keeps_yearly_winners_indefinitely(self):
        """Closed-year snapshots survive beyond the monthly window."""
        end = datetime.date(2029, 12, 31)
        snaps = self._synth(365 * 10 + 3, end=end)
        keepers = self._pk(snaps)
        yearly_winners = {
            datetime.date.fromisoformat(n.split("dinary-")[1].split("T")[0])
            for n in keepers
            if "-12-31T" in n
        }
        for year in range(2020, 2030):
            assert datetime.date(year, 12, 31) in yearly_winners

    def test_prunes_old_dailies_but_keeps_monthly_winners(self):
        """After MONTHLY_KEEP months, dailies are pruned but monthly
        winners survive.
        """
        end = datetime.date(2026, 4, 15)
        snaps = self._synth(400, end=end)
        keepers = self._pk(snaps)
        kept_dates = {
            datetime.date.fromisoformat(n.split("dinary-")[1].split("T")[0])
            for n in keepers
        }
        assert datetime.date(2026, 3, 31) in kept_dates
        day_in_scope = end - datetime.timedelta(days=3)
        day_out_of_daily = end - datetime.timedelta(days=90)
        assert day_in_scope in kept_dates
        month_of_ood = (day_out_of_daily.year, day_out_of_daily.month)
        monthly_winner = max(d for d in kept_dates if (d.year, d.month) == month_of_ood)
        if monthly_winner != day_out_of_daily:
            assert day_out_of_daily not in kept_dates

    def test_pick_keepers_is_idempotent_on_buckets(self):
        """Running retention twice on the same set produces the same result."""
        end = datetime.date(2026, 4, 22)
        snaps = self._synth(400, end=end)
        assert self._pk(snaps) == self._pk(snaps)


@allure.epic("Deploy")
@allure.feature("backup-cloud-setup: systemd units")
class TestBackupSystemdUnits:
    """The service + timer must together produce a daily backup with
    no manual intervention after ``inv backup-cloud-setup``. Getting either
    unit subtly wrong (wrong User, wrong OnCalendar, no Persistent)
    surfaces as "my backups just stopped" weeks later, so pin the
    invariants here.
    """

    def test_service_runs_as_ubuntu_not_root(self):
        """rclone reads ``~/.config/rclone/rclone.conf`` under the
        invoking user's HOME. The operator ran ``rclone config`` as
        ``ubuntu``; a ``User=root`` unit would silently fail with
        "rclone remote yandex not found".
        """
        unit = _tasks_backup._build_backup_service_unit()
        assert "User=ubuntu" in unit

    def test_service_is_oneshot(self):
        """``Type=oneshot`` is the natural shape for a pipeline that
        either completes or fails — anything else keeps the unit in
        "active (running)" forever after the script exits, masking
        the real success/failure state from ``systemctl status``.
        """
        unit = _tasks_backup._build_backup_service_unit()
        assert "Type=oneshot" in unit

    def test_service_execstart_points_at_installed_script(self):
        """``ExecStart`` must reference the canonical script path.
        Drift between the constant and the unit would make
        ``inv backup-cloud-setup`` succeed but trigger "no such file" at
        timer fire.
        """
        unit = _tasks_backup._build_backup_service_unit()
        assert f"ExecStart={_tasks_common.BACKUP_SCRIPT_PATH}" in unit

    def test_service_is_deprioritized_to_not_starve_litestream(self):
        """Backup runs on VM2 which concurrently hosts the Litestream
        SFTP sink. CPU/IO priority must be lowered so the backup job
        never blocks WAL ingestion — a stalled sink means WAL backlog
        on VM1.
        """
        unit = _tasks_backup._build_backup_service_unit()
        assert "Nice=10" in unit
        assert "IOSchedulingClass=best-effort" in unit

    def test_timer_fires_daily_off_the_hour(self):
        """03:17 UTC: off every hour boundary so we do not collide
        with the top-of-hour Litestream snapshot cadence. Dropping
        the minute offset would create contention with every
        snapshot-producing ``inv litestream-status`` probe.
        """
        unit = _tasks_backup._build_backup_timer_unit()
        assert "OnCalendar=*-*-* 03:17:00" in unit

    def test_timer_is_persistent_so_missed_runs_catch_up(self):
        """``Persistent=true`` guarantees that a reboot across the
        scheduled slot still fires the missed backup at next boot.
        Otherwise the timer would silently create a 24 h retention
        gap on any unlucky reboot.
        """
        unit = _tasks_backup._build_backup_timer_unit()
        assert "Persistent=true" in unit

    def test_timer_has_jitter(self):
        """``RandomizedDelaySec`` spreads load if this task ever runs
        on more than one replica. Zero-jitter timers are a sharp
        thundering-herd footgun even at small scale; pin the
        non-zero value.
        """
        unit = _tasks_backup._build_backup_timer_unit()
        assert "RandomizedDelaySec=" in unit
        assert "RandomizedDelaySec=0" not in unit


@allure.epic("Deploy")
@allure.feature("backup-cloud-setup: orchestration")
class TestSetupBackupTask:
    """``backup-cloud-setup`` orchestrates four things in the single
    ``_ssh_replica`` stream:

    1. apt packages
    2. Litestream binary install
    3. rclone remote check + yadisk directory bootstrap
    4. Script + systemd unit writes + timer enable

    The composition itself is the contract. Order matters — package
    install must precede the rclone check (rclone itself is in the
    apt payload) and the script writes must precede
    ``systemctl enable --now``.
    """

    @pytest.fixture
    def _spy(self, monkeypatch):
        class Spy:
            ssh_calls: list[str]
            write_calls: list[tuple[str, str]]
            ensure_calls: int
            events: list[str]

            def __init__(self) -> None:
                self.ssh_calls = []
                self.write_calls = []
                self.ensure_calls = 0
                self.events = []

        spy = Spy()

        def fake_ssh_replica(_c, cmd: str) -> None:
            spy.ssh_calls.append(cmd)
            spy.events.append(f"ssh:{cmd}")

        def fake_write(_c, path: str, content: str) -> None:
            spy.write_calls.append((path, content))
            spy.events.append(f"write:{path}")

        def fake_ensure(_c) -> None:
            spy.ensure_calls += 1
            spy.events.append("ensure_yandex")

        monkeypatch.setattr(_tasks_backup, "_ssh_replica", fake_ssh_replica)
        monkeypatch.setattr(_tasks_backup, "_write_remote_replica_file", fake_write)
        monkeypatch.setattr(
            _tasks_backup, "_ensure_yandex_rclone_configured", fake_ensure
        )
        monkeypatch.setattr(
            _tasks_backup,
            "_replica_host",
            lambda: "ubuntu@dinary-replica",
        )
        return spy

    def test_runs_package_install_before_rclone_bootstrap(self, _spy):
        """rclone itself is installed in the apt step; the yandex
        remote bootstrap must therefore come after. Inverting the
        order would make the bootstrap fail with "command not
        found" on a fresh VM2 rather than reaching the interactive
        prompt we want.
        """
        _tasks.setup_replica_backup.body(MagicMock())
        pkg_idx = next(
            (
                i for i, ev in enumerate(_spy.events)
                if ev.startswith("ssh:") and "apt-get install -y -qq rclone" in ev
            ),
            None,
        )
        ensure_idx = next(
            (i for i, ev in enumerate(_spy.events) if ev == "ensure_yandex"),
            None,
        )
        assert pkg_idx is not None
        assert ensure_idx is not None
        assert pkg_idx < ensure_idx

    def test_installs_litestream_via_shared_helper(self, _spy):
        """The ``litestream restore`` call inside ``dinary-backup``
        needs the pinned binary — reuse ``_litestream_install_script``
        so VM1 and VM2 converge on the same version. A parallel
        inline install here would let the two sides drift.
        """
        _tasks.setup_replica_backup.body(MagicMock())
        install_cmd = _tasks_common._litestream_install_script()
        assert install_cmd in _spy.ssh_calls

    def test_calls_yandex_rclone_bootstrap_helper(self, _spy):
        """The orchestrator must delegate the rclone-remote setup
        to the dedicated helper; inlining the check here (old
        behaviour) meant a missing remote produced only a "run
        rclone config" hint instead of actually setting it up.
        """
        _tasks.setup_replica_backup.body(MagicMock())
        assert _spy.ensure_calls == 1

    def test_mkdir_runs_after_yandex_bootstrap(self, _spy):
        """``rclone mkdir yandex:Backup/dinary`` only succeeds after
        the ``yandex:`` remote exists. Reversing this order on a
        fresh VM2 would hard-fail the whole orchestrator.
        """
        _tasks.setup_replica_backup.body(MagicMock())
        ensure_idx = next(
            i for i, ev in enumerate(_spy.events) if ev == "ensure_yandex"
        )
        mkdir_idx = next(
            (
                i for i, ev in enumerate(_spy.events)
                if ev.startswith("ssh:") and "rclone mkdir" in ev
            ),
            None,
        )
        assert mkdir_idx is not None
        assert ensure_idx < mkdir_idx

    def test_writes_all_four_managed_paths(self, _spy):
        """Silently dropping any of the four paths would leave VM2
        in a half-configured state (e.g. timer enabled, no script).
        """
        _tasks.setup_replica_backup.body(MagicMock())
        paths = {p for p, _content in _spy.write_calls}
        assert paths == {
            _tasks_common.BACKUP_SCRIPT_PATH,
            _tasks_common.BACKUP_RETENTION_SCRIPT_PATH,
            _tasks_common.BACKUP_SERVICE_PATH,
            _tasks_common.BACKUP_TIMER_PATH,
        }

    def test_writes_match_the_pure_builders(self, _spy):
        """The content pushed to VM2 MUST be byte-identical to what
        the pure builders emit. Drift here would let a helper change
        land in tests but skip the actual file written to prod.
        """
        _tasks.setup_replica_backup.body(MagicMock())
        by_path = dict(_spy.write_calls)
        assert (
            by_path[_tasks_common.BACKUP_SCRIPT_PATH] == _tasks_backup._build_backup_script()
        )
        _retention_expected = (
            Path(__file__).resolve().parent.parent
            / "src/dinary/tools/backup_retention.py"
        ).read_text()
        assert by_path[_tasks_common.BACKUP_RETENTION_SCRIPT_PATH] == _retention_expected
        assert (
            by_path[_tasks_common.BACKUP_SERVICE_PATH]
            == _tasks_backup._build_backup_service_unit()
        )
        assert (
            by_path[_tasks_common.BACKUP_TIMER_PATH]
            == _tasks_backup._build_backup_timer_unit()
        )

    def test_scripts_are_made_executable(self, _spy):
        """``systemd`` refuses to start a unit whose ExecStart target
        is not +x, and the bash pipeline similarly calls the
        retention script as an executable (no ``python3 ...`` prefix
        hack). Both chmods must be emitted.
        """
        _tasks.setup_replica_backup.body(MagicMock())
        chmod_cmds = [
            cmd for cmd in _spy.ssh_calls if cmd.startswith("sudo chmod 0755")
        ]
        chmodded = {cmd.rsplit(" ", 1)[-1] for cmd in chmod_cmds}
        assert _tasks_common.BACKUP_SCRIPT_PATH in chmodded
        assert _tasks_common.BACKUP_RETENTION_SCRIPT_PATH in chmodded

    def test_timer_is_enabled_and_started_in_one_step(self, _spy):
        """``enable --now`` both activates the symlink in
        ``timers.target.wants`` and starts the timer; dropping the
        ``--now`` would leave the timer inactive until the next boot
        and quietly skip the first 24 h of backups.
        """
        _tasks.setup_replica_backup.body(MagicMock())
        assert any(
            "systemctl enable --now dinary-backup.timer" in cmd
            for cmd in _spy.ssh_calls
        )


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
        monkeypatch.setattr(
            _tasks_backup, "_replica_host", lambda: "ubuntu@dinary-replica"
        )

    def test_skips_when_remote_already_exists_and_works(self, monkeypatch):
        """Re-running ``backup-cloud-setup`` on a working replica
        must not re-prompt for credentials — the second-run UX is
        ``inv pre`` + redeploy, not "now re-enter your Yandex
        password".
        """
        monkeypatch.setattr(
            _tasks_backup, "_replica_has_working_yandex_remote", lambda: True
        )

        def boom(*_a, **_kw):
            raise AssertionError("must not prompt when remote exists")

        monkeypatch.setattr(_tasks_backup, "_prompt_yandex_credentials", boom)
        monkeypatch.setattr(_tasks_backup, "_install_yandex_rclone_remote", boom)
        _tasks_backup._ensure_yandex_rclone_configured(MagicMock())

    def test_prompts_and_installs_when_remote_missing_or_broken(self, monkeypatch):
        """The happy path on a fresh VM2 AND the recovery path from a
        previously-broken config both land here: probe returns False
        → prompt → install. A silent skip here would hide setup
        failures until the first timer fires a day later.
        """
        monkeypatch.setattr(
            _tasks_backup, "_replica_has_working_yandex_remote", lambda: False
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

        monkeypatch.setattr(_tasks_backup, "_prompt_yandex_credentials", fake_prompt)
        monkeypatch.setattr(_tasks_backup, "_install_yandex_rclone_remote", fake_install)
        _tasks_backup._ensure_yandex_rclone_configured(MagicMock())
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

        monkeypatch.setattr(_tasks_backup.subprocess, "run", fake_run)
        assert _tasks_backup._replica_has_working_yandex_remote() is False
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

        monkeypatch.setattr(_tasks_backup.subprocess, "run", fake_run)
        _tasks_backup._replica_has_working_yandex_remote()
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

        monkeypatch.setattr(_tasks_backup.subprocess, "run", fake_run)
        _tasks_backup._replica_has_working_yandex_remote()
        assert any("rclone config delete yandex" in c for c in calls)

    def test_probe_returns_true_when_remote_works(self, monkeypatch):
        """Positive counterpart: a probe that exits 0 (listremotes
        matched + lsd succeeded) must short-circuit the prompt.
        """

        def fake_run(cmd, *, capture_output, text, check):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(_tasks_backup.subprocess, "run", fake_run)
        assert _tasks_backup._replica_has_working_yandex_remote() is True

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

        monkeypatch.setattr(_tasks_backup.subprocess, "run", fake_run)
        _tasks_backup._install_yandex_rclone_remote("joe", "super-secret-pw")
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

        monkeypatch.setattr(_tasks_backup.subprocess, "run", fake_run)
        _tasks_backup._install_yandex_rclone_remote("joe", "pw")
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

        monkeypatch.setattr(_tasks_backup.subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as excinfo:
            _tasks_backup._install_yandex_rclone_remote("joe", "pw")
        assert excinfo.value.code == 5


@allure.epic("Deploy")
@allure.feature("backup-cloud-restore: inventory + snapshot picker")
class TestRestoreFromYadiskHelpers:
    """``backup-cloud-restore`` is split into three helpers so the
    destructive file-replacement path can be read separately from the
    discovery path. These tests cover the non-destructive helpers
    (list parsing, snapshot picking) — the full task's file-writing
    path is covered in ``TestRestoreFromYadiskTask`` below.
    """

    def test_regex_round_trips_between_retention_and_restore(self):
        """retention and restore use the same pattern via _backup_make_pattern —
        a drift (one side tightens the time precision, the other doesn't)
        would leave keepers the restorer cannot see, or vice versa.
        """
        pattern = _backup_make_pattern(
            _tasks_common.BACKUP_FILENAME_PREFIX, _tasks_common.BACKUP_FILENAME_SUFFIX
        )
        assert pattern.match("dinary-2026-04-22T0317Z.db.zst")
        assert not pattern.match("dinary-2026-04-22.db.zst")
        assert not pattern.match("random.txt")

    def test_list_snapshots_parses_rclone_lsjson(self, monkeypatch):
        """The inventory parser must survive rclone's JSON shape and
        ignore non-matching filenames so human-uploaded noise in the
        same Yandex folder does not break the daily timer.
        """
        fake_json = json.dumps(
            [
                {"Name": "dinary-2026-04-22T0317Z.db.zst", "Size": 324000},
                {"Name": "dinary-2026-04-21T0317Z.db.zst", "Size": 322000},
                {"Name": "README.md", "Size": 100},
                {"Name": "dinary-malformed", "Size": 42},
            ],
        )

        def fake_check_output(cmd, text=True):
            assert "rclone" in cmd[0]
            assert "lsjson" in cmd[1]
            return fake_json

        monkeypatch.setattr(_tasks_backup.subprocess, "check_output", fake_check_output)
        result = _tasks_backup._yadisk_list_snapshots()
        assert result == [
            ("dinary-2026-04-21T0317Z.db.zst", 322000),
            ("dinary-2026-04-22T0317Z.db.zst", 324000),
        ]

    def test_pick_snapshot_latest_returns_newest(self):
        """``--snapshot latest`` must return the tail of the sorted
        list (sort is lexicographic on filenames, which is also
        chronological by construction). A regression that picks
        ``[0]`` instead would silently restore the oldest available
        snapshot and lose weeks of data.
        """
        snaps = [
            ("dinary-2026-04-20T0317Z.db.zst", 100),
            ("dinary-2026-04-21T0317Z.db.zst", 200),
            ("dinary-2026-04-22T0317Z.db.zst", 300),
        ]
        picked = _tasks_backup._pick_snapshot(snaps, "latest")
        assert picked == ("dinary-2026-04-22T0317Z.db.zst", 300)

    def test_pick_snapshot_by_date_prefix_matches_any_time_suffix(self):
        """Operators type ``--snapshot 2026-04-21`` rather than
        memorizing the time stamp. Partial-prefix match must be
        supported.
        """
        snaps = [
            ("dinary-2026-04-20T0317Z.db.zst", 100),
            ("dinary-2026-04-21T0317Z.db.zst", 200),
            ("dinary-2026-04-22T0317Z.db.zst", 300),
        ]
        picked = _tasks_backup._pick_snapshot(snaps, "2026-04-21")
        assert picked == ("dinary-2026-04-21T0317Z.db.zst", 200)

    def test_pick_snapshot_returns_none_on_miss(self):
        """A typo in ``--snapshot`` must return None so the task
        surfaces the full inventory in its error message rather than
        silently restoring the wrong date.
        """
        snaps = [("dinary-2026-04-20T0317Z.db.zst", 100)]
        assert _tasks_backup._pick_snapshot(snaps, "1999-01-01") is None

    def test_pick_snapshot_on_empty_returns_none(self):
        """Fresh bucket case: calls with an empty list return None
        rather than raising, so the caller can emit a "no snapshots
        found" message instead of an opaque IndexError.
        """
        assert _tasks_backup._pick_snapshot([], "latest") is None


@allure.epic("Deploy")
@allure.feature("backup-cloud-status: freshness check")
class TestBackupStatusHelpers:
    """Pure helpers behind ``inv backup-cloud-status``. The task itself is a
    thin wrapper over :func:`_replica_list_snapshots` (I/O) and
    :func:`_check_backup_freshness` (pure) — these tests pin the
    pure branches so the ok/stale/empty/unparseable transitions are
    locked down independently of SSH/rclone plumbing.
    """

    def test_parse_timestamp_round_trips_canonical_filename(self):
        """The canonical name produced by ``dinary-backup`` must parse
        to the exact UTC datetime it encodes. The single source of
        truth for "when was this backup produced" is the filename,
        not Yandex-side ModTime, so a silent drift here would make
        freshness checks lie.
        """
        ts = _tasks_backup._parse_snapshot_timestamp("dinary-2026-04-22T0317Z.db.zst")
        assert ts == _dt(2026, 4, 22, 3, 17, tzinfo=_tz.utc)

    def test_parse_timestamp_returns_none_on_unexpected_shape(self):
        """Human-uploaded noise in the same Yandex folder must not
        crash the parser: it returns ``None`` so the caller can treat
        it the same as "no timestamp" rather than surfacing a
        ValueError to cron.
        """
        assert _tasks_backup._parse_snapshot_timestamp("random.txt") is None
        assert _tasks_backup._parse_snapshot_timestamp("dinary-bad.db.zst") is None

    def test_check_freshness_ok_when_newest_inside_threshold(self):
        """Under-threshold → ``ok`` + exact age in hours. The newest
        snapshot is always the last entry of the sorted list — any
        regression that reads ``[0]`` would read the oldest and
        false-alert every day.
        """
        snaps = [
            ("dinary-2026-04-21T0317Z.db.zst", 100),
            ("dinary-2026-04-22T0317Z.db.zst", 200),
        ]
        now = _dt(2026, 4, 22, 10, 17, tzinfo=_tz.utc)
        verdict = _tasks_backup._check_backup_freshness(snaps, now, max_age_hours=26)
        assert verdict["status"] == "ok"
        assert verdict["newest"] == "dinary-2026-04-22T0317Z.db.zst"
        assert verdict["age_hours"] == pytest.approx(7.0)
        assert verdict["size_bytes"] == 200

    def test_check_freshness_stale_when_newest_older_than_threshold(self):
        """Over-threshold → ``stale``. Uses a 49h gap (two full days
        missed) so the threshold itself (26h default) is unambiguous.
        """
        snaps = [("dinary-2026-04-20T0317Z.db.zst", 100)]
        now = _dt(2026, 4, 22, 4, 17, tzinfo=_tz.utc)
        verdict = _tasks_backup._check_backup_freshness(snaps, now, max_age_hours=26)
        assert verdict["status"] == "stale"
        assert verdict["age_hours"] == pytest.approx(49.0)

    def test_check_freshness_empty_bucket(self):
        """No snapshots at all → ``empty`` (distinct from ``stale``
        so the alert message can point at the right failure mode:
        "nothing ever uploaded" vs "uploads stopped").
        """
        verdict = _tasks_backup._check_backup_freshness([], now=None, max_age_hours=26)
        assert verdict["status"] == "empty"
        assert verdict["newest"] is None
        assert verdict["age_hours"] is None
        assert verdict["threshold_hours"] == 26.0

    def test_check_freshness_unparseable_newest_is_stale(self):
        """A newest file that does not match the canonical timestamp
        shape (e.g. someone manually uploaded ``dinary-final.db.zst``)
        must surface as ``stale`` with ``age_hours=None`` — we refuse
        to guess a timestamp and the operator sees something is
        wrong.
        """
        snaps = [("dinary-final.db.zst", 42)]
        verdict = _tasks_backup._check_backup_freshness(snaps, now=None, max_age_hours=26)
        assert verdict["status"] == "stale"
        assert verdict["age_hours"] is None

    def test_format_line_ok(self):
        """Human summary must contain the tag, filename, age and
        threshold so the one-line log in ``sync_log`` is enough to
        diagnose without re-running the task.
        """
        line = _tasks_backup._format_backup_status_line({
            "status": "ok",
            "newest": "dinary-2026-04-22T0317Z.db.zst",
            "age_hours": 7.0,
            "size_bytes": 203456,
            "threshold_hours": 26.0,
        })
        assert line.startswith("OK: ")
        assert "dinary-2026-04-22T0317Z.db.zst" in line
        assert "7.0h" in line
        assert "26h" in line

    def test_format_line_stale(self):
        """``stale`` shows the ``STALE:`` tag — cron wrapper greps
        only the exit code, but the operator seeing the log line
        needs to recognize the failure mode at a glance.
        """
        line = _tasks_backup._format_backup_status_line({
            "status": "stale",
            "newest": "dinary-2026-04-20T0317Z.db.zst",
            "age_hours": 49.0,
            "size_bytes": 200000,
            "threshold_hours": 26.0,
        })
        assert line.startswith("STALE: ")
        assert "49.0h" in line

    def test_format_line_empty(self):
        """``empty`` points at the remote path so the operator can
        jump straight to rclone/Yandex to investigate — the message
        is not just "STALE" without context.
        """
        line = _tasks_backup._format_backup_status_line({
            "status": "empty",
            "newest": None,
            "age_hours": None,
            "size_bytes": None,
            "threshold_hours": 26.0,
        })
        assert line.startswith("STALE: no snapshots")
        assert _tasks_common.BACKUP_RCLONE_REMOTE in line
        assert _tasks_common.BACKUP_RCLONE_PATH in line


@allure.epic("Deploy")
@allure.feature("backup-cloud-status: task")
class TestBackupStatusTask:
    """End-to-end tests for the ``inv backup-cloud-status`` task: mocks the
    two I/O seams (``_replica_list_snapshots`` and ``_dt.now``) and
    pins the print/exit behavior.
    """

    @pytest.fixture
    def _mock_now(self, monkeypatch):
        """Freeze the clock at a well-known UTC timestamp so test
        expectations don't depend on the runner's wall clock. The
        task only reads ``datetime.now(tz=utc)`` once.
        """
        frozen = _dt(2026, 4, 22, 10, 17, tzinfo=_tz.utc)

        class _FrozenDateTime(_dt):
            @classmethod
            def now(cls, tz=None):
                return frozen

        monkeypatch.setattr(_tasks_backup, "_dt", _FrozenDateTime)

    def test_ok_prints_summary_and_does_not_exit(
        self, monkeypatch, capsys, _mock_now
    ):
        """Happy path: fresh snapshot → one-line summary on stdout,
        no sys.exit(1). The task's contract for cron is "exit code
        0 means everything is fine"; a regression that exits 1 on OK
        would false-alert the operator every hour.
        """
        monkeypatch.setattr(
            _tasks_backup, "_replica_list_snapshots",
            lambda: [("dinary-2026-04-22T0317Z.db.zst", 200)],
        )
        _tasks.backup_status.body(MagicMock())
        out = capsys.readouterr().out
        assert out.startswith("OK: ")
        assert "dinary-2026-04-22T0317Z.db.zst" in out

    def test_stale_exits_one(self, monkeypatch, _mock_now):
        """Stale snapshot → ``SystemExit(1)``. The cron wrapper only
        looks at the exit code to decide whether to fire
        ``send_fail_email``.
        """
        monkeypatch.setattr(
            _tasks_backup, "_replica_list_snapshots",
            lambda: [("dinary-2026-04-20T0317Z.db.zst", 200)],
        )
        with pytest.raises(SystemExit) as exc:
            _tasks.backup_status.body(MagicMock())
        assert exc.value.code == 1

    def test_empty_exits_one(self, monkeypatch, _mock_now):
        """No snapshots at all must also signal failure — an
        always-empty backup bucket is the worst-case silent failure
        we're protecting against.
        """
        monkeypatch.setattr(_tasks_backup, "_replica_list_snapshots", lambda: [])
        with pytest.raises(SystemExit) as exc:
            _tasks.backup_status.body(MagicMock())
        assert exc.value.code == 1

    def test_json_output_emits_machine_readable(
        self, monkeypatch, capsys, _mock_now
    ):
        """``--json-output`` emits a single JSON object on stdout so
        other tooling (future dashboard) can consume the same verdict
        without scraping the human line.
        """
        monkeypatch.setattr(
            _tasks_backup, "_replica_list_snapshots",
            lambda: [("dinary-2026-04-22T0317Z.db.zst", 200)],
        )
        _tasks.backup_status.body(MagicMock(), json_output=True)
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["status"] == "ok"
        assert payload["newest"] == "dinary-2026-04-22T0317Z.db.zst"
        assert payload["age_hours"] == pytest.approx(7.0)
        assert payload["threshold_hours"] == 26.0

    def test_max_age_hours_override_flips_ok_to_stale(
        self, monkeypatch, capsys, _mock_now
    ):
        """``--max-age-hours`` lowers the threshold so the operator
        can verify a fresh backup has landed during an incident. A
        7h-old backup with a 3h threshold must flip to ``stale``.
        """
        monkeypatch.setattr(
            _tasks_backup, "_replica_list_snapshots",
            lambda: [("dinary-2026-04-22T0317Z.db.zst", 200)],
        )
        with pytest.raises(SystemExit) as exc:
            _tasks.backup_status.body(MagicMock(), max_age_hours=3)
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert out.startswith("STALE: ")


@allure.epic("Deploy")
@allure.feature("backup-cloud-restore: task")
@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "backup-cloud-restore shells out to the zstd and sqlite3 CLI "
        "binaries, which are not on the Windows CI runner path. The "
        "task itself only targets Linux (VM 1) / macOS (operator "
        "laptop), so skipping Windows here matches the deploy matrix."
    ),
)
class TestRestoreFromYadiskTask:
    """End-to-end tests for the destructive path: download, decompress,
    validate, preserve-and-replace. Uses real SQLite + zstd on
    ``tmp_path`` so the PRAGMA integrity_check path and the backup-
    before-overwrite behavior are exercised against actual file ops.
    """

    @pytest.fixture
    def _cwd(self, tmp_path, monkeypatch):
        """``restore_from_yadisk`` writes to ``./data/dinary.db``."""
        (tmp_path / "data").mkdir()
        monkeypatch.chdir(tmp_path)
        return tmp_path

    @staticmethod
    def _make_sqlite(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as con:
            con.executescript(
                "CREATE TABLE expense (id INTEGER PRIMARY KEY, amount REAL);"
                "INSERT INTO expense (amount) VALUES (1.0), (2.0);",
            )

    @pytest.fixture
    def _mock_binaries_present(self, monkeypatch):
        """rclone / sqlite3 / zstd pre-flight passes. Keep the spy
        ordering deterministic by pretending every ``which`` hits.
        """
        monkeypatch.setattr(_tasks_backup.shutil, "which", lambda name: f"/fake/{name}")

    @pytest.fixture
    def _fake_snapshot(self, tmp_path, monkeypatch, _mock_binaries_present):
        """Stand up a fake Yandex-like snapshot on ``tmp_path`` and
        stub ``_yadisk_list_snapshots`` plus ``c.run`` to make rclone
        a file copy and zstd a real decompression.
        """
        snapshot_name = "dinary-2026-04-22T0317Z.db.zst"
        remote_root = tmp_path / "fake-yadisk"
        remote_root.mkdir()
        plain = remote_root / "plain.db"
        self._make_sqlite(plain)
        archive = remote_root / snapshot_name
        subprocess.run(
            ["zstd", "-q", "-19", str(plain), "-o", str(archive)],
            check=True,
        )

        monkeypatch.setattr(
            _tasks_backup,
            "_yadisk_list_snapshots",
            lambda: [(snapshot_name, archive.stat().st_size)],
        )

        class FakeContext:
            def run(self_inner, cmd):
                tokens = shlex.split(cmd)
                if tokens[0] == "rclone":
                    src = f"{_tasks_common.BACKUP_RCLONE_REMOTE}:{_tasks_common.BACKUP_RCLONE_PATH}/{snapshot_name}"
                    assert tokens[:2] == ["rclone", "copyto"]
                    assert tokens[2] == src
                    shutil.copyfile(archive, tokens[3])
                    return None
                if tokens[0] == "zstd":
                    subprocess.run(tokens, check=True)
                    return None
                raise AssertionError(f"unexpected command: {cmd}")

        return FakeContext(), snapshot_name

    def test_restore_writes_data_dinary_db_from_snapshot(
        self, _cwd, _fake_snapshot, capsys,
    ):
        """Happy path: no existing ``data/dinary.db``, ``--yes``
        implicit (no prompt when target is absent). Restored file
        must contain the rows from the snapshot.
        """
        c, _name = _fake_snapshot
        _tasks.restore_from_yadisk.body(c, yes=True)

        target = _cwd / "data" / "dinary.db"
        assert target.exists()
        with sqlite3.connect(target) as con:
            count = con.execute("SELECT COUNT(*) FROM expense").fetchone()[0]
        assert count == 2

    def test_preserves_existing_db_before_overwrite(
        self, _cwd, _fake_snapshot, capsys,
    ):
        """An existing ``data/dinary.db`` (non-empty) MUST end up at
        ``data/dinary.db.before-restore-<ts>`` before the replacement
        lands. With ``--yes``, no prompt, but the preservation still
        applies.
        """
        target = _cwd / "data" / "dinary.db"
        self._make_sqlite(target)
        original_bytes = target.read_bytes()
        c, _name = _fake_snapshot

        _tasks.restore_from_yadisk.body(c, yes=True)

        preserved = sorted(
            p for p in (_cwd / "data").iterdir()
            if p.name.startswith("dinary.db.before-restore-")
        )
        assert len(preserved) == 1
        assert preserved[0].read_bytes() == original_bytes

    def test_refuses_to_restore_corrupt_snapshot(
        self, _cwd, monkeypatch, tmp_path, _mock_binaries_present, capsys,
    ):
        """A snapshot that fails ``PRAGMA integrity_check`` must
        leave ``data/dinary.db`` untouched. The preserved-backup
        dance only happens on the success branch; a corrupt
        archive gets the operator a loud stderr, not a silent swap.
        """
        snapshot_name = "dinary-2026-04-22T0317Z.db.zst"
        remote_root = tmp_path / "fake-yadisk"
        remote_root.mkdir()
        corrupt = remote_root / "corrupt.db"
        corrupt.write_bytes(b"not a sqlite file")
        archive = remote_root / snapshot_name
        subprocess.run(
            ["zstd", "-q", "-19", str(corrupt), "-o", str(archive)],
            check=True,
        )

        monkeypatch.setattr(
            _tasks_backup,
            "_yadisk_list_snapshots",
            lambda: [(snapshot_name, archive.stat().st_size)],
        )

        existing = _cwd / "data" / "dinary.db"
        self._make_sqlite(existing)
        existing_bytes = existing.read_bytes()

        class FakeContext:
            def run(self_inner, cmd):
                tokens = shlex.split(cmd)
                if tokens[0] == "rclone":
                    shutil.copyfile(archive, tokens[3])
                elif tokens[0] == "zstd":
                    subprocess.run(tokens, check=True)
                else:
                    raise AssertionError(f"unexpected: {cmd}")

        with pytest.raises(SystemExit) as excinfo:
            _tasks.restore_from_yadisk.body(FakeContext(), yes=True)

        assert excinfo.value.code == 1
        assert existing.read_bytes() == existing_bytes
        preserved = [
            p for p in (_cwd / "data").iterdir()
            if p.name.startswith("dinary.db.before-restore-")
        ]
        assert preserved == []

    def test_list_only_is_readonly(
        self, _cwd, _fake_snapshot, capsys,
    ):
        """``--list-only`` must never touch the local filesystem —
        no downloads, no preservation, no overwrite. The test sets a
        non-empty ``data/dinary.db`` and asserts it is byte-unchanged
        after the call.
        """
        target = _cwd / "data" / "dinary.db"
        self._make_sqlite(target)
        before = target.read_bytes()
        c, _name = _fake_snapshot

        _tasks.restore_from_yadisk.body(c, list_only=True)

        assert target.read_bytes() == before
        assert (_cwd / "data").name == "data"
        preserved = [
            p for p in (_cwd / "data").iterdir()
            if p.name.startswith("dinary.db.before-restore-")
        ]
        assert preserved == []
        out = capsys.readouterr().out
        assert "dinary-2026-04-22T0317Z.db.zst" in out

    def test_exits_when_no_snapshots_available(
        self, _cwd, _mock_binaries_present, monkeypatch,
    ):
        """Empty-bucket case (fresh setup or post-wipe) must exit 1
        with a message pointing at the Yandex path, not crash with
        an IndexError deep in ``_pick_snapshot``.
        """
        monkeypatch.setattr(_tasks_backup, "_yadisk_list_snapshots", lambda: [])
        with pytest.raises(SystemExit) as excinfo:
            _tasks.restore_from_yadisk.body(MagicMock())
        assert excinfo.value.code == 1

    def test_exits_when_snapshot_arg_does_not_match(
        self, _cwd, _fake_snapshot, capsys,
    ):
        """Typo in ``--snapshot``: task must surface the available
        inventory in stderr and exit 1, so the operator sees valid
        keys to retry with.
        """
        c, _name = _fake_snapshot
        with pytest.raises(SystemExit) as excinfo:
            _tasks.restore_from_yadisk.body(c, snapshot="1999-01-01")
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "1999-01-01" in err
        assert "dinary-2026-04-22T0317Z.db.zst" in err

    def test_exits_when_local_tools_missing(
        self, _cwd, monkeypatch,
    ):
        """Pre-flight must catch missing rclone/sqlite3/zstd with a
        single consolidated error message, not fail mid-pipeline
        after the download has already started.
        """
        monkeypatch.setattr(_tasks_backup.shutil, "which", lambda name: None)
        with pytest.raises(SystemExit) as excinfo:
            _tasks.restore_from_yadisk.body(MagicMock())
        assert excinfo.value.code == 1

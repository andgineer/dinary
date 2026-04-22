"""Tests for helpers in ``tasks.py`` (deploy/operator orchestration).

We only cover pure helpers here; tasks themselves run shell commands
against a real server and are exercised via the deploy flow.
"""

import importlib.util
import io
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import allure
import pytest

_TASKS_PATH = Path(__file__).resolve().parent.parent / "tasks.py"
_spec = importlib.util.spec_from_file_location("_dinary_tasks", _TASKS_PATH)
_tasks = importlib.util.module_from_spec(_spec)
sys.modules["_dinary_tasks"] = _tasks
_spec.loader.exec_module(_tasks)
_systemd_quote = _tasks._systemd_quote
_remote_snapshot_cmd = _tasks._remote_snapshot_cmd


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
        monkeypatch.setattr(_tasks, "_host", lambda: "ubuntu@test.invalid")

    def test_returns_raw_bytes_not_decoded_str(self, monkeypatch):
        captured = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b'{"k": "v"}\n', stderr=b"",
        )
        monkeypatch.setattr(_tasks.subprocess, "run", lambda *a, **kw: captured)
        out = _tasks._ssh_capture_bytes("whoami")
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

        monkeypatch.setattr(_tasks.subprocess, "run", fake_run)
        monkeypatch.setattr(_tasks, "_host", lambda: "ubuntu@203.0.113.1")
        _tasks._ssh_capture_bytes("echo hello")

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
        monkeypatch.setattr(_tasks.subprocess, "run", lambda *a, **kw: captured)
        out = _tasks._ssh_capture_bytes("whatever")
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

        monkeypatch.setattr(_tasks, "_ssh_capture_bytes", fake)
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
        _tasks._run_report_module(c, "income", [], remote=True)
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
        _tasks._run_report_module(c, "income", ["--csv"], remote=True)
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
        _tasks._run_report_module(c, "income", ["--json"], remote=True)
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
        _tasks._run_report_module(c, "income", [], remote=True)
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
        _tasks._run_report_module(c, "expenses", [], remote=True)
        out = capsys.readouterr().out
        assert "\ufffd" not in out
        assert "путешествия" in out

    def test_expenses_remote_forwards_filter_flags_but_not_format_flags(
        self, _fake_ssh_bytes, capsys,
    ):
        _fake_ssh_bytes.payload = b"[]\n"
        c = MagicMock()
        _tasks._run_report_module(
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

        monkeypatch.setattr(_tasks, "_ssh_capture_bytes", fake_bytes)
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
        script = _tasks._litestream_install_script()
        assert f"litestream-{_tasks.LITESTREAM_VERSION}-linux-x86_64.deb" in script
        assert f"litestream-{_tasks.LITESTREAM_VERSION}-linux-arm64.deb" in script

    def test_x86_64_and_amd64_both_map_to_x86_64_asset(self):
        """``uname -m`` historically varies: Linux kernels on Intel
        report ``x86_64``, but some embedded userlands and Debian
        dpkg spelling use ``amd64``. Both must route to the same
        Litestream asset.
        """
        script = _tasks._litestream_install_script()
        assert (
            f"x86_64|amd64) ASSET=litestream-{_tasks.LITESTREAM_VERSION}-linux-x86_64.deb"
            in script
        )

    def test_aarch64_and_arm64_both_map_to_arm64_asset(self):
        """Same double-spelling problem on Ampere / Graviton:
        Linux kernels report ``aarch64``, Debian userland prefers
        ``arm64``. Both must pick the arm64 asset.
        """
        script = _tasks._litestream_install_script()
        assert (
            f"aarch64|arm64) ASSET=litestream-{_tasks.LITESTREAM_VERSION}-linux-arm64.deb"
            in script
        )

    def test_unsupported_arch_exits_with_actionable_error(self):
        """An unsupported ``uname -m`` (e.g. ``riscv64``) must error
        out loudly with the offending arch and the pinned version,
        not silently ``curl 404`` a non-existent asset.
        """
        script = _tasks._litestream_install_script()
        assert f'Unsupported arch $ARCH for litestream {_tasks.LITESTREAM_VERSION}' in script
        assert "*) echo" in script
        assert "exit 1" in script

    def test_download_url_uses_github_release_path_for_pinned_version(self):
        """The asset URL is ``<.../releases/download/v<ver>/$ASSET>``
        (upstream's canonical layout) — a typo in the ``v`` prefix or
        the path layout here is invisible until bootstrap day.
        """
        script = _tasks._litestream_install_script()
        assert (
            "https://github.com/benbjohnson/litestream/releases/download/"
            f"v{_tasks.LITESTREAM_VERSION}/$ASSET"
            in script
        )

    def test_script_is_idempotent_when_litestream_already_installed(self):
        """Re-running ``inv litestream-setup`` must be cheap: no new
        download when the binary is already on PATH. The outer
        ``if command -v litestream`` gate is the only thing
        preserving that property — pin it.
        """
        script = _tasks._litestream_install_script()
        assert "if ! command -v litestream >/dev/null" in script

    def test_version_parameter_allows_future_upgrade(self):
        """Pure-helper ergonomics: passing a different version
        interpolates cleanly into every line that mentions it, so a
        future upgrade is a one-line constant bump rather than a
        string-surgery PR.
        """
        script = _tasks._litestream_install_script(version="0.6.0")
        assert "litestream-0.6.0-linux-x86_64.deb" in script
        assert "litestream-0.6.0-linux-arm64.deb" in script
        assert "/releases/download/v0.6.0/$ASSET" in script
        # Sanity: the pinned-default version is NOT leaking into a
        # caller-overridden script.
        assert f"litestream-{_tasks.LITESTREAM_VERSION}" not in script


@allure.epic("Deploy")
@allure.feature("verify-db: integrity_check + foreign_key_check gate")
class TestVerifyDbLocal:
    """``inv verify-db`` runs SQLite's two ship-blocker pragmas against
    ``data/dinary.db`` (local) or a snapshot of the prod DB (remote).
    The remote path is shell-only and tested via the snapshot-wrapper
    assertions elsewhere; these tests cover the local happy path,
    the hard-failure path (FK violation), and the ``no DB`` guard.

    The fixture builds real SQLite files on ``tmp_path`` so the test
    exercises the production ``sqlite3`` CLI path (the one
    ``tasks.verify_db`` invokes via ``subprocess.run``) — a pure mock
    would not catch a regression that, e.g., reordered the two
    ``PRAGMA`` statements or dropped the output-line check.
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
        import sqlite3 as _sqlite

        with _sqlite.connect(db_path) as con:
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
        import sqlite3 as _sqlite

        with _sqlite.connect(db_path) as con:
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

        monkeypatch.setattr(_tasks, "_ssh_capture_bytes", fake_bytes)
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

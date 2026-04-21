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
    / ``inv import-report-2d-3d --remote`` cannot open the primary
    prod DuckDB file directly — the running uvicorn worker holds an
    exclusive single-writer lock on it (DuckDB 1.x).
    ``_remote_snapshot_cmd`` wraps the report invocation in a
    snapshot-copy prologue so the read-only module runs against an
    isolated ``/tmp`` copy instead. These tests pin the exact shape
    of the emitted command so a future refactor cannot silently drop
    the snapshot step and revive the
    ``IOException: Could not set lock`` failure mode reported by
    the operator.
    """

    def test_copies_primary_db_to_tmp_snapshot(self):
        cmd = _remote_snapshot_cmd("dinary.reports.income", [])
        assert "cp /home/ubuntu/dinary-server/data/dinary.duckdb ${SNAP}" in cmd

    def test_copies_wal_sidecar_when_present(self):
        """The WAL sidecar may not exist (fresh install, post-checkpoint),
        so the copy must be tolerant of a missing file — otherwise
        ``set -e`` would abort on every clean install."""
        cmd = _remote_snapshot_cmd("dinary.reports.income", [])
        assert "cp /home/ubuntu/dinary-server/data/dinary.duckdb.wal" in cmd
        assert "2>/dev/null || true" in cmd

    def test_points_data_path_at_snapshot_not_primary_db(self):
        cmd = _remote_snapshot_cmd("dinary.reports.expenses", ["--csv"])
        assert "DINARY_DATA_PATH=${SNAP}" in cmd
        # Belt-and-suspenders: the emitted command must NEVER point the
        # report module at the live, locked primary file.
        assert "DINARY_DATA_PATH=/home/ubuntu/dinary-server/data/dinary.duckdb " not in cmd

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
        multi-hundred-MB ``.duckdb`` snapshot in ``/tmp``. The trap
        is registered before the ``cp`` so even an interrupt between
        registration and completion cannot orphan the file.
        """
        cmd = _remote_snapshot_cmd("dinary.reports.income", [])
        assert 'trap "rm -f ${SNAP} ${SNAP}.wal" EXIT' in cmd
        # trap must come BEFORE the first cp so an interrupt between
        # the copy and the trap-registration cannot leak.
        trap_pos = cmd.index("trap")
        first_cp_pos = cmd.index("cp /home/ubuntu/dinary-server/data/dinary.duckdb ${SNAP}")
        assert trap_pos < first_cp_pos

    def test_uses_set_e_so_cp_failure_is_visible(self):
        """If the ``cp`` of the primary DB fails, the operator must
        see the error immediately — otherwise the subsequent
        ``DINARY_DATA_PATH=${SNAP}`` would run against a missing /
        empty file and emit a confusing "DB not found" downstream.
        """
        cmd = _remote_snapshot_cmd("dinary.reports.income", [])
        assert cmd.startswith("set -e; ")


@allure.epic("Deploy")
@allure.feature("SSH bytes-capture (UTF-8 chunk-boundary safety)")
class TestSshCaptureBytes:
    """``inv report-* --remote`` used to hand stdout off to
    ``invoke.c.run(..., hide='stdout')``, whose
    :meth:`invoke.runners.Runner.decode` decodes each
    ``read_proc_stdout`` chunk independently with ``errors='replace'``
    — no incremental decoder. When a multi-byte UTF-8 character
    (``─`` = ``E2 94 80``, Cyrillic letters) lands on a chunk
    boundary, each side of the split becomes U+FFFD (``�``). That's
    the ``путешест��ия`` / ``├──────���┼`` corruption the operator
    reported. The fix is to capture bytes via a raw subprocess,
    decode **once** at the end, and only then parse / render. These
    tests pin the new helper's contract: it uses ``subprocess.run``
    (not invoke), returns bytes, and round-trips a realistic UTF-8
    payload without any replacement characters.
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
        """The server process holds an exclusive DuckDB lock on
        ``data/dinary.duckdb``; a read-only report that opens the same
        path fails with ``IOException: Could not set lock``. The
        ``import-report-2d-3d --remote`` flow must go through the
        same ``/tmp`` snapshot wrapper as ``inv report-*``.
        """
        _spy_transports.ssh_bytes_payload = self._sample_payload()
        c = MagicMock()
        self._run(c, remote=True)

        cmd = _spy_transports.ssh_bytes_cmd or ""
        # Snapshot wrapper invariants: must copy the live DB aside,
        # run the report against the snapshot, and set up the trap
        # before the copy.
        assert "SNAP=/tmp/dinary-report-snapshot-$$" in cmd
        assert "cp /home/ubuntu/dinary-server/data/dinary.duckdb ${SNAP}" in cmd
        assert "DINARY_DATA_PATH=${SNAP}" in cmd
        assert 'trap "rm -f ${SNAP} ${SNAP}.wal" EXIT' in cmd
        assert cmd.index("trap") < cmd.index(
            "cp /home/ubuntu/dinary-server/data/dinary.duckdb ${SNAP}"
        )

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

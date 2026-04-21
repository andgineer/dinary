"""Tests for helpers in ``tasks.py`` (deploy/operator orchestration).

We only cover pure helpers here; tasks themselves run shell commands
against a real server and are exercised via the deploy flow.
"""

import importlib.util
import sys
from pathlib import Path

import allure

_TASKS_PATH = Path(__file__).resolve().parent.parent / "tasks.py"
_spec = importlib.util.spec_from_file_location("_dinary_tasks", _TASKS_PATH)
_tasks = importlib.util.module_from_spec(_spec)
sys.modules["_dinary_tasks"] = _tasks
_spec.loader.exec_module(_tasks)
_systemd_quote = _tasks._systemd_quote
_remote_report_cmd = _tasks._remote_report_cmd


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
class TestRemoteReportCmd:
    """``inv show-income --remote`` / ``inv show-expenses --remote``
    cannot open the primary prod DuckDB file directly — the running
    uvicorn worker holds an exclusive single-writer lock on it
    (DuckDB 1.x). ``_remote_report_cmd`` wraps the report invocation
    in a snapshot-copy prologue so the read-only report runs
    against an isolated ``/tmp`` copy instead. These tests pin the
    exact shape of the emitted command so a future refactor cannot
    silently drop the snapshot step and revive the
    ``IOException: Could not set lock`` failure mode reported by
    the operator.
    """

    def test_copies_primary_db_to_tmp_snapshot(self):
        cmd = _remote_report_cmd("income", [])
        assert "cp /home/ubuntu/dinary-server/data/dinary.duckdb ${SNAP}" in cmd

    def test_copies_wal_sidecar_when_present(self):
        """The WAL sidecar may not exist (fresh install, post-checkpoint),
        so the copy must be tolerant of a missing file — otherwise
        ``set -e`` would abort on every clean install."""
        cmd = _remote_report_cmd("income", [])
        assert "cp /home/ubuntu/dinary-server/data/dinary.duckdb.wal" in cmd
        assert "2>/dev/null || true" in cmd

    def test_points_data_path_at_snapshot_not_primary_db(self):
        cmd = _remote_report_cmd("expenses", ["--csv"])
        assert "DINARY_DATA_PATH=${SNAP}" in cmd
        # Belt-and-suspenders: the emitted command must NEVER point the
        # report module at the live, locked primary file.
        assert "DINARY_DATA_PATH=/home/ubuntu/dinary-server/data/dinary.duckdb " not in cmd

    def test_passes_flags_through_to_module(self):
        cmd = _remote_report_cmd("expenses", ["--year", "2026", "--csv"])
        assert "uv run python -m dinary.reports.expenses --year 2026 --csv" in cmd

    def test_flagless_invocation_has_no_trailing_space(self):
        cmd = _remote_report_cmd("income", [])
        assert "uv run python -m dinary.reports.income" in cmd
        # Avoid a trailing space that would render as an empty argv
        # token in the remote shell.
        assert "dinary.reports.income " not in cmd or "dinary.reports.income --" in cmd

    def test_snapshot_is_pid_scoped_for_parallel_runs(self):
        """Two operators running ``inv show-income --remote`` at the
        same time must not clobber each other's ``/tmp`` file. ``$$``
        expands to the remote shell PID and is how we keep them
        isolated without coordinating via a lock file.
        """
        cmd = _remote_report_cmd("income", [])
        assert "$$" in cmd
        assert "/tmp/dinary-report-snapshot-$$" in cmd

    def test_trap_cleans_up_snapshot_on_exit(self):
        """A failing report (or ``Ctrl-C``) must not leak a
        multi-hundred-MB ``.duckdb`` snapshot in ``/tmp``. The trap
        is registered before the ``cp`` so even an interrupt between
        registration and completion cannot orphan the file.
        """
        cmd = _remote_report_cmd("income", [])
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
        cmd = _remote_report_cmd("income", [])
        assert cmd.startswith("set -e; ")

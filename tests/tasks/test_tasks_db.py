"""Tests for ``inv verify-db``, ``inv restore-yoyo``, and ``inv restore-litestream``
in :mod:`tasks.db`.

Local verify-db path uses real SQLite files on ``tmp_path``. The restore tasks
are shell-only: SSH calls are stubbed so tests pin command shape and guard
conditions without touching a real server.
"""

import sqlite3
from unittest.mock import MagicMock

import allure
import pytest

import tasks
import tasks.db


@allure.epic("Infrastructure")
@allure.feature("Deploy")
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
        return tasks.verify_db.body(c, remote=remote)

    @pytest.fixture
    def _cwd(self, tmp_path, monkeypatch):
        """``verify_db`` reads ``data/dinary.db`` relative to cwd."""
        (tmp_path / "data").mkdir()
        monkeypatch.chdir(tmp_path)
        return tmp_path

    def test_passes_on_healthy_db(self, _cwd, capsys):
        db_path = _cwd / "data" / "dinary.db"

        con = sqlite3.connect(db_path)
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
        con.close()
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

        con = sqlite3.connect(db_path)
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
        con.close()
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
        clear message, not a cryptic sqlite3 error.
        """
        # Note: _cwd already created ``data/`` but not ``dinary.db``.
        c = MagicMock()
        with pytest.raises(SystemExit) as excinfo:
            self._verify_db(c)
        assert excinfo.value.code == 1
        assert "No local DB" in capsys.readouterr().err


@allure.epic("Infrastructure")
@allure.feature("Deploy")
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

        monkeypatch.setattr(tasks.db, "ssh_capture_bytes", fake_bytes)
        return spy

    def test_remote_snapshots_live_db_before_pragma_checks(self, _spy):
        tasks.verify_db.body(MagicMock(), remote=True)
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
        tasks.verify_db.body(MagicMock(), remote=True)
        cmd = _spy.cmd or ""
        # Both pragmas must target ``$SNAP``, not the live DB path —
        # a regression that shortened this to ``sqlite3 "$DB" "..."``
        # would race with WAL checkpoints on a busy server.
        assert 'sqlite3 "$SNAP" "PRAGMA integrity_check; PRAGMA foreign_key_check;"' in cmd

    def test_remote_propagates_pragma_failure_as_exit_1(self, _spy, capsys):
        """When the remote snapshot reports any issue, the local side
        must still honour the ``lines == ["ok"]`` contract and exit 1
        with the pragma output visible to the operator.
        """
        _spy.payload = b"ok\nchild|1|parent|0\n"
        with pytest.raises(SystemExit) as excinfo:
            tasks.verify_db.body(MagicMock(), remote=True)
        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "child|1|parent|0" in captured.out
        assert "=== verify-db FAILED ===" in captured.err

    def test_remote_reports_ok_when_snapshot_is_healthy(self, _spy, capsys):
        _spy.payload = b"ok\n"
        tasks.verify_db.body(MagicMock(), remote=True)
        out = capsys.readouterr().out
        assert "=== verify-db OK ===" in out


@allure.epic("Infrastructure")
@allure.feature("Deploy")
class TestRestoreYoyo:
    """``inv restore-yoyo --to=<prefix>`` rolls back server migrations.

    SSH calls are stubbed so these tests run without a live server. The
    service-running guard must refuse to proceed and emit a clear message.
    """

    _TWO_MIGRATIONS = ["0001_initial_schema", "0002_add_something"]

    @pytest.fixture
    def _two_migrations(self, monkeypatch):
        monkeypatch.setattr(tasks.db, "migration_ids", lambda: self._TWO_MIGRATIONS)

    @pytest.fixture
    def _service_inactive(self, monkeypatch):
        monkeypatch.setattr(tasks.db, "ssh_capture", lambda c, cmd: "inactive\n")

    @pytest.fixture
    def _service_active(self, monkeypatch):
        monkeypatch.setattr(tasks.db, "ssh_capture", lambda c, cmd: "active\n")

    @pytest.fixture
    def _ssh_run_spy(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(tasks.db, "ssh_run", lambda c, cmd: calls.append(cmd))
        return calls

    def test_invalid_prefix_exits_1(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            tasks.restore_yoyo.body(MagicMock(), to="9999")
        assert excinfo.value.code == 1
        assert "9999" in capsys.readouterr().err

    def test_nothing_to_rollback_prints_message(self, capsys):
        """With only one migration file, --to=0001 finds the target but
        nothing to roll back. Must print a message and return without
        contacting the server at all.
        """
        tasks.restore_yoyo.body(MagicMock(), to="0001")
        out = capsys.readouterr().out
        assert "nothing to roll back" in out

    def test_service_running_exits_1_without_rollback(
        self, _two_migrations, _service_active, _ssh_run_spy, capsys
    ):
        """If dinary is active the task must refuse to proceed — the operator
        must stop the service first to avoid mid-migration crashes.
        """
        with pytest.raises(SystemExit) as excinfo:
            tasks.restore_yoyo.body(MagicMock(), to="0001")
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "dinary" in err
        assert "stop" in err.lower()
        assert _ssh_run_spy == []

    def test_happy_path_runs_yoyo_rollback_command(
        self, _two_migrations, _service_inactive, _ssh_run_spy
    ):
        tasks.restore_yoyo.body(MagicMock(), to="0001")
        assert len(_ssh_run_spy) == 1
        cmd = _ssh_run_spy[0]
        assert "yoyo rollback" in cmd
        assert "--batch" in cmd
        assert "-r 0002_add_something" in cmd


@allure.epic("Infrastructure")
@allure.feature("Deploy")
class TestRestoreLitestream:
    """``inv restore-litestream --at=<iso8601>`` restores the server DB from WAL.

    SSH calls are stubbed. Both dinary and litestream must be stopped before
    the task proceeds; tests verify each guard independently.
    """

    @pytest.fixture
    def _services_inactive(self, monkeypatch):
        monkeypatch.setattr(tasks.db, "ssh_capture", lambda c, cmd: "inactive\n")

    @pytest.fixture
    def _dinary_active(self, monkeypatch):
        def _capture(c, cmd):
            return "active\n" if "dinary" in cmd else "inactive\n"

        monkeypatch.setattr(tasks.db, "ssh_capture", _capture)

    @pytest.fixture
    def _litestream_active(self, monkeypatch):
        def _capture(c, cmd):
            return "active\n" if "litestream" in cmd else "inactive\n"

        monkeypatch.setattr(tasks.db, "ssh_capture", _capture)

    @pytest.fixture
    def _ssh_run_spy(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(tasks.db, "ssh_run", lambda c, cmd: calls.append(cmd))
        return calls

    def test_invalid_at_exits_1(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            tasks.restore_litestream.body(MagicMock(), at="not-a-date")
        assert excinfo.value.code == 1
        assert "not-a-date" in capsys.readouterr().err

    def test_dinary_running_exits_1_without_restore(self, _dinary_active, _ssh_run_spy, capsys):
        with pytest.raises(SystemExit) as excinfo:
            tasks.restore_litestream.body(MagicMock(), at="2026-06-22 14:30")
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "dinary" in err
        assert _ssh_run_spy == []

    def test_litestream_running_exits_1_without_restore(
        self, _litestream_active, _ssh_run_spy, capsys
    ):
        with pytest.raises(SystemExit) as excinfo:
            tasks.restore_litestream.body(MagicMock(), at="2026-06-22 14:30")
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "litestream" in err
        assert _ssh_run_spy == []

    def test_happy_path_runs_litestream_restore_then_mv(self, _services_inactive, _ssh_run_spy):
        tasks.restore_litestream.body(MagicMock(), at="2026-06-22 14:30")
        assert len(_ssh_run_spy) == 2
        assert "litestream restore" in _ssh_run_spy[0]
        assert "2026-06-22T14:30:00Z" in _ssh_run_spy[0]
        assert "mv" in _ssh_run_spy[1]

    def test_z_suffix_in_at_is_accepted(self, _services_inactive, _ssh_run_spy):
        tasks.restore_litestream.body(MagicMock(), at="2026-06-22T14:30:00Z")
        assert len(_ssh_run_spy) == 2
        assert "2026-06-22T14:30:00Z" in _ssh_run_spy[0]

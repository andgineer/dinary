"""Tests for ``inv verify-db`` (local + remote) in :mod:`tasks.db`.

Local path uses real SQLite files on ``tmp_path`` so the two
ship-blocker pragmas run through the stdlib bindings the task
actually calls. Remote path is shell-only so the assertions there
pin the snapshot-wrapper command shape.
"""

import sqlite3
from unittest.mock import MagicMock

import allure
import pytest

import tasks
import tasks.db


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
        return tasks.verify_db.body(c, remote=remote)

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

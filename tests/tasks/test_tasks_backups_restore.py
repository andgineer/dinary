"""Tests for ``backup-cloud-restore``: inventory + destructive replace.

Covers the discovery helpers (``yadisk_list_snapshots``,
``pick_snapshot``) and the end-to-end ``restore_from_yadisk`` task,
including the preserve-and-replace and integrity-check branches.
"""

import json
import shlex
import shutil
import sqlite3
import subprocess
import sys
from unittest.mock import MagicMock

import allure
import pytest

import tasks
import tasks.backups
from dinary.tools.backup_retention import _make_pattern
from dinary.tools.backup_snapshots import (
    BACKUP_FILENAME_PREFIX,
    BACKUP_FILENAME_SUFFIX,
    BACKUP_RCLONE_PATH,
    BACKUP_RCLONE_REMOTE,
)


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
        """retention and restore use the same pattern via _make_pattern —
        a drift (one side tightens the time precision, the other doesn't)
        would leave keepers the restorer cannot see, or vice versa.
        """
        pattern = _make_pattern(BACKUP_FILENAME_PREFIX, BACKUP_FILENAME_SUFFIX)
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

        monkeypatch.setattr(tasks.backups.subprocess, "check_output", fake_check_output)
        result = tasks.backups.yadisk_list_snapshots()
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
        picked = tasks.backups.pick_snapshot(snaps, "latest")
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
        picked = tasks.backups.pick_snapshot(snaps, "2026-04-21")
        assert picked == ("dinary-2026-04-21T0317Z.db.zst", 200)

    def test_pick_snapshot_returns_none_on_miss(self):
        """A typo in ``--snapshot`` must return None so the task
        surfaces the full inventory in its error message rather than
        silently restoring the wrong date.
        """
        snaps = [("dinary-2026-04-20T0317Z.db.zst", 100)]
        assert tasks.backups.pick_snapshot(snaps, "1999-01-01") is None

    def test_pick_snapshot_on_empty_returns_none(self):
        """Fresh bucket case: calls with an empty list return None
        rather than raising, so the caller can emit a "no snapshots
        found" message instead of an opaque IndexError.
        """
        assert tasks.backups.pick_snapshot([], "latest") is None


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
        Also stubs ``_env`` so the post-restore replica check does not
        require a real ``.deploy/.env``.
        """
        monkeypatch.setattr(tasks.backups.shutil, "which", lambda name: f"/fake/{name}")
        monkeypatch.setattr(tasks.backups, "_env", lambda: {})

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
            tasks.backups,
            "yadisk_list_snapshots",
            lambda: [(snapshot_name, archive.stat().st_size)],
        )

        class FakeContext:
            def run(self_inner, cmd):
                tokens = shlex.split(cmd)
                if tokens[0] == "rclone":
                    src = f"{BACKUP_RCLONE_REMOTE}:{BACKUP_RCLONE_PATH}/{snapshot_name}"
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
        self,
        _cwd,
        _fake_snapshot,
        capsys,
    ):
        """Happy path: no existing ``data/dinary.db``, ``--yes``
        implicit (no prompt when target is absent). Restored file
        must contain the rows from the snapshot.
        """
        c, _name = _fake_snapshot
        tasks.restore_from_yadisk.body(c, yes=True)

        target = _cwd / "data" / "dinary.db"
        assert target.exists()
        with sqlite3.connect(target) as con:
            count = con.execute("SELECT COUNT(*) FROM expense").fetchone()[0]
        assert count == 2

    def test_preserves_existing_db_before_overwrite(
        self,
        _cwd,
        _fake_snapshot,
        capsys,
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

        tasks.restore_from_yadisk.body(c, yes=True)

        preserved = sorted(
            p for p in (_cwd / "data").iterdir() if p.name.startswith("dinary.db.before-restore-")
        )
        assert len(preserved) == 1
        assert preserved[0].read_bytes() == original_bytes

    def test_refuses_to_restore_corrupt_snapshot(
        self,
        _cwd,
        monkeypatch,
        tmp_path,
        _mock_binaries_present,
        capsys,
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
            tasks.backups,
            "yadisk_list_snapshots",
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
            tasks.restore_from_yadisk.body(FakeContext(), yes=True)

        assert excinfo.value.code == 1
        assert existing.read_bytes() == existing_bytes
        preserved = [
            p for p in (_cwd / "data").iterdir() if p.name.startswith("dinary.db.before-restore-")
        ]
        assert preserved == []

    def test_list_only_is_readonly(
        self,
        _cwd,
        _fake_snapshot,
        capsys,
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

        tasks.restore_from_yadisk.body(c, list_only=True)

        assert target.read_bytes() == before
        assert (_cwd / "data").name == "data"
        preserved = [
            p for p in (_cwd / "data").iterdir() if p.name.startswith("dinary.db.before-restore-")
        ]
        assert preserved == []
        out = capsys.readouterr().out
        assert "dinary-2026-04-22T0317Z.db.zst" in out

    def test_exits_when_no_snapshots_available(
        self,
        _cwd,
        _mock_binaries_present,
        monkeypatch,
    ):
        """Empty-bucket case (fresh setup or post-wipe) must exit 1
        with a message pointing at the Yandex path, not crash with
        an IndexError deep in ``_pick_snapshot``.
        """
        monkeypatch.setattr(tasks.backups, "yadisk_list_snapshots", lambda: [])
        with pytest.raises(SystemExit) as excinfo:
            tasks.restore_from_yadisk.body(MagicMock())
        assert excinfo.value.code == 1

    def test_exits_when_snapshot_arg_does_not_match(
        self,
        _cwd,
        _fake_snapshot,
        capsys,
    ):
        """Typo in ``--snapshot``: task must surface the available
        inventory in stderr and exit 1, so the operator sees valid
        keys to retry with.
        """
        c, _name = _fake_snapshot
        with pytest.raises(SystemExit) as excinfo:
            tasks.restore_from_yadisk.body(c, snapshot="1999-01-01")
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "1999-01-01" in err
        assert "dinary-2026-04-22T0317Z.db.zst" in err

    def test_exits_when_local_tools_missing(
        self,
        _cwd,
        monkeypatch,
    ):
        """Pre-flight must catch missing rclone/sqlite3/zstd with a
        single consolidated error message, not fail mid-pipeline
        after the download has already started.
        """
        monkeypatch.setattr(tasks.backups.shutil, "which", lambda name: None)
        with pytest.raises(SystemExit) as excinfo:
            tasks.restore_from_yadisk.body(MagicMock())
        assert excinfo.value.code == 1

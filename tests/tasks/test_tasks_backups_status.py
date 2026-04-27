"""Tests for ``inv backup-cloud-status``: freshness check.

Covers the pure helpers (``parse_snapshot_timestamp``,
``check_backup_freshness``, ``format_backup_status_line``) and the
end-to-end task body, mocking the SSH/rclone seam.
"""

import json
from datetime import datetime as _dt
from datetime import timezone as _tz
from unittest.mock import MagicMock

import allure
import pytest

import tasks
import tasks.backups
from dinary.tools.backup_snapshots import (
    BACKUP_RCLONE_PATH,
    BACKUP_RCLONE_REMOTE,
)


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
        ts = tasks.backups.parse_snapshot_timestamp("dinary-2026-04-22T0317Z.db.zst")
        assert ts == _dt(2026, 4, 22, 3, 17, tzinfo=_tz.utc)

    def test_parse_timestamp_returns_none_on_unexpected_shape(self):
        """Human-uploaded noise in the same Yandex folder must not
        crash the parser: it returns ``None`` so the caller can treat
        it the same as "no timestamp" rather than surfacing a
        ValueError to cron.
        """
        assert tasks.backups.parse_snapshot_timestamp("random.txt") is None
        assert tasks.backups.parse_snapshot_timestamp("dinary-bad.db.zst") is None

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
        verdict = tasks.backups.check_backup_freshness(snaps, now, max_age_hours=26)
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
        verdict = tasks.backups.check_backup_freshness(snaps, now, max_age_hours=26)
        assert verdict["status"] == "stale"
        assert verdict["age_hours"] == pytest.approx(49.0)

    def test_check_freshness_empty_bucket(self):
        """No snapshots at all → ``empty`` (distinct from ``stale``
        so the alert message can point at the right failure mode:
        "nothing ever uploaded" vs "uploads stopped").
        """
        verdict = tasks.backups.check_backup_freshness([], now=None, max_age_hours=26)
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
        verdict = tasks.backups.check_backup_freshness(snaps, now=None, max_age_hours=26)
        assert verdict["status"] == "stale"
        assert verdict["age_hours"] is None

    def test_format_line_ok(self):
        """Human summary must contain the tag, filename, age and
        threshold so the one-line log in ``sync_log`` is enough to
        diagnose without re-running the task.
        """
        line = tasks.backups.format_backup_status_line(
            {
                "status": "ok",
                "newest": "dinary-2026-04-22T0317Z.db.zst",
                "age_hours": 7.0,
                "size_bytes": 203456,
                "threshold_hours": 26.0,
            }
        )
        assert line.startswith("OK: ")
        assert "dinary-2026-04-22T0317Z.db.zst" in line
        assert "7.0h" in line
        assert "26h" in line

    def test_format_line_stale(self):
        """``stale`` shows the ``STALE:`` tag — cron wrapper greps
        only the exit code, but the operator seeing the log line
        needs to recognize the failure mode at a glance.
        """
        line = tasks.backups.format_backup_status_line(
            {
                "status": "stale",
                "newest": "dinary-2026-04-20T0317Z.db.zst",
                "age_hours": 49.0,
                "size_bytes": 200000,
                "threshold_hours": 26.0,
            }
        )
        assert line.startswith("STALE: ")
        assert "49.0h" in line

    def test_format_line_empty(self):
        """``empty`` points at the remote path so the operator can
        jump straight to rclone/Yandex to investigate — the message
        is not just "STALE" without context.
        """
        line = tasks.backups.format_backup_status_line(
            {
                "status": "empty",
                "newest": None,
                "age_hours": None,
                "size_bytes": None,
                "threshold_hours": 26.0,
            }
        )
        assert line.startswith("STALE: no snapshots")
        assert BACKUP_RCLONE_REMOTE in line
        assert BACKUP_RCLONE_PATH in line


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

        monkeypatch.setattr(tasks.backups, "datetime", _FrozenDateTime)

    def test_ok_prints_summary_and_does_not_exit(self, monkeypatch, capsys, _mock_now):
        """Happy path: fresh snapshot → one-line summary on stdout,
        no sys.exit(1). The task's contract for cron is "exit code
        0 means everything is fine"; a regression that exits 1 on OK
        would false-alert the operator every hour.
        """
        monkeypatch.setattr(
            tasks.backups,
            "replica_list_snapshots",
            lambda: [("dinary-2026-04-22T0317Z.db.zst", 200)],
        )
        tasks.backup_status.body(MagicMock())
        out = capsys.readouterr().out
        assert out.startswith("OK: ")
        assert "dinary-2026-04-22T0317Z.db.zst" in out

    def test_stale_exits_one(self, monkeypatch, _mock_now):
        """Stale snapshot → ``SystemExit(1)``. The cron wrapper only
        looks at the exit code to decide whether to fire
        ``send_fail_email``.
        """
        monkeypatch.setattr(
            tasks.backups,
            "replica_list_snapshots",
            lambda: [("dinary-2026-04-20T0317Z.db.zst", 200)],
        )
        with pytest.raises(SystemExit) as exc:
            tasks.backup_status.body(MagicMock())
        assert exc.value.code == 1

    def test_empty_exits_one(self, monkeypatch, _mock_now):
        """No snapshots at all must also signal failure — an
        always-empty backup bucket is the worst-case silent failure
        we're protecting against.
        """
        monkeypatch.setattr(tasks.backups, "replica_list_snapshots", lambda: [])
        with pytest.raises(SystemExit) as exc:
            tasks.backup_status.body(MagicMock())
        assert exc.value.code == 1

    def test_json_output_emits_machine_readable(self, monkeypatch, capsys, _mock_now):
        """``--json-output`` emits a single JSON object on stdout so
        other tooling (future dashboard) can consume the same verdict
        without scraping the human line.
        """
        monkeypatch.setattr(
            tasks.backups,
            "replica_list_snapshots",
            lambda: [("dinary-2026-04-22T0317Z.db.zst", 200)],
        )
        tasks.backup_status.body(MagicMock(), json_output=True)
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["status"] == "ok"
        assert payload["newest"] == "dinary-2026-04-22T0317Z.db.zst"
        assert payload["age_hours"] == pytest.approx(7.0)
        assert payload["threshold_hours"] == 26.0

    def test_max_age_hours_override_flips_ok_to_stale(self, monkeypatch, capsys, _mock_now):
        """``--max-age-hours`` lowers the threshold so the operator
        can verify a fresh backup has landed during an incident. A
        7h-old backup with a 3h threshold must flip to ``stale``.
        """
        monkeypatch.setattr(
            tasks.backups,
            "replica_list_snapshots",
            lambda: [("dinary-2026-04-22T0317Z.db.zst", 200)],
        )
        with pytest.raises(SystemExit) as exc:
            tasks.backup_status.body(MagicMock(), max_age_hours=3)
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert out.startswith("STALE: ")

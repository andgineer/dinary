"""Yandex.Disk backup freshness monitoring.

Owns the ``inv backup-cloud-status`` task and the SSH-based snapshot
inventory it uses. Splits cleanly from the restore pipeline because
the monitoring path runs from any laptop with SSH access — it does not
need rclone configured locally.
"""

import json
import sys
from datetime import UTC, datetime

from invoke import task

from tasks.backups.backup_snapshots import (
    BACKUP_RCLONE_PATH,
    BACKUP_RCLONE_REMOTE,
    BACKUP_STALE_HOURS,
    check_backup_freshness,
    check_identical_backup_sizes,
    format_backup_status_line,
    format_frozen_replica_line,
    format_single_backup_line,
    parse_snapshot_lsjson,
)
from tasks.ssh_utils import ssh_replica_capture_bytes


def replica_list_snapshots():
    """Reuses VM2's already-configured ``yandex:`` remote, so the laptop can run
    freshness checks from cron without its own Yandex credentials."""
    raw = ssh_replica_capture_bytes(
        f"rclone lsjson {BACKUP_RCLONE_REMOTE}:{BACKUP_RCLONE_PATH}/ --files-only",
    ).decode("utf-8")
    return parse_snapshot_lsjson(raw)


@task(name="backup-cloud-status")
def backup_status(_c, max_age_hours=None, json_output=False):
    """Check freshness of the newest Yandex.Disk backup. Exits non-zero if stale.

    --max-age-hours N (default 26), --json-output for machine-readable result.
    """
    threshold = float(max_age_hours) if max_age_hours is not None else float(BACKUP_STALE_HOURS)
    snapshots = replica_list_snapshots()
    now = datetime.now(tz=UTC)
    verdict = check_backup_freshness(snapshots, now, threshold)
    frozen = check_identical_backup_sizes(snapshots)
    if json_output:
        print(json.dumps(verdict))
    else:
        print(format_backup_status_line(verdict))
        if frozen["status"] == "frozen":
            print(format_frozen_replica_line(frozen), file=sys.stderr)
        elif frozen["status"] == "single":
            print(format_single_backup_line(frozen), file=sys.stderr)
    if verdict["status"] != "ok" or frozen["status"] in ("frozen", "single"):
        sys.exit(1)

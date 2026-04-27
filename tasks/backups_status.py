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

from dinary.tools.backup_snapshots import (
    BACKUP_RCLONE_PATH,
    BACKUP_RCLONE_REMOTE,
    BACKUP_STALE_HOURS,
    check_backup_freshness,
    format_backup_status_line,
    parse_snapshot_lsjson,
)

from .ssh_utils import ssh_replica_capture_bytes


def replica_list_snapshots():
    """List Yadisk snapshots by asking VM2 over SSH.

    Used by :func:`backup_status` so the monitoring path reuses the
    already-configured ``yandex:`` remote on VM2. The laptop can
    then run freshness checks from cron without keeping its own
    Yandex WebDAV credentials.

    Shape/sort contract is inherited from
    :func:`dinary.tools.backup_snapshots.parse_snapshot_lsjson`.
    """
    raw = ssh_replica_capture_bytes(
        f"rclone lsjson {BACKUP_RCLONE_REMOTE}:{BACKUP_RCLONE_PATH}/ --files-only",
    ).decode("utf-8")
    return parse_snapshot_lsjson(raw)


@task(name="backup-cloud-status")
def backup_status(_c, max_age_hours=None, json_output=False):
    """Check freshness of the newest Yandex.Disk backup.

    Prints a one-line summary and exits 0 when the newest snapshot
    is within ``--max-age-hours`` (default :data:`BACKUP_STALE_HOURS`),
    non-zero otherwise.

    Flags:
        --max-age-hours N   Freshness threshold in hours.
        --json-output       Emit a JSON object instead of the human summary.
    """
    threshold = float(max_age_hours) if max_age_hours is not None else float(BACKUP_STALE_HOURS)
    snapshots = replica_list_snapshots()
    now = datetime.now(tz=UTC)
    verdict = check_backup_freshness(snapshots, now, threshold)
    if json_output:
        print(json.dumps(verdict))
    else:
        print(format_backup_status_line(verdict))
    if verdict["status"] != "ok":
        sys.exit(1)

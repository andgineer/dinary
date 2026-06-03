"""inv tasks for analytics.db backup and restore (local file and Yandex.Disk)."""

import json
import shlex
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from invoke import task

from tasks.backups.backup_retention import _make_pattern, list_snapshots, pick_keepers
from tasks.backups.backup_snapshots import (
    BACKUP_RCLONE_REMOTE,
    assert_local_binaries,
    pick_snapshot,
    print_snapshot_list,
)
from tasks.backups.backups_yandex import ensure_local_yandex_rclone_configured

_ANALYTICS_RCLONE_PATH = "Backup/dinary-analytics"
_ANALYTICS_PREFIX = "dinary-analytics-"
_ANALYTICS_SUFFIX = ".db.zst"
_ANALYTICS_PATTERN = _make_pattern(_ANALYTICS_PREFIX, _ANALYTICS_SUFFIX)

_RETENTION_DAILY = 7
_RETENTION_WEEKLY = 4
_RETENTION_MONTHLY = 12


def _list_yadisk_analytics_snapshots() -> list[tuple[str, int]]:
    raw = subprocess.check_output(
        [
            "rclone",
            "lsjson",
            f"{BACKUP_RCLONE_REMOTE}:{_ANALYTICS_RCLONE_PATH}/",
            "--files-only",
        ],
        text=True,
    )
    entries = json.loads(raw)
    result = []
    for entry in entries:
        name = entry.get("Name", "")
        if _ANALYTICS_PATTERN.match(name):
            result.append((name, int(entry.get("Size", 0))))
    result.sort(key=lambda x: x[0])
    return result


def _apply_yadisk_retention() -> None:
    remote = f"{BACKUP_RCLONE_REMOTE}:{_ANALYTICS_RCLONE_PATH}/"
    snaps = list_snapshots(remote, _ANALYTICS_PATTERN)
    keepers = pick_keepers(
        snaps,
        daily=_RETENTION_DAILY,
        weekly=_RETENTION_WEEKLY,
        monthly=_RETENTION_MONTHLY,
    )
    to_delete = [name for _, name in snaps if name not in keepers]
    for name in to_delete:
        subprocess.check_call(["rclone", "delete", remote + name])
    print(f"Retention: kept {len(keepers)}, deleted {len(to_delete)}")


def _do_backup(c, dest: Path) -> None:
    """Create a local .db.zst backup of analytics.db via the analytics package."""
    assert_local_binaries(["zstd"])
    c.run(
        f"uv run python -m dinary_analytics.backup backup --output {shlex.quote(str(dest))}",
    )


def _prompt_restore_confirmation(src: Path) -> None:
    print(
        f"\nAbout to overwrite data/analytics.db with {src.name}.\n"
        "The current data.mdb will be renamed data.mdb.before-restore-<UTC>.\n",
    )
    answer = input("Type 'yes' to proceed: ").strip()
    if answer != "yes":
        sys.stderr.write("Aborted.\n")
        sys.exit(1)


@task(
    name="backup-analytics",
    help={"output": "Output path for the .db.zst file (default: auto-named in CWD)."},
)
def backup_analytics(c, output=None):
    """Backup analytics.db to a local zstd-compressed file."""
    ts = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H%MZ")
    dest = Path(output) if output else Path(f"{_ANALYTICS_PREFIX}{ts}{_ANALYTICS_SUFFIX}")
    _do_backup(c, dest)


@task(name="backup-analytics-yadisk")
def backup_analytics_yadisk(c):
    """Backup analytics.db to Yandex.Disk and apply GFS retention."""
    assert_local_binaries(["zstd", "rclone"])
    ensure_local_yandex_rclone_configured()
    ts = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H%MZ")
    filename = f"{_ANALYTICS_PREFIX}{ts}{_ANALYTICS_SUFFIX}"
    remote = f"{BACKUP_RCLONE_REMOTE}:{_ANALYTICS_RCLONE_PATH}/{filename}"
    with tempfile.TemporaryDirectory() as workdir:
        local_archive = Path(workdir) / filename
        _do_backup(c, local_archive)
        c.run(f"rclone copyto {shlex.quote(str(local_archive))} {shlex.quote(remote)}")
        print(f"Uploaded → {remote}")
    _apply_yadisk_retention()


@task(
    name="restore-analytics",
    help={"file": "Path to .db.zst backup file.", "yes": "Skip confirmation prompt."},
)
def restore_analytics(c, file, yes=False):
    """Restore analytics.db from a local zstd-compressed backup file."""
    assert_local_binaries(["zstd"])
    src = Path(file)
    if not yes:
        _prompt_restore_confirmation(src)
    c.run(
        f"uv run python -m dinary_analytics.backup restore --file {shlex.quote(str(src))}",
    )


@task(
    name="restore-analytics-yadisk",
    help={
        "snapshot": "Snapshot date prefix or 'latest' (default: latest).",
        "list_only": "List available snapshots and exit.",
        "yes": "Skip confirmation prompt.",
    },
)
def restore_analytics_yadisk(c, snapshot="latest", list_only=False, yes=False):
    """Restore analytics.db from a Yandex.Disk snapshot."""
    assert_local_binaries(["zstd", "rclone"])
    ensure_local_yandex_rclone_configured()
    snapshots = _list_yadisk_analytics_snapshots()
    if not snapshots:
        sys.stderr.write(
            f"No analytics snapshots found at {BACKUP_RCLONE_REMOTE}:{_ANALYTICS_RCLONE_PATH}/\n",
        )
        sys.exit(1)
    if list_only:
        print_snapshot_list(snapshots)
        return
    picked = pick_snapshot(snapshots, snapshot)
    if picked is None:
        sys.stderr.write(f"No snapshot matches --snapshot={snapshot!r}.\n")
        print_snapshot_list(snapshots, stream=sys.stderr)
        sys.exit(1)
    if not yes:
        _prompt_restore_confirmation(Path(picked[0]))
    with tempfile.TemporaryDirectory() as workdir:
        archive = Path(workdir) / picked[0]
        remote_path = f"{BACKUP_RCLONE_REMOTE}:{_ANALYTICS_RCLONE_PATH}/{picked[0]}"
        c.run(f"rclone copyto {shlex.quote(remote_path)} {shlex.quote(str(archive))}")
        c.run(
            f"uv run python -m dinary_analytics.backup restore --file {shlex.quote(str(archive))}",
        )

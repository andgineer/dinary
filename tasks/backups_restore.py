"""Yandex.Disk → laptop restore pipeline.

Pulls a compressed snapshot from the operator-local ``yandex:`` rclone
remote, decompresses + integrity-checks, then atomically swaps it into
``data/dinary.db`` and resyncs the Litestream replica so VM2's WAL
position matches the restored DB.
"""

import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from invoke import task

from dinary.tools.backup_snapshots import (
    BACKUP_RCLONE_PATH,
    BACKUP_RCLONE_REMOTE,
    assert_local_binaries,
    parse_snapshot_lsjson,
    pick_snapshot,
    print_snapshot_list,
    sqlite_row_count,
)

from .backups_replica import replica_resync
from .env import _env, replica_host


def yadisk_list_snapshots():
    """Return ``[(filename, size_bytes), ...]`` of backups on Yandex.Disk.

    Uses ``rclone lsjson`` against the operator-local ``yandex:``
    remote (the one configured on the machine running
    :func:`restore_from_yadisk`). Shape/sort contract is inherited
    from :func:`dinary.tools.backup_snapshots.parse_snapshot_lsjson`.
    """
    raw = subprocess.check_output(
        ["rclone", "lsjson", f"{BACKUP_RCLONE_REMOTE}:{BACKUP_RCLONE_PATH}/", "--files-only"],
        text=True,
    )
    return parse_snapshot_lsjson(raw)


def _prompt_restore_confirmation(target_db, picked):
    """Interactive "type yes" gate before overwriting a non-empty DB.

    Shows row count + size + mtime of the file that will be replaced
    and the size of the incoming snapshot so the operator can sanity-
    check they are about to lose ~nothing (debug DB case) or a lot
    (prod case). Any input other than the literal ``yes`` aborts.

    Why not a simple y/n: ``y`` is a one-keypress accept and every
    heavy-destructive CLI tool I want to keep safe asks for a full
    word precisely so ``Enter`` cannot accidentally commit.
    """
    row_count = sqlite_row_count(target_db)
    size_kb = target_db.stat().st_size / 1024
    mtime = datetime.fromtimestamp(target_db.stat().st_mtime, tz=UTC).strftime(
        "%Y-%m-%d %H:%M UTC",
    )
    print(
        f"About to overwrite {target_db} ({row_count:,} expense rows, "
        f"{size_kb:,.1f} KB, mtime {mtime})\n"
        f"with snapshot {picked[0]} ({picked[1] / 1024:,.1f} KB compressed).\n"
        f"The previous file will be saved as "
        f"{target_db.name}.before-restore-<UTC-ISO>.\n"
        f"WARNING: stop the server before proceeding to avoid WAL corruption.",
    )
    answer = input("Type 'yes' to proceed: ").strip()
    if answer != "yes":
        sys.stderr.write("Aborted.\n")
        sys.exit(1)


def _download_and_verify(c, picked, workpath: Path) -> Path:
    """Download snapshot from Yadisk, decompress, integrity-check. Returns path to restored DB."""
    archive = workpath / picked[0]
    restored = workpath / "restored.db"
    remote_path = f"{BACKUP_RCLONE_REMOTE}:{BACKUP_RCLONE_PATH}/{picked[0]}"
    c.run(f"rclone copyto {shlex.quote(remote_path)} {shlex.quote(str(archive))}")
    c.run(f"zstd -q -d {shlex.quote(str(archive))} -o {shlex.quote(str(restored))}")
    check = subprocess.run(
        ["sqlite3", str(restored), "PRAGMA integrity_check"],
        capture_output=True,
        text=True,
        check=False,
    )
    if check.returncode != 0 or check.stdout.strip() != "ok":
        sys.stderr.write(
            f"integrity_check FAILED on {picked[0]}; "
            f"data/dinary.db NOT touched.\n"
            f"  stdout: {check.stdout.strip() or '(empty)'}\n"
            f"  stderr: {check.stderr.strip() or '(empty)'}\n",
        )
        sys.exit(1)
    return restored


@task(name="restore-cloud-backup")
def restore_from_yadisk(c, snapshot="latest", list_only=False, yes=False, no_resync=False):
    """Restore DB from Yandex.Disk snapshots written by the Litestream replica (VM2).

    The replica continuously pushes compressed SQLite snapshots to Yandex.Disk
    via ``rclone``.  This task downloads and restores from that same remote,
    making it the DR counterpart of the replica's backup job.

    **Run on the server** (``ssh ubuntu@dinary && cd ~/dinary``), not locally.
    Writes to ``./data/dinary.db`` relative to the cwd.

    After the restore, automatically resyncs the Litestream replica
    (``inv replica-resync``) so its WAL position matches the restored DB.
    Skip with ``--no-resync`` if ``DINARY_REPLICA_HOST`` is not configured
    or the replica is already stopped.

    Flags:
        --snapshot DATE   pick by date prefix (e.g. ``2026-04-22``). Default ``latest``.
        --list-only       enumerate snapshots and exit without writing.
        --yes             skip the "type yes to proceed" gate.
        --no-resync       skip automatic replica resync after restore.
    """
    assert_local_binaries(["rclone", "sqlite3", "zstd"])

    snapshots = yadisk_list_snapshots()
    if not snapshots:
        sys.stderr.write(
            f"No snapshots found at {BACKUP_RCLONE_REMOTE}:{BACKUP_RCLONE_PATH}/.\n",
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

    target_db = Path("data/dinary.db")
    if target_db.exists() and target_db.stat().st_size > 0 and not yes:
        _prompt_restore_confirmation(target_db, picked)

    with tempfile.TemporaryDirectory() as workdir:
        restored = _download_and_verify(c, picked, Path(workdir))

        target_db.parent.mkdir(parents=True, exist_ok=True)
        if target_db.exists():
            ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%MZ")
            preserved = target_db.with_name(f"dinary.db.before-restore-{ts}")
            target_db.rename(preserved)
            print(f"Previous data/dinary.db saved as data/{preserved.name}")

        for wal_file in (
            target_db.with_suffix(".db-wal"),
            target_db.with_suffix(".db-shm"),
        ):
            if wal_file.exists():
                wal_file.unlink()
                print(f"Removed stale WAL file {wal_file.name}")
        shutil.move(str(restored), str(target_db))

    print(f"Restored data/dinary.db from {picked[0]}")

    if no_resync:
        print("=== --no-resync set: skipping replica resync. ===")
        return

    if not _env().get("DINARY_REPLICA_HOST"):
        print("=== DINARY_REPLICA_HOST not set: skipping replica resync. ===")
        return
    print(f"=== Replica {replica_host()} detected — resyncing to match restored DB ===")
    replica_resync(c)

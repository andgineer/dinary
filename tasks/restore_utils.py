"""Shared helpers for restore-primary, restore-replica, and restore-cloud-backup."""

import sys
from datetime import UTC, datetime
from pathlib import Path

from dinary.tools.backup_snapshots import sqlite_row_count


def confirm_overwrite(target: Path, incoming_desc: str, yes: bool) -> None:
    """Print existing DB stats and prompt for confirmation. Exits non-zero on refusal."""
    row_count = sqlite_row_count(target)
    size_kb = target.stat().st_size / 1024
    mtime = datetime.fromtimestamp(target.stat().st_mtime, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    print(
        f"About to overwrite {target} ({row_count:,} expense rows, "
        f"{size_kb:,.1f} KB, mtime {mtime})\n"
        f"with {incoming_desc}.\n"
        f"Previous file will be saved as {target.name}.before-restore-<UTC-ISO>.\n"
        f"WARNING: stop the server before proceeding to avoid WAL corruption.",
    )
    if not yes:
        answer = input("Type 'yes' to proceed: ").strip()
        if answer != "yes":
            sys.stderr.write("Aborted.\n")
            sys.exit(1)


def apply_restore(db_bytes: bytes, target: Path) -> None:
    """Write db_bytes to target, preserving the old file and removing stale WAL."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%MZ")
        preserved = target.with_name(f"{target.name}.before-restore-{ts}")
        target.rename(preserved)
        print(f"Previous {target.name} saved as {preserved.name}")
    for wal_file in (target.with_suffix(".db-wal"), target.with_suffix(".db-shm")):
        if wal_file.exists():
            wal_file.unlink()
            print(f"Removed stale WAL file {wal_file.name}")
    target.write_bytes(db_bytes)

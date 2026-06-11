"""Database tasks: migrate, verify-db, restore-primary."""

import base64
import sqlite3
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from invoke import task

from tasks.backups.restore_utils import apply_restore, confirm_overwrite
from tasks.devtools.env import host
from tasks.ssh_utils import sqlite_backup_prologue, ssh_capture_bytes

_LOCAL_DB_PATH = Path("data/dinary.db")


@contextmanager
def open_local_db() -> Generator[sqlite3.Connection]:
    if not _LOCAL_DB_PATH.exists():
        print(f"No local DB at {_LOCAL_DB_PATH}", file=sys.stderr)
        sys.exit(1)
    con = sqlite3.connect(_LOCAL_DB_PATH)
    try:
        yield con
    finally:
        con.close()


@task(name="migrate")
def migrate(c):
    """Apply pending yoyo migrations to local data/dinary.db (local dev only).

    The server applies migrations automatically on start — no manual step needed there.
    """
    c.run(
        "uv run python -c 'from dinary.db import storage; "
        'storage.init_db(); print("Migrated data/dinary.db")\'',
    )


@task(name="seed-categories")
def seed_categories(c):
    """Seed/reconcile the category catalog from src/dinary/category_templates/ (local dev only).

    The server runs this automatically on every boot — manual entry point for
    ad-hoc reseed/reconcile without restarting the service.
    """
    c.run(
        "uv run python -c '"
        "from dinary.db import storage, category_seed; "
        "storage.init_db()\n"
        "with storage.connection() as con:\n"
        "    category_seed.bootstrap_categories(con)\n"
        'print("Seeded categories")\'',
    )


@task(name="verify-db")
def verify_db(c, remote=False):  # noqa: ARG001
    """Check DB structural integrity and foreign-key consistency.

    --remote runs against a prod snapshot over SSH (default: local data/dinary.db).
    Exits non-zero on any issue.
    """
    if remote:
        remote_cmd = (
            "set -e; "
            + sqlite_backup_prologue("dinary-verify-db")
            + 'sqlite3 "$SNAP" "PRAGMA integrity_check; PRAGMA foreign_key_check;"'
        )
        raw = ssh_capture_bytes(remote_cmd)
        output = raw.decode("utf-8", errors="replace")
    else:
        with open_local_db() as con:
            rows = con.execute("PRAGMA integrity_check").fetchall()
            rows.extend(con.execute("PRAGMA foreign_key_check").fetchall())
        output = "\n".join("|".join(str(col) for col in row) for row in rows)
    print(output, end="" if output.endswith("\n") else "\n")
    lines = [line for line in output.splitlines() if line.strip()]
    if lines != ["ok"]:
        print("=== verify-db FAILED ===", file=sys.stderr)
        sys.exit(1)
    print("=== verify-db OK ===")


@task(name="restore-primary")
def restore_primary(c, output=None, yes=False):  # noqa: ARG001
    """Download a live consistent snapshot from VM1 to data/dinary.db.

    Uses SQLite online-backup API — no service shutdown needed.
    Flags: -o PATH (default data/dinary.db), --yes to skip confirmation.
    """
    target = Path(output) if output else Path("data/dinary.db")
    if target.exists() and target.stat().st_size > 0:
        confirm_overwrite(target, "live snapshot from VM1", yes)
    remote_cmd = sqlite_backup_prologue("dinary-restore-primary") + 'cat "$SNAP"'
    b64 = base64.b64encode(remote_cmd.encode()).decode()
    db_bytes = subprocess.run(
        ["ssh", host(), f"echo {b64} | base64 -d | bash"],
        capture_output=True,
        check=True,
    ).stdout
    apply_restore(db_bytes, target)
    print(f"Restored {len(db_bytes) / 1024:.1f} KB → {target}")

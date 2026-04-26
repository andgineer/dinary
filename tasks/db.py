"""Database tasks: migrate, verify-db, backup."""

import base64
import sqlite3
import subprocess
import sys
from datetime import datetime as _dt
from pathlib import Path

from invoke import task

from .env import host
from .ssh_utils import sqlite_backup_prologue, ssh_capture_bytes, ssh_run


@task(name="migrate")
def migrate(c, remote=False):
    """Apply pending schema migrations to ``data/dinary.db``.

    Flags:
        --remote   Run against the production DB on the server over SSH.
                   Default runs locally against ``data/dinary.db``.
    """
    if not remote:
        c.run(
            "uv run python -c 'from dinary.services import ledger_repo; "
            'ledger_repo.init_db(); print("Migrated data/dinary.db")\'',
        )
        return
    ssh_run(
        c,
        "cd ~/dinary && source ~/.local/bin/env && "
        "uv run python -c 'from dinary.services import ledger_repo; "
        'ledger_repo.init_db(); print("Migrated data/dinary.db")\'',
    )


@task(name="verify-db")
def verify_db(c, remote=False):  # noqa: ARG001
    """Check DB structural integrity and foreign key consistency.

    Both pragmas are read-only and cheap for a DB on the order of a
    few hundred MB. ``integrity_check`` walks every btree page and
    reports structural damage (torn pages, index/table mismatches,
    orphan freelist entries); ``foreign_key_check`` lists every row
    that violates a declared FK. A healthy DB prints ``ok`` for the
    first and zero rows for the second.

    Flags:
        --remote   run against a ``/tmp`` snapshot of the prod DB
                   over SSH. Default runs locally against
                   ``data/dinary.db``.

    Exits non-zero when ``integrity_check`` prints anything other
    than ``ok`` or when ``foreign_key_check`` reports at least one
    offending row.
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
        db_path = Path("data/dinary.db")
        if not db_path.exists():
            print(
                f"No local DB at {db_path}; run `inv dev` or `inv backup` first.",
                file=sys.stderr,
            )
            sys.exit(1)
        con = sqlite3.connect(db_path)
        try:
            rows = con.execute("PRAGMA integrity_check").fetchall()
            rows.extend(con.execute("PRAGMA foreign_key_check").fetchall())
        finally:
            con.close()
        output = "\n".join("|".join(str(col) for col in row) for row in rows)
    print(output, end="" if output.endswith("\n") else "\n")
    lines = [line for line in output.splitlines() if line.strip()]
    if lines != ["ok"]:
        print("=== verify-db FAILED ===", file=sys.stderr)
        sys.exit(1)
    print("=== verify-db OK ===")


@task
def backup(c):  # noqa: ARG001
    """Take a consistent SQLite snapshot on the server and download it.

    Under SQLite WAL a raw ``scp data/dinary.db`` from a live server
    would copy a stale main file and miss whatever committed pages
    only live in the WAL yet — the resulting download would look
    valid on open (SQLite replays the WAL, if present) but silently
    lose the tail of the write history.

    Instead we invoke ``sqlite3 "$DB" ".backup $SNAP"`` on the server,
    which uses SQLite's online-backup API and captures a
    transactionally consistent snapshot while the service keeps
    writing. The snapshot is written into ``/tmp`` on the server,
    ``scp``'d home, and torn down via ``trap`` on exit so a failure
    never leaks a multi-hundred-MB file into ``/tmp``.

    Output lands in ``~/Library/dinary/<ts>/dinary.db`` — matching
    the default layout so operators can copy the file straight into
    ``data/`` and point ``inv dev`` at it.
    """
    dest = Path.home() / "Library" / "dinary"
    ts = _dt.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = dest / ts
    backup_dir.mkdir(parents=True, exist_ok=True)
    deploy_host = host()
    remote_cmd = sqlite_backup_prologue("dinary-backup") + 'cat "$SNAP"'
    b64 = base64.b64encode(remote_cmd.encode()).decode()
    local_db = backup_dir / "dinary.db"
    with local_db.open("wb") as fh:
        subprocess.run(
            ["ssh", deploy_host, f"echo {b64} | base64 -d | bash"],
            stdout=fh,
            check=True,
        )
    print(f"Backed up to {local_db}")

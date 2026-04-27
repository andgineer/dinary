"""Server-facing tasks: status, logs, restart, SSH sessions, healthcheck."""

import sqlite3
import sys
from datetime import date as _date
from datetime import timedelta as _timedelta
from pathlib import Path

from invoke import task

from .constants import REMOTE_LITESTREAM_CONFIG_PATH, REPLICA_DB_NAME, REPLICA_LITESTREAM_DIR
from .env import _env, host, replica_host, tunnel
from .ssh_utils import (
    sqlite_backup_prologue,
    ssh_capture,
    ssh_capture_bytes,
    ssh_replica_capture_bytes,
    ssh_run,
    ssh_sudo,
)


@task(name="restart-server")
def restart_server(c):
    """Restart the dinary systemd service on the server."""
    ssh_sudo(c, "systemctl restart dinary")
    print("=== Restarted. Checking health... ===")
    ssh_run(c, "sleep 5 && curl -s http://localhost:8000/api/health")


@task
def logs(c, follow=False, lines=100, remote=False):
    """Show dinary service logs.

    Flags:
        --remote      Fetch logs from the production server over SSH.
                      Default runs locally (prints a hint since local dev
                      logs appear in the ``inv dev`` terminal).
        -f            Follow log output (remote only).
        -l N          Number of lines to show (default 100, remote only).
    """
    if not remote:
        print("Local dev logs appear in the terminal when running `inv dev`.")
        return
    flag = "-f" if follow else f"-n {lines} --no-pager"
    c.run(f"ssh {host()} 'sudo journalctl -u dinary {flag}'")


@task
def status(c, remote=False):
    """Show dinary service status and Litestream replicator state.

    Flags:
        --remote   Check the production server over SSH.
                   Default checks local dev server at localhost:8000.
    """
    if not remote:
        c.run("curl -sf http://localhost:8000/api/health || echo 'Server not responding'")
        return
    tun = tunnel()
    ssh_sudo(c, "systemctl status dinary --no-pager")
    if tun == "tailscale":
        ssh_run(c, "tailscale serve status")
    elif tun == "cloudflare":
        ssh_sudo(c, "systemctl status cloudflared --no-pager")
    ssh_run(c, "systemctl status litestream --no-pager || true")
    ssh_run(c, "sudo journalctl -u litestream -n 30 --no-pager || true")
    ssh_run(c, f"litestream databases -config {REMOTE_LITESTREAM_CONFIG_PATH} || true")


@task
def ssh(c):
    """Open SSH session to the server."""
    c.run(f"ssh {host()}", pty=True)


@task(name="ssh-replica")
def ssh_replica(c):
    """Open SSH session to the replica (VM2)."""
    c.run(f"ssh {replica_host()}", pty=True)


def _healthcheck_query_lines(c, remote: bool, rate_sql: str, sheet_sql: str) -> list[str]:  # noqa: ARG001
    if remote:
        raw = ssh_capture_bytes(
            sqlite_backup_prologue("dinary-healthcheck")
            + f'sqlite3 "$SNAP" "{rate_sql}; {sheet_sql}"',
        )
        return raw.decode("utf-8", errors="replace").strip().splitlines()
    db_path = Path("data/dinary.db")
    if not db_path.exists():
        print(f"No local DB at {db_path}", file=sys.stderr)
        sys.exit(1)
    con = sqlite3.connect(db_path)
    try:
        return [str(con.execute(sql).fetchone()[0]) for sql in [rate_sql, sheet_sql]]
    finally:
        con.close()


def _healthcheck_sheet_log(lines: list[str]) -> None:
    expense_line = lines[1].strip() if len(lines) > 1 else ""
    if not expense_line:
        print("OK: no expenses in DB, nothing to check")
        return
    parts = expense_line.split("|")
    expense_id = parts[0]
    job_status = parts[1] if len(parts) > 1 else ""
    if job_status == "poisoned":
        print(
            f"FAIL: last expense (id={expense_id}) sheet logging is poisoned",
            file=sys.stderr,
        )
        sys.exit(1)
    if job_status in ("pending", "in_progress"):
        print(f"OK: last expense (id={expense_id}) sheet logging {job_status} (in queue)")
    elif not job_status:
        print(f"OK: last expense (id={expense_id}) logged to sheet")
    else:
        print(
            f"FAIL: last expense (id={expense_id}) unexpected status: {job_status}",
            file=sys.stderr,
        )
        sys.exit(1)


def _build_replica_page_count_script() -> str:
    """Restore latest LTX snapshot on VM2 and output its page_count integer."""
    replica_path = f"{REPLICA_LITESTREAM_DIR}/{REPLICA_DB_NAME}"
    return (
        "set -euo pipefail\n"
        "WORKDIR=$(mktemp -d)\n"
        "trap 'rm -rf \"$WORKDIR\"' EXIT\n"
        'SNAP="$WORKDIR/hc.db"\n'
        'CFG="$WORKDIR/ls.yml"\n'
        'cat > "$CFG" <<LSYAML\n'
        "dbs:\n"
        "  - path: $SNAP\n"
        "    replicas:\n"
        "      - type: file\n"
        f"        path: {replica_path}\n"
        "LSYAML\n"
        'litestream restore -config "$CFG" "$SNAP" >&2\n'
        'sqlite3 "$SNAP" "PRAGMA page_count;"\n'
    )


def _healthcheck_replica_page_count() -> None:
    """Compare VM1 and VM2 SQLite page_count; exit non-zero if they diverge.

    A diverged page count is the clearest symptom of a missed
    ``inv replica-resync`` after a restore.
    If ``DINARY_REPLICA_HOST`` is not configured the check is skipped.
    """
    if not _env().get("DINARY_REPLICA_HOST"):
        return
    primary_raw = ssh_capture_bytes(
        sqlite_backup_prologue("dinary-hc-primary") + 'sqlite3 "$SNAP" "PRAGMA page_count;"',
    )
    replica_raw = ssh_replica_capture_bytes(_build_replica_page_count_script())
    primary_pages = primary_raw.decode("utf-8", errors="replace").strip()
    replica_pages = replica_raw.decode("utf-8", errors="replace").strip()
    if primary_pages != replica_pages:
        print(
            f"FAIL: replica page_count ({replica_pages}) != primary ({primary_pages}). "
            "Run `inv replica-resync` to fix.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"OK: replica page_count matches primary ({primary_pages})")


@task(name="healthcheck")
def healthcheck(c, remote=False):  # noqa: ARG001
    """Check the health of the dinary server: systemd services, background tasks, and DB state.

    Verifies:
      1. (remote only) systemd services are active: dinary, litestream, tunnel.
      2. (remote only) replica DB page count matches primary (WAL-sync check).
      3. Exchange rate for yesterday exists in the cache (rate prefetch task).
      4. Last expense has been logged to Google Sheets (sheet logging task),
         when sheet logging is enabled.

    Flags:
        --remote   check the production server over SSH.
                   Default runs locally against ``data/dinary.db``.

    Exits non-zero on the first failed check and prints what is broken.
    """
    if remote:
        tun = tunnel()
        services = ["dinary", "litestream"]
        if tun == "cloudflare":
            services.append("cloudflared")
        for svc in services:
            state = ssh_capture(c, f"systemctl is-active {svc} || true").strip()
            if state != "active":
                print(f"FAIL: service {svc} is {state!r}", file=sys.stderr)
                sys.exit(1)
            print(f"OK: service {svc} active")
        _healthcheck_replica_page_count()

    yesterday = (_date.today() - _timedelta(days=1)).isoformat()
    rate_sql = f"SELECT count(*) FROM exchange_rates WHERE date = '{yesterday}'"  # noqa: S608
    sheet_sql = (
        "SELECT COALESCE("
        "(SELECT e.id || '|' || COALESCE(slj.status, '')"
        " FROM expenses e"
        " LEFT JOIN sheet_logging_jobs slj ON slj.expense_id = e.id"
        " ORDER BY e.id DESC LIMIT 1),"
        " '')"
    )
    lines = _healthcheck_query_lines(c, remote, rate_sql, sheet_sql)

    rate_count = int(lines[0]) if lines else 0
    if rate_count == 0:
        print(f"FAIL: no exchange rate cached for {yesterday}", file=sys.stderr)
        sys.exit(1)
    print(f"OK: exchange rate for {yesterday} cached")

    if not _env().get("DINARY_SHEET_LOGGING_SPREADSHEET"):
        print("OK: sheet logging not configured, skipping")
        return

    _healthcheck_sheet_log(lines)

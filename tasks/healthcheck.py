"""Healthcheck task and helpers — moved from server.py."""

import sys
from datetime import date as _date
from datetime import timedelta as _timedelta

from invoke import task

from .constants import REPLICA_DB_NAME, REPLICA_LITESTREAM_DIR
from .db import open_local_db
from .env import _env, tunnel
from .ssh_utils import (
    sqlite_backup_prologue,
    ssh_capture,
    ssh_capture_bytes,
    ssh_replica_capture_bytes,
)


def _healthcheck_run_queries(c, remote: bool, **queries: str) -> dict[str, str]:  # noqa: ARG001
    names = list(queries)
    sqls = list(queries.values())
    if remote:
        combined = "; ".join(sqls)
        raw = ssh_capture_bytes(
            sqlite_backup_prologue("dinary-healthcheck") + f'sqlite3 "$SNAP" "{combined}"',
        )
        values = raw.decode("utf-8", errors="replace").strip().splitlines()
    else:
        with open_local_db() as con:
            values = [str(con.execute(sql).fetchone()[0]) for sql in sqls]
    return dict(zip(names, values, strict=False))


def _healthcheck_sheet_log(results: dict[str, str]) -> None:
    expense_line = results.get("sheet", "").strip()
    if not expense_line:
        print("OK: no expenses in DB, nothing to check")
        return
    expense_id, job_status = (expense_line.split("|", 1) + [""])[:2]
    if job_status == "poisoned":
        print(
            f"FAIL: last expense (id={expense_id}) sheet logging failed after all retries,"
            " needs manual fix",
            file=sys.stderr,
        )
        sys.exit(1)
    if job_status in ("pending", "in_progress"):
        print(f"OK: last expense (id={expense_id}) sheet logging in progress")
    elif not job_status:
        print(f"OK: last expense (id={expense_id}) logged to sheet")
    else:
        print(
            f"FAIL: last expense (id={expense_id}) unexpected sheet logging status: {job_status!r}",
            file=sys.stderr,
        )
        sys.exit(1)


def _fmt_amount(value: str) -> str:
    try:
        f = float(value)
        return str(int(f)) if f == int(f) else f"{f:.2f}"
    except (ValueError, OverflowError):
        return value


def _healthcheck_last_expense_info(results: dict[str, str]) -> None:
    detail = results.get("last_expense", "").strip()
    prev_total = results.get("prev_day_total", "").strip()
    if detail:
        amount_str, currency, category = (detail.split("|", 2) + ["", ""])[:3]
        print(f"OK: last expense {_fmt_amount(amount_str)} {currency} ({category})")
    if prev_total:
        totals = ", ".join(
            f"{_fmt_amount(total)} {cur}"
            for entry in prev_total.split(",")
            for cur, total in [entry.split(":", 1)]
        )
        print(f"OK: yesterday total {totals}")


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
    """Compare VM1 and VM2 SQLite page_count; exit non-zero if they diverge."""
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


def _healthcheck_receipt_llm(results: dict[str, str]) -> bool:
    """Print LLM provider health lines. Returns True if any failure was found."""
    switch = results.get("llm_switch", "").strip()
    exhausted = results.get("llm_exhausted", "").strip()
    count = results.get("llm_switch_count", "0").strip()

    if count != "0":
        print(f"OK: LLM provider switches since last start: {count}")

    failed = False
    if switch:
        print(f"FAIL: LLM provider switched — {switch}", file=sys.stderr)
        failed = True
    if exhausted:
        print(f"FAIL: All LLM providers exhausted — {exhausted}", file=sys.stderr)
        failed = True
    return failed


def _healthcheck_receipt_fetch(results: dict[str, str]) -> bool:
    """Print receipt-fetch health lines. Returns True if any failure was found."""
    fallback = results.get("receipt_fallback", "").strip()
    count = results.get("receipt_fallback_count", "0").strip()

    if count != "0":
        print(f"OK: /specifications fallback uses since last start: {count}")

    if fallback:
        print(f"FAIL: /specifications fallback used — {fallback}", file=sys.stderr)
        return True
    return False


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
    results = _healthcheck_run_queries(
        c,
        remote,
        rate=f"SELECT count(*) FROM exchange_rates WHERE date = '{yesterday}'",  # noqa: S608
        llm_switch=(
            "SELECT COALESCE((SELECT value FROM app_metadata"
            " WHERE key = 'llm_provider_switch_last'), '')"
        ),
        llm_exhausted=(
            "SELECT COALESCE((SELECT value FROM app_metadata"
            " WHERE key = 'llm_all_exhausted_last'), '')"
        ),
        llm_switch_count=(
            "SELECT COALESCE((SELECT value FROM app_metadata"
            " WHERE key = 'llm_provider_switch_count'), '0')"
        ),
        receipt_fallback=(
            "SELECT COALESCE((SELECT value FROM app_metadata"
            " WHERE key = 'receipt_fetch_fallback_last'), '')"
        ),
        receipt_fallback_count=(
            "SELECT COALESCE((SELECT value FROM app_metadata"
            " WHERE key = 'receipt_fetch_fallback_count'), '0')"
        ),
        sheet=(
            "SELECT COALESCE("
            "(SELECT e.id || '|' || COALESCE(slj.status, '')"
            " FROM expenses e"
            " LEFT JOIN sheet_logging_jobs slj ON slj.expense_id = e.id"
            " ORDER BY e.id DESC LIMIT 1),"
            " '')"
        ),
        last_expense=(
            "SELECT COALESCE("
            "(SELECT CAST(e.amount_original AS TEXT) || '|' || e.currency_original || '|' || c.name"
            " FROM expenses e"
            " JOIN categories c ON c.id = e.category_id"
            " ORDER BY e.id DESC LIMIT 1),"
            " '')"
        ),
        prev_day_total=(
            f"SELECT COALESCE(GROUP_CONCAT(currency_original || ':' || total, ','), '')"  # noqa: S608
            f" FROM (SELECT currency_original, SUM(amount_original) AS total"
            f" FROM expenses WHERE DATE(datetime) = '{yesterday}'"
            f" GROUP BY currency_original)"
        ),
    )

    rate_count = int(results.get("rate", "0") or "0")
    if rate_count == 0:
        print(f"FAIL: no exchange rate cached for {yesterday}", file=sys.stderr)
        sys.exit(1)
    print(f"OK: exchange rate for {yesterday} cached")

    if not _env().get("DINARY_SHEET_LOGGING_SPREADSHEET"):
        print("OK: sheet logging not configured, skipping")
    else:
        _healthcheck_sheet_log(results)

    _healthcheck_last_expense_info(results)
    llm_failed = _healthcheck_receipt_llm(results)
    fetch_failed = _healthcheck_receipt_fetch(results)
    if llm_failed or fetch_failed:
        sys.exit(1)

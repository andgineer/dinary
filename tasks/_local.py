"""Local development tasks: version, test, dev, build-static, backup, verify-db, healthcheck."""

import base64
import shutil
import sqlite3
import subprocess
import sys
from datetime import date as _date
from datetime import datetime as _dt
from datetime import timedelta as _timedelta
from pathlib import Path

from invoke import task

from dinary.__about__ import __version__

from ._common import (
    _env,
    _host,
    _sqlite_backup_to_tmp_snapshot_prologue,
    _ssh_capture_bytes,
)


@task
def version(_c):
    """Show the current version."""
    with open("src/dinary/__about__.py") as f:
        version_line = f.readline()
        version_num = version_line.split('"')[1]
        print(version_num)
        return version_num


def ver_task_factory(version_type: str):
    @task
    def ver(c):
        """Bump the version."""
        c.run(f"./scripts/verup.sh {version_type}")

    return ver


@task
def reqs(c):
    """Upgrade requirements including pre-commit."""
    c.run("pre-commit autoupdate")
    c.run("uv lock --upgrade")


def docs_task_factory(language: str):
    @task
    def docs(c):
        """Docs preview for the language specified."""
        c.run("open -a 'Google Chrome' http://127.0.0.1:8000/dinary/")
        c.run(f"scripts/build-docs.sh --copy-assets {language}")
        c.run("mkdocs serve -f docs/_mkdocs.yml")

    return docs


@task
def uv(c):
    """Install or upgrade uv."""
    c.run("curl -LsSf https://astral.sh/uv/install.sh | sh")


@task
def test(c):
    """Run all tests (Python + JavaScript) with Allure results.

    Both suites always run (so npm test is not skipped on a pytest
    failure), but the task exits non-zero if either failed so CI can
    distinguish "all green" from "partial green".
    """
    c.run("rm -rf allure-results")
    py_result = c.run("uv run pytest tests/ -v --alluredir=allure-results", warn=True)
    js_result = c.run("npm test", warn=True)
    failed = []
    if py_result is not None and py_result.exited != 0:
        failed.append(f"pytest (exit {py_result.exited})")
    if js_result is not None and js_result.exited != 0:
        failed.append(f"npm test (exit {js_result.exited})")
    if failed:
        print(f"Test failures: {', '.join(failed)}")
        sys.exit(1)


@task
def pre(c):
    """Run pre-commit checks."""
    c.run("pre-commit run --verbose --all-files")


@task(
    help={
        "port": "TCP port to listen on (default 8000).",
        "sheet-logging": (
            "Opt back into Google-Sheets logging. OFF by default so debug "
            "expenses don't leak to the prod logging spreadsheet."
        ),
        "reset": (
            "Wipe ``data/dinary.db`` (plus its WAL / SHM sidecars) before "
            "starting, then re-seed the catalog from the hardcoded "
            "taxonomy (groups, categories, events, tags) so PWA dropdowns "
            "have rows. Best-effort kills any lingering local uvicorn "
            "process holding the DB. Non-destructive if no DB exists yet."
        ),
    },
)
def dev(c, port=8000, sheet_logging=False, reset=False):
    """Run the FastAPI server locally with auto-reload for PWA debugging.

    - Listens on http://127.0.0.1:<port> — open that URL in your
      browser instead of pushing to Oracle Cloud on every iteration.
    - ``uvicorn --reload`` watches ``src/`` and re-imports on Python
      changes. Static files (``static/``) are served straight from
      disk on every request, so editing ``static/css/style.css`` or
      ``static/js/app.js`` only needs a browser refresh — no server
      restart, no rebuild.
    - Sheet-logging is **disabled** by default
      (``DINARY_SHEET_LOGGING_SPREADSHEET=`` overrides the value
      from ``.deploy/.env``), so test expenses you create in dev
      do NOT show up in the prod Google Sheet. Pass
      ``--sheet-logging`` to opt in (rare; for debugging the drain
      loop itself).
    - Uses ``data/dinary.db`` as the local DB (SQLite in WAL mode).
      Schema migrations run automatically on server startup via the
      FastAPI lifespan (``_lifespan -> ledger_repo.init_db``), so
      editing a migration and restarting ``inv dev`` is enough — no
      separate ``migrate`` step. For a clean slate use ``--reset``.
    - To work against real prod data: run ``inv backup`` first to
      fetch a consistent snapshot into ``~/Library/dinary/<ts>/``,
      then copy the resulting ``dinary.db`` into ``data/``.

    PWA caching tip: once the service worker is registered the
    browser will serve cached assets even after you edit them. In
    Chrome DevTools open ``Application -> Service Workers`` and
    tick ``Update on reload`` (or ``Bypass for network``) for fast
    iteration. Hard-refresh (Cmd-Shift-R) also bypasses the SW
    for that one navigation.
    """
    if reset:
        subprocess.run(
            ["pkill", "-f", "uvicorn dinary"],
            check=False,
        )
        for fn in (
            "data/dinary.db",
            "data/dinary.db-wal",
            "data/dinary.db-shm",
        ):
            p = Path(fn)
            if p.exists():
                p.unlink()
                print(f"Removed {p}")
        c.run(
            "uv run python -c 'from dinary.services.seed_config "
            "import bootstrap_catalog; import json; "
            "print(json.dumps(bootstrap_catalog()))'",
        )

    overrides = []
    if not sheet_logging:
        overrides += [
            "DINARY_SHEET_LOGGING_SPREADSHEET=",
            "DINARY_SHEET_LOGGING_DRAIN_INTERVAL_SEC=0",
        ]
    prefix = (" ".join(overrides) + " ") if overrides else ""
    cmd = (
        f"{prefix}uv run uvicorn dinary.main:app "
        f"--reload --reload-dir src "
        f"--host 127.0.0.1 --port {port}"
    )
    c.run(cmd, pty=True)


@task(name="build-static")
def build_static(c):  # noqa: ARG001
    """Replace __VERSION__ in static/ files, write to _static/."""
    src = Path("static")
    dst = Path("_static")
    data = Path("data")

    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

    for filepath in [dst / "js" / "app.js", dst / "sw.js"]:
        text = filepath.read_text()
        filepath.write_text(text.replace("__VERSION__", __version__))

    data.mkdir(exist_ok=True)
    (data / ".deployed_version").write_text(__version__)
    print(f"Built _static/ with version {__version__}")


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
    host = _host()
    remote_cmd = _sqlite_backup_to_tmp_snapshot_prologue("dinary-backup") + 'cat "$SNAP"'
    b64 = base64.b64encode(remote_cmd.encode()).decode()
    local_db = backup_dir / "dinary.db"
    with local_db.open("wb") as fh:
        subprocess.run(
            ["ssh", host, f"echo {b64} | base64 -d | bash"],
            stdout=fh,
            check=True,
        )
    print(f"Backed up to {local_db}")


@task(name="verify-db")
def verify_db(c, remote=False):  # noqa: ARG001
    """Run SQLite's ``PRAGMA integrity_check`` + ``PRAGMA foreign_key_check``.

    Both pragmas are read-only and cheap for a DB on the order of a
    few hundred MB. ``integrity_check`` walks every btree page and
    reports structural damage (torn pages, index/table mismatches,
    orphan freelist entries); ``foreign_key_check`` lists every row
    that violates a declared FK. A healthy DB prints ``ok`` for the
    first and zero rows for the second. This is the
    ``.plans/storage-migration.md`` verification gate for the post-
    migration rollout: a silent corruption that slipped past
    ``inv verify-bootstrap-import-all`` would still be caught here.

    Flags:
        --remote   run against a ``/tmp`` snapshot of the prod DB
                   over SSH (same snapshot wrapper as
                   ``inv report-*``). Default runs locally against
                   ``data/dinary.db``.

    Exits non-zero when ``integrity_check`` prints anything other
    than ``ok`` or when ``foreign_key_check`` reports at least one
    offending row, so CI / coordinated-reset flows can fail loud.
    """
    if remote:
        remote_cmd = (
            _sqlite_backup_to_tmp_snapshot_prologue("dinary-verify-db")
            + 'sqlite3 "$SNAP" "PRAGMA integrity_check; PRAGMA foreign_key_check;"'
        )
        raw = _ssh_capture_bytes(remote_cmd)
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


@task(name="healthcheck")
def healthcheck(c, remote=False):  # noqa: ARG001
    """Check that background services are healthy.

    Verifies:
      1. Exchange rate for yesterday exists in the cache (rate prefetch task).
      2. Last expense has been logged to Google Sheets (sheet logging task),
         when sheet logging is enabled.

    Flags:
        --remote   check the production DB over SSH (snapshot).
                   Default runs locally against ``data/dinary.db``.

    Exits non-zero on the first failed check and prints what is broken.
    """
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
    sheet_logging_enabled = bool(_env().get("DINARY_SHEET_LOGGING_SPREADSHEET"))

    if remote:
        raw = _ssh_capture_bytes(
            _sqlite_backup_to_tmp_snapshot_prologue("dinary-healthcheck")
            + f'sqlite3 "$SNAP" "{rate_sql}; {sheet_sql}"',
        )
        lines = raw.decode("utf-8", errors="replace").strip().splitlines()
    else:
        db_path = Path("data/dinary.db")
        if not db_path.exists():
            print(f"No local DB at {db_path}", file=sys.stderr)
            sys.exit(1)
        con = sqlite3.connect(db_path)
        try:
            lines = [str(con.execute(sql).fetchone()[0]) for sql in [rate_sql, sheet_sql]]
        finally:
            con.close()

    rate_count = int(lines[0]) if lines else 0
    if rate_count == 0:
        print(f"FAIL: no exchange rate cached for {yesterday}", file=sys.stderr)
        sys.exit(1)
    print(f"OK: exchange rate for {yesterday} cached")

    if not sheet_logging_enabled:
        print("OK: sheet logging not configured, skipping")
        return

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

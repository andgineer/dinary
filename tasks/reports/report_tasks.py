"""Report and SQL query tasks."""

import json as _json
import shlex
import subprocess
import sys
from pathlib import Path

from invoke import task

import tasks.sql as sql_module
from tasks.reports import expenses as expenses_report
from tasks.reports import income as income_report
from tasks.reports.report_helpers import extract_format_flags, extract_year_month
from tasks.ssh_utils import remote_snapshot_cmd, ssh_capture_bytes


def _run_report_module(c, module: str, flags: list[str], *, remote: bool) -> None:
    """Dispatch a ``tasks.reports.<module>`` run locally or over SSH.

    Both modes follow the same shape: fetch rows → render locally.

    * Local: ``uv run python -m tasks.reports.<module> <flags>`` —
      a single process fetches from ``data/dinary.db`` and renders.
    * Remote: the same module runs on the server in ``--json`` mode
      via the SSH snapshot wrapper (see :func:`_remote_snapshot_cmd`
      for why a SQLite snapshot is used), the JSON bytes come back
      through :func:`_ssh_capture_bytes`, and the module's
      ``render`` runs on the local terminal.

    JSON is the wire format so stdout over SSH only ever carries
    structured data: a single end-of-stream UTF-8 decode on the
    client keeps Cyrillic and box-drawing glyphs intact regardless
    of how the SSH transport chunks the stream. The local side then
    picks any renderer (rich, csv, or raw JSON passthrough).
    """
    if not remote:
        cmd = f"uv run python -m tasks.reports.{module}"
        if flags:
            cmd = f"{cmd} {' '.join(flags)}"
        c.run(cmd)
        return

    as_csv, as_json, filter_flags = extract_format_flags(flags)

    remote_flags = [*filter_flags, "--json"]
    raw = ssh_capture_bytes(remote_snapshot_cmd(f"tasks.reports.{module}", remote_flags))

    if as_json:
        sys.stdout.buffer.write(raw)
        return

    payload = _json.loads(raw.decode("utf-8"))

    if module == "income":
        income_rows = income_report.rows_from_json(payload)
        income_report.render(income_rows, as_csv=as_csv, stream=sys.stdout)
    elif module == "expenses":
        expense_rows = expenses_report.rows_from_json(payload)
        year, month = extract_year_month(filter_flags)
        expenses_report.render(
            expense_rows,
            year=year,
            month=month,
            as_csv=as_csv,
            stream=sys.stdout,
        )
    else:
        msg = f"unknown report module: {module!r}"
        raise ValueError(msg)


@task(name="report-expenses")
def report_expenses(c, year="", month="", csv=False, remote=False):  # noqa: A002
    """Show expenses by (category, event, tags). Flags: --year, --month, --csv, --remote."""
    if year and month:
        print("--year and --month are mutually exclusive", file=sys.stderr)
        sys.exit(1)

    flags: list[str] = []
    if year:
        flags.extend(["--year", str(int(year))])
    if month:
        flags.extend(["--month", shlex.quote(month)])
    if csv:
        flags.append("--csv")
    _run_report_module(c, "expenses", flags, remote=remote)


@task(name="report-income")
def report_income(c, csv=False, remote=False):  # noqa: A002
    """Show income by year. Flags: --csv, --remote."""
    flags: list[str] = []
    if csv:
        flags.append("--csv")
    _run_report_module(c, "income", flags, remote=remote)


def _run_local_sql(c, sql_text: str, csv: bool, json_mode: bool, write: bool) -> None:
    local_flags = ["--query", sql_text]
    if csv:
        local_flags.append("--csv")
    elif json_mode:
        local_flags.append("--json")
    if write:
        local_flags.append("--write")
    c.run(f"uv run python -m tasks.sql {shlex.join(local_flags)}")


def _run_remote_sql(sql_text: str, csv: bool, json_mode: bool) -> None:
    remote_flags = ["--query", sql_text, "--json"]
    try:
        raw = ssh_capture_bytes(remote_snapshot_cmd("tasks.sql", remote_flags))
    except subprocess.CalledProcessError:
        sys.exit(1)
    if json_mode:
        sys.stdout.buffer.write(raw)
        return
    payload = _json.loads(raw.decode("utf-8"))
    columns, rows = sql_module.rows_from_json(payload)
    if csv:
        sql_module.render_csv(columns, rows, stream=sys.stdout)
    else:
        sql_module.render_rich(columns, rows, stream=sys.stdout)


@task(
    name="sql",
    help={
        "query": "SQL query string (mutex with --file).",
        "file": "Read SQL from file at this path (mutex with --query).",
        "csv": "Emit CSV to stdout instead of a rich table.",
        "json": ("Emit JSON envelope {columns, rows, row_count} to stdout. Mutex with --csv."),
        "write": (
            "Open the DB read-write so UPDATE/DELETE/INSERT can run. "
            "Off by default; forbidden with --remote."
        ),
        "remote": (
            "Run against a /tmp snapshot of the prod DB over SSH instead of local data/dinary.db."
        ),
    },
)
def sql_query(c, query="", file="", csv=False, json=False, write=False, remote=False):  # noqa: A002
    """Run a SQL query against data/dinary.db (read-only by default).

    Examples:
        inv sql -q "SELECT * FROM app_metadata ORDER BY key"
        inv sql -q "DELETE FROM expenses WHERE id = 999" --write
        inv sql -f scripts/summary.sql --csv > out.csv
        inv sql -q "SELECT * FROM app_metadata" --remote

    --write enables mutations (rejected with --remote).
    See https://andgineer.github.io/dinary/operations#reporting-and-data-access
    """

    if csv and json:
        raise SystemExit("--csv and --json are mutually exclusive")
    if bool(query) == bool(file):
        raise SystemExit("exactly one of --query / --file is required")
    if write and remote:
        raise SystemExit(
            "--write is not allowed with --remote: the remote runs against a "
            "/tmp snapshot that is torn down on exit, so any mutations would "
            "be silently discarded. SSH to the host and run `inv sql --write` "
            "there, or write a proper migration.",
        )

    sql_text = Path(file).read_text(encoding="utf-8") if file else query
    if not remote:
        _run_local_sql(c, sql_text, csv, json, write)
    else:
        _run_remote_sql(sql_text, csv, json)

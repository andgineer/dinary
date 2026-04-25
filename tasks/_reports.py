"""Report and SQL query tasks."""

import json as _json
import shlex
import subprocess
import sys
from pathlib import Path

from invoke import task

from dinary.reports import expenses as expenses_report
from dinary.reports import income as income_report
from dinary.tools import sql as sql_module

from ._common import (
    _extract_format_flags,
    _extract_year_month,
    _remote_snapshot_cmd,
    _ssh_capture_bytes,
)


def _run_report_module(c, module: str, flags: list[str], *, remote: bool) -> None:
    """Dispatch a ``dinary.reports.<module>`` run locally or over SSH.

    Both modes follow the same shape: fetch rows → render locally.

    * Local: ``uv run python -m dinary.reports.<module> <flags>`` —
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
        cmd = f"uv run python -m dinary.reports.{module}"
        if flags:
            cmd = f"{cmd} {' '.join(flags)}"
        c.run(cmd)
        return

    as_csv, as_json, filter_flags = _extract_format_flags(flags)

    remote_flags = [*filter_flags, "--json"]
    raw = _ssh_capture_bytes(_remote_snapshot_cmd(f"dinary.reports.{module}", remote_flags))

    if as_json:
        sys.stdout.buffer.write(raw)
        return

    payload = _json.loads(raw.decode("utf-8"))

    if module == "income":
        income_rows = income_report.rows_from_json(payload)
        income_report.render(income_rows, as_csv=as_csv, stream=sys.stdout)
    elif module == "expenses":
        expense_rows = expenses_report.rows_from_json(payload)
        year, month = _extract_year_month(filter_flags)
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
    """Show expenses aggregated by unique (category, event, tags) coord.

    Flags (all optional):
        --year YYYY        restrict to a single calendar year
        --month YYYY-MM    restrict to a single month (mutex with --year)
        --csv              emit CSV to stdout instead of a rich table
        --remote           query the production DB over SSH. Default
                           runs locally against ``data/dinary.db``
                           — useful after ``inv backup`` or during
                           local development.

    The aggregation key is the project's 3D coord: the expense's
    category name, its event name (blank when the expense has no
    event), and the deterministic join of its tag names. Rows sort
    by descending total so the biggest spend lines surface at the top.
    """
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
    """Show income aggregated by year.

    Flags (all optional):
        --csv      emit CSV to stdout instead of a rich table
        --remote   query the production DB over SSH. Default runs
                   locally against ``data/dinary.db``.

    One row per calendar year, with per-year total, count of
    months-with-data, and average per data-month.
    """
    flags: list[str] = []
    if csv:
        flags.append("--csv")
    _run_report_module(c, "income", flags, remote=remote)


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
def sql_query(c, query="", file="", csv=False, json=False, write=False, remote=False):  # noqa: A002,PLR0913,C901,PLR0912
    """Run a SQL query against ``data/dinary.db``.

    By default the connection is opened ``mode=ro`` via the SQLite
    URI form — typoing ``UPDATE`` / ``DELETE`` errors out at the
    SQLite layer instead of quietly mutating the ledger. Pass
    ``--write`` to explicitly opt into mutations for one-off fixups.
    For free-form inspection of the ``app_metadata`` anchor,
    per-currency totals in ``expenses``, sheet-logging job state,
    etc. Report-shaped queries should still live in
    ``dinary.reports.*``.

    Examples::

        inv sql -q "SELECT * FROM app_metadata ORDER BY key"
        inv sql -q "SELECT currency_original, COUNT(*) FROM expenses GROUP BY 1"
        inv sql -f scripts/monthly_summary.sql --csv > out.csv
        inv sql -q "SELECT * FROM app_metadata" --remote
        inv sql -q "DELETE FROM expenses WHERE id = 999" --write

    ``--write`` is rejected together with ``--remote``: mutating
    prod through an SSH pipe into a ``/tmp`` snapshot would silently
    discard the writes when the snapshot is torn down on exit, which
    is a far worse failure mode than a clear "not allowed" error.
    Use ``ssh`` + ``inv sql --write`` on the host for real prod fixups,
    or better — write a proper migration.

    Local concurrency: SQLite in WAL mode lets an ``inv sql --`` run
    read concurrently with a live ``inv dev`` uvicorn writer, so
    you don't need to stop the dev server first. ``--write`` locally
    still needs exclusive file access to the page it writes, so
    running ``--write`` concurrently with the dev server may either
    block briefly (busy_timeout) or surface a ``database is locked``
    error — stop the dev server first for anything non-trivial.

    ``--remote`` follows the same JSON-over-SSH snapshot pattern as
    ``inv report-*`` (see :func:`_remote_snapshot_cmd`): a
    transactionally consistent SQLite snapshot is taken on the
    server via ``sqlite3 .backup``, the module emits a JSON
    envelope, and the local process either forwards those bytes
    (``--csv`` / ``--json``) or renders a rich table.

    ``--file`` + ``--remote`` reads the SQL file locally and ships
    its contents as ``--query`` over SSH — no SCP round-trip.
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

    sql_flags = ["--query", shlex.quote(sql_text)]

    if not remote:
        local_flags = [*sql_flags]
        if csv:
            local_flags.append("--csv")
        elif json:
            local_flags.append("--json")
        if write:
            local_flags.append("--write")
        c.run(f"uv run python -m dinary.tools.sql {' '.join(local_flags)}")
        return

    remote_flags = [*sql_flags, "--json"]
    try:
        raw = _ssh_capture_bytes(_remote_snapshot_cmd("dinary.tools.sql", remote_flags))
    except subprocess.CalledProcessError:
        sys.exit(1)

    if json:
        sys.stdout.buffer.write(raw)
        return

    payload = _json.loads(raw.decode("utf-8"))
    columns, rows = sql_module.rows_from_json(payload)
    if csv:
        sql_module.render_csv(columns, rows, stream=sys.stdout)
    else:
        sql_module.render_rich(columns, rows, stream=sys.stdout)

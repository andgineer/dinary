"""Interactive SQL runner against ``data/dinary.duckdb``.

Invoked via ``inv sql`` (locally or over ``--remote``). By default
the module opens the DB ``read_only=True`` — ``UPDATE`` / ``DELETE``
/ ``INSERT`` statements error out at the DuckDB layer — so an
operator peeking at prod cannot accidentally mutate the ledger by
typoing a query. ``--write`` is the explicit opt-in for mutation
(one-off fixups, ad-hoc cleanups) and is deliberately absent from
the ``--remote`` path so the opt-in can never ride an SSH pipe
into a snapshot that gets torn down on exit. Three output formats
keep parity with ``inv report-*``:

* default (``--``): rich table to stdout, one-line footer with row count
* ``--csv``: CSV with header row, suitable for piping into ``wc`` /
  ``csvkit`` / a spreadsheet
* ``--json``: a single envelope ``{"columns": [...], "rows": [[...], ...],
  "row_count": N}``. Non-primitive values (``Decimal``, ``date``,
  ``datetime``) are stringified — callers that need typed JSON should
  cast in SQL (``amount::DOUBLE``).

The ``--remote`` dispatch lives in ``tasks.py::sql_task`` and goes
through the same ``_remote_snapshot_cmd`` wrapper as ``inv report-*``:
a ``/tmp`` snapshot of the live DB is opened read-only on the server,
the JSON envelope comes back over SSH, and the local process renders.
That avoids the DuckDB single-writer lock while the ``dinary`` service
is up and keeps Cyrillic/box-drawing bytes intact across the wire.
"""

import argparse
import csv as _csv
import json as _json
import sys
from decimal import Decimal
from pathlib import Path
from typing import IO, Any

import duckdb
from rich.console import Console
from rich.table import Table

from dinary.config import settings


def _execute(sql: str, *, write: bool = False) -> tuple[list[str], list[tuple]]:
    """Open ``settings.data_path`` and run ``sql``.

    ``write=False`` (default) connects ``read_only=True`` so the DuckDB
    engine itself refuses any ``INSERT`` / ``UPDATE`` / ``DELETE``;
    ``write=True`` opens in the default read-write mode — used for
    explicit operator-triggered fixups and only reachable through
    ``inv sql --write`` locally (never over ``--remote``).

    Returns ``(columns, rows)``. ``columns == []`` means the statement
    had no result set (DuckDB returns ``cursor.description is None`` for
    DDL / pragma / no-op statements). The connection is closed on every
    exit path so a repeated ``inv sql`` doesn't pile up file handles
    against the DB.
    """
    con = duckdb.connect(str(Path(settings.data_path)), read_only=not write)
    try:
        cursor = con.execute(sql)
        columns = [d[0] for d in cursor.description] if cursor.description else []
        rows = cursor.fetchall() if columns else []
    finally:
        con.close()
    return columns, rows


def _coerce_for_json(value: Any) -> Any:
    """Coerce DuckDB row values into JSON-serialisable primitives.

    DuckDB returns ``Decimal`` for ``DECIMAL`` columns, ``date`` /
    ``datetime`` / ``time`` for temporal columns, ``bytes`` for ``BLOB``,
    and ``UUID`` / ``Interval`` for their respective types. None of these
    survive ``json.dumps`` natively. Primitive types (``int``, ``float``,
    ``str``, ``bool``, ``None``) pass through verbatim so downstream
    ``jq`` filters see native JSON numbers rather than strings.
    """
    if value is None or isinstance(value, int | float | str | bool):
        return value
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def render_rich(columns: list[str], rows: list[tuple], *, stream: IO[str] | None = None) -> None:
    """Write a rich ``Table`` with a subdued footer row-count.

    ``stream`` defaults to ``sys.stdout`` *resolved at call time* — a
    bare ``stream=sys.stdout`` default would bind the current stdout
    at import time, which pytest's ``capsys`` cannot intercept because
    it swaps ``sys.stdout`` after test collection. Same rationale in
    ``render_csv`` / ``render_json``.
    """
    out = stream if stream is not None else sys.stdout
    if not columns:
        out.write("OK (no result set)\n")
        return
    table = Table(show_header=True, header_style="bold cyan")
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*("" if v is None else str(v) for v in row))
    console = Console(file=out)
    console.print(table)
    console.print(f"[dim]{len(rows)} row(s)[/dim]")


def render_csv(columns: list[str], rows: list[tuple], *, stream: IO[str] | None = None) -> None:
    """Write CSV with header. Empty cells for ``NULL`` per standard CSV habit."""
    out = stream if stream is not None else sys.stdout
    writer = _csv.writer(out)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if v is None else v for v in row])


def render_json(columns: list[str], rows: list[tuple], *, stream: IO[str] | None = None) -> None:
    """Write a single JSON envelope used as the SSH wire format too.

    The schema is intentionally stable: ``inv sql --remote`` forwards
    these bytes verbatim when the operator passes ``--json``, so any
    future callers that pipe through ``jq`` can treat local and remote
    output interchangeably.
    """
    out = stream if stream is not None else sys.stdout
    payload = {
        "columns": columns,
        "rows": [[_coerce_for_json(v) for v in row] for row in rows],
        "row_count": len(rows),
    }
    out.write(_json.dumps(payload, ensure_ascii=False))
    out.write("\n")


def rows_from_json(payload: dict) -> tuple[list[str], list[tuple]]:
    """Reverse of ``render_json`` for the local-render path on ``--remote``.

    Rows round-trip as tuples of the same primitives — ``render_rich``
    and ``render_csv`` don't care about types, they ``str(...)``
    everything except ``None``.
    """
    return payload["columns"], [tuple(r) for r in payload["rows"]]


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Parses args, runs the query, renders."""
    parser = argparse.ArgumentParser(
        description="Read-only SQL runner against data/dinary.duckdb",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--query", "-q", help="SQL query string")
    src.add_argument("--file", "-f", type=Path, help="read SQL from file")
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument("--csv", action="store_true", help="emit CSV to stdout")
    fmt.add_argument("--json", action="store_true", help="emit JSON envelope")
    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "open the DB read-write so mutating statements "
            "(UPDATE/DELETE/INSERT) can run. Off by default."
        ),
    )
    args = parser.parse_args(argv)

    sql = args.query if args.query else args.file.read_text(encoding="utf-8")

    columns, rows = _execute(sql, write=args.write)
    if args.csv:
        render_csv(columns, rows)
    elif args.json:
        render_json(columns, rows)
    else:
        render_rich(columns, rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())

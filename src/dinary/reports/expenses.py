"""Aggregated expense viewer grouped by the 3D classification coord.

Reads ``expenses`` + ``expense_tags`` from the runtime DuckDB ledger
and groups by the unique ``(category, event, tags)`` tuple — the
"3D coordinate" the catalog is organized around. Two optional,
mutually exclusive filters narrow the window: ``--year YYYY`` or
``--month YYYY-MM``. Neither flag → whole ledger.

Output is a ``rich`` table by default; ``--csv`` emits plain CSV to
stdout instead, which is what ``inv report-expenses --csv`` consumes
to pipe the result into downstream tooling.

Strictly read-only. Opens the shared ``duckdb_repo`` cursor, runs one
aggregation SELECT, closes the cursor. Safe to run against live
production data while the FastAPI service is serving HTTP.
"""

import argparse
import csv
import dataclasses
import json
import sys
from collections.abc import Iterable
from decimal import Decimal
from typing import TextIO

import duckdb
from rich.console import Console
from rich.table import Table

from dinary.config import settings
from dinary.services import duckdb_repo


@dataclasses.dataclass(frozen=True, slots=True)
class ExpenseSummaryRow:
    """One row of the aggregation: a unique 3D coord + its totals."""

    category: str
    event: str
    tags: str
    rows: int
    total: Decimal


#: Column order used by both the CSV writer and the rich-table builder.
#: Kept module-level so the two renderers cannot drift.
COLUMNS: tuple[str, ...] = ("category", "event", "tags", "rows", "total")

#: Invariants of the ``YYYY-MM`` CLI flag format. Extracted as
#: constants so ``parse_month`` reads declaratively (and to keep
#: ruff's PLR2004 "magic value" check from flagging the literals).
_MONTH_PARTS = 2
_MONTH_MIN = 1
_MONTH_MAX = 12

#: Static SQL body for ``aggregate_expenses``. Only the ``{where}``
#: placeholder is filled with one of three hardcoded fragments from
#: ``_build_filter`` — never with caller-supplied data — and all
#: year/month values travel through real positional bind parameters.
_EXPENSES_AGGREGATION_SQL = """
    WITH tagged AS (
        SELECT
            e.category_id,
            e.event_id,
            e.amount,
            COALESCE(
                (SELECT STRING_AGG(t.name, ', ' ORDER BY t.name)
                 FROM expense_tags et
                 JOIN tags t ON t.id = et.tag_id
                 WHERE et.expense_id = e.id),
                ''
            ) AS tags_joined
        FROM expenses e
        {where}
    )
    SELECT
        c.name AS category,
        COALESCE(ev.name, '') AS event,
        t.tags_joined AS tags,
        COUNT(*) AS rows,
        SUM(t.amount) AS total
    FROM tagged t
    JOIN categories c ON c.id = t.category_id
    LEFT JOIN events ev ON ev.id = t.event_id
    GROUP BY c.name, COALESCE(ev.name, ''), t.tags_joined
    ORDER BY total DESC, category ASC, event ASC, tags ASC
"""


def parse_month(value: str) -> tuple[int, int]:
    """Parse ``YYYY-MM`` into ``(year, month)``.

    Rejected inputs raise ``argparse.ArgumentTypeError`` so the CLI
    prints a clean usage message rather than a stack trace.
    """
    parts = value.split("-")
    if len(parts) != _MONTH_PARTS:
        msg = f"--month expects YYYY-MM, got {value!r}"
        raise argparse.ArgumentTypeError(msg)
    try:
        year = int(parts[0])
        month = int(parts[1])
    except ValueError as exc:
        msg = f"--month expects numeric YYYY-MM, got {value!r}"
        raise argparse.ArgumentTypeError(msg) from exc
    if not _MONTH_MIN <= month <= _MONTH_MAX:
        msg = f"--month month component must be in {_MONTH_MIN}..{_MONTH_MAX}, got {month}"
        raise argparse.ArgumentTypeError(msg)
    return year, month


def _build_filter(
    year: int | None,
    month: tuple[int, int] | None,
) -> tuple[str, list[object]]:
    """Return a ``(where_clause, params)`` pair.

    ``year`` and ``month`` are mutually exclusive at the CLI layer;
    this helper accepts either alone or both ``None`` (= no filter)
    and keeps the SQL short. ``month`` accepts a pre-validated
    ``(year, month)`` pair so invalid values cannot reach the DB.
    """
    if month is not None:
        return (
            "WHERE EXTRACT(YEAR FROM e.datetime) = ? AND EXTRACT(MONTH FROM e.datetime) = ?",
            [month[0], month[1]],
        )
    if year is not None:
        return "WHERE EXTRACT(YEAR FROM e.datetime) = ?", [year]
    return "", []


def aggregate_expenses(
    con: duckdb.DuckDBPyConnection,
    *,
    year: int | None = None,
    month: tuple[int, int] | None = None,
) -> list[ExpenseSummaryRow]:
    """Aggregate matching expense rows by the 3D coord.

    The tag set is joined into a canonical, deterministic string
    (``STRING_AGG(..., ', ' ORDER BY t.name)``) so two expenses whose
    tags are the same set but inserted in different orders collapse
    into one summary row. ``event`` is ``''`` when the expense has
    no event; this matches the project's convention of rendering
    ``event_id IS NULL`` as an empty cell in 2D/3D reports.

    Rows are returned sorted by ``total DESC`` then by ``(category,
    event, tags)`` ASC so ties break deterministically — the
    secondary sort matters for test snapshots.
    """
    where, params = _build_filter(year, month)
    # S608 suppression: ``where`` is one of three hardcoded strings
    # returned by ``_build_filter`` — it never contains user data.
    # User-supplied ``year`` / ``month`` values travel through
    # ``params`` as real positional bind parameters, not through
    # string interpolation. ``str.format`` here just stitches the
    # static fragment into the static SQL body.
    sql = _EXPENSES_AGGREGATION_SQL.format(where=where)
    rows = con.execute(sql, params).fetchall()
    return [
        ExpenseSummaryRow(
            category=str(row[0]),
            event=str(row[1]),
            tags=str(row[2]),
            rows=int(row[3]),
            total=Decimal(str(row[4])),
        )
        for row in rows
    ]


def _format_amount(value: Decimal) -> str:
    """Render an amount with thousands separators and two decimal places."""
    return f"{value:,.2f}"


def render_rich(
    rows: list[ExpenseSummaryRow],
    *,
    currency: str,
    title_suffix: str,
    stream: TextIO | None = None,
) -> None:
    """Pretty-print the summary as a ``rich`` table.

    This module (``dinary.reports.expenses``) is dev-only tooling: it
    is invoked exclusively through ``inv report-expenses`` / ``python -m
    dinary.reports.expenses`` and is never imported by the FastAPI
    runtime. That is why depending on ``rich`` (a dev-only dep) at
    module level is safe here — the runtime import graph never
    transits through ``dinary.reports``. ``stream`` defaults to
    ``sys.stdout`` via ``Console``'s own default.
    """
    console = Console(file=stream)
    title = f"Expenses by 3D coord — {title_suffix} ({currency})"
    table = Table(title=title, show_lines=False)
    table.add_column("Category", style="cyan")
    table.add_column("Event", style="magenta")
    table.add_column("Tags")
    table.add_column("Rows", justify="right", style="green")
    table.add_column(f"Total ({currency})", justify="right", style="bold")

    total_sum = Decimal(0)
    total_count = 0
    for r in rows:
        table.add_row(r.category, r.event, r.tags, str(r.rows), _format_amount(r.total))
        total_sum += r.total
        total_count += r.rows

    if rows:
        table.add_section()
        table.add_row(
            "TOTAL",
            "",
            "",
            str(total_count),
            _format_amount(total_sum),
            style="bold white",
        )

    console.print(table)
    if not rows:
        console.print("[dim](no matching expenses)[/dim]")


def render_csv(rows: list[ExpenseSummaryRow], *, stream: TextIO) -> None:
    """Emit the summary as CSV (header + one row per 3D coord)."""
    writer = csv.writer(stream)
    writer.writerow(COLUMNS)
    for r in rows:
        writer.writerow((r.category, r.event, r.tags, r.rows, f"{r.total:.2f}"))


def render_json(rows: Iterable[ExpenseSummaryRow], *, stream: TextIO) -> None:
    """Emit the summary as a JSON array — the remote-transport format.

    See the corresponding docstring in :mod:`dinary.reports.income` for
    the rationale (avoids the UTF-8 chunk-boundary corruption that
    :meth:`invoke.runners.Runner.decode` introduces when rich-rendered
    text is shipped as stdout over SSH).

    ``category`` / ``event`` / ``tags`` routinely contain Cyrillic
    (``еда``, ``отпуск-2026``, ``собака``), so ``ensure_ascii=False``
    matters here even more than it does in ``reports.income``: with
    ``ensure_ascii=True`` the Cyrillic fields balloon 6× (one
    ``\\uXXXX`` escape per character) and become unreadable in raw
    ``--json`` output, while adding zero safety — UTF-8 on the wire
    is the contract anyway.
    """
    payload = [
        {
            "category": r.category,
            "event": r.event,
            "tags": r.tags,
            "rows": r.rows,
            "total": format(r.total, "f"),
        }
        for r in rows
    ]
    json.dump(payload, stream, ensure_ascii=False)
    stream.write("\n")


def rows_from_json(payload: list[dict]) -> list[ExpenseSummaryRow]:
    """Inverse of :func:`render_json` — used by the local render step.

    Strings come back with ``int(entry["rows"])`` tolerant of both
    int and str encodings because downstream code may JSON-round-trip
    the payload through a tool that stringifies all scalars.
    """
    return [
        ExpenseSummaryRow(
            category=str(entry["category"]),
            event=str(entry["event"]),
            tags=str(entry["tags"]),
            rows=int(entry["rows"]),
            total=Decimal(str(entry["total"])),
        )
        for entry in payload
    ]


def _title_suffix(year: int | None, month: tuple[int, int] | None) -> str:
    if month is not None:
        return f"{month[0]:04d}-{month[1]:02d}"
    if year is not None:
        return str(year)
    return "all time"


def render(  # noqa: PLR0913 — all args are cosmetic format knobs dispatched inline
    rows: list[ExpenseSummaryRow],
    *,
    year: int | None = None,
    month: tuple[int, int] | None = None,
    as_csv: bool = False,
    as_json: bool = False,
    stream: TextIO | None = None,
) -> None:
    """Render prefetched rows in the requested format.

    Single entry point used by both ``run()`` (local DuckDB path) and
    the ``tasks.py`` remote transport (rows arrive as a JSON payload
    over SSH). ``year`` / ``month`` are forwarded only to the rich
    renderer where they land in the table title — the rows
    themselves are already filtered upstream, so the renderer never
    uses them for anything but cosmetics.
    """
    if as_csv and as_json:
        msg = "--csv and --json are mutually exclusive"
        raise ValueError(msg)
    out = stream if stream is not None else sys.stdout
    if as_json:
        render_json(rows, stream=out)
    elif as_csv:
        render_csv(rows, stream=out)
    else:
        render_rich(
            rows,
            currency=settings.app_currency,
            title_suffix=_title_suffix(year, month),
            stream=out,
        )


def run(
    *,
    year: int | None,
    month: tuple[int, int] | None,
    as_csv: bool = False,
    as_json: bool = False,
    stream: TextIO | None = None,
) -> int:
    """Headless entry point used by both ``main()`` and tests.

    Thin ``fetch → render`` composition: open the local DuckDB, run
    the 3D aggregation (optionally filtered by ``year`` / ``month``),
    render. Returns the intended CLI exit code: ``0``
    unconditionally, since "no matching expenses" is a valid report
    outcome, not an error.

    ``as_csv`` and ``as_json`` are mutually exclusive; see the same
    contract in :func:`dinary.reports.income.run` for the rationale.
    """
    if as_csv and as_json:
        msg = "--csv and --json are mutually exclusive"
        raise ValueError(msg)
    if not duckdb_repo.DB_PATH.exists():
        msg = (
            f"DB not found at {duckdb_repo.DB_PATH}. Either point "
            "DINARY_DATA_PATH at an existing DuckDB file, or use "
            "`inv report-expenses --remote` to query the server."
        )
        print(msg, file=sys.stderr)
        return 1

    con = duckdb_repo.get_connection()
    try:
        rows = aggregate_expenses(con, year=year, month=month)
    finally:
        con.close()

    render(
        rows,
        year=year,
        month=month,
        as_csv=as_csv,
        as_json=as_json,
        stream=stream,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Show expenses aggregated by unique (category, event, tags) coord.",
    )
    window = parser.add_mutually_exclusive_group()
    window.add_argument("--year", type=int, help="restrict to a single year")
    window.add_argument(
        "--month",
        type=parse_month,
        help="restrict to a single month (YYYY-MM)",
    )
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument(
        "--csv",
        action="store_true",
        help="emit CSV to stdout instead of a rich table",
    )
    fmt.add_argument(
        "--json",
        action="store_true",
        help=(
            "emit a JSON array of rows to stdout (wire format used by "
            "``inv report-expenses --remote`` to carry raw data back "
            "for local rendering)"
        ),
    )
    args = parser.parse_args(argv)
    return run(
        year=args.year,
        month=args.month,
        as_csv=args.csv,
        as_json=args.json,
    )


if __name__ == "__main__":
    sys.exit(main())

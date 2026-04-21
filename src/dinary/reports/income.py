"""Aggregated income viewer grouped by year.

Reads the ``income`` table (one row per ``(year, month)``) and rolls
it up to one row per year with per-year total, months-with-data
count, and average-per-month. ``inv report-income`` wraps this module.

Output is a ``rich`` table by default; ``--csv`` emits plain CSV to
stdout instead. Strictly read-only — no DB writes, no ledger mutation.
"""

import argparse
import csv
import dataclasses
import sys
from decimal import Decimal
from typing import TextIO

import duckdb
from rich.console import Console
from rich.table import Table

from dinary.config import settings
from dinary.services import duckdb_repo


@dataclasses.dataclass(frozen=True, slots=True)
class IncomeSummaryRow:
    """One row of the aggregation: one calendar year."""

    year: int
    months: int
    total: Decimal
    avg_month: Decimal


#: Column order used by both renderers.
COLUMNS: tuple[str, ...] = ("year", "months", "total", "avg_month")


def aggregate_income(con: duckdb.DuckDBPyConnection) -> list[IncomeSummaryRow]:
    """Return one summary row per year present in the ``income`` table.

    ``avg_month`` divides the per-year total by the count of
    months-with-data (1..12), **not** by 12: that answers "how much
    income per month, when there was any" rather than "how much
    monthly income would I have averaged over the full year" — the
    former is more useful for spotting gaps in legacy sheets where
    some months never had income recorded.

    Rows come back newest-year-first so a terminal printout shows
    the most relevant years at the top.
    """
    sql = """
        SELECT
            year,
            COUNT(*) AS months,
            SUM(amount) AS total,
            SUM(amount) / COUNT(*) AS avg_month
        FROM income
        GROUP BY year
        ORDER BY year DESC
    """
    rows = con.execute(sql).fetchall()
    return [
        IncomeSummaryRow(
            year=int(row[0]),
            months=int(row[1]),
            total=Decimal(str(row[2])),
            avg_month=Decimal(str(row[3])),
        )
        for row in rows
    ]


def _format_amount(value: Decimal) -> str:
    return f"{value:,.2f}"


def render_rich(
    rows: list[IncomeSummaryRow],
    *,
    currency: str,
    stream: TextIO | None = None,
) -> None:
    """Pretty-print the summary as a ``rich`` table.

    See the corresponding renderer in ``dinary.reports.expenses`` for
    the rationale behind depending on ``rich`` at module level (this
    subpackage is dev-only tooling, outside the runtime import graph).
    """
    console = Console(file=stream)
    table = Table(title=f"Income by year ({currency})", show_lines=False)
    table.add_column("Year", justify="right", style="cyan")
    table.add_column("Months", justify="right")
    table.add_column(f"Total ({currency})", justify="right", style="bold")
    table.add_column(f"Avg / month ({currency})", justify="right")

    total_sum = Decimal(0)
    total_months = 0
    for r in rows:
        table.add_row(
            str(r.year),
            str(r.months),
            _format_amount(r.total),
            _format_amount(r.avg_month),
        )
        total_sum += r.total
        total_months += r.months

    if rows:
        table.add_section()
        overall_avg = total_sum / total_months if total_months else Decimal(0)
        table.add_row(
            "TOTAL",
            str(total_months),
            _format_amount(total_sum),
            _format_amount(overall_avg),
            style="bold white",
        )

    console.print(table)
    if not rows:
        console.print("[dim](no income rows)[/dim]")


def render_csv(rows: list[IncomeSummaryRow], *, stream: TextIO) -> None:
    """Emit the summary as CSV (header + one row per year)."""
    writer = csv.writer(stream)
    writer.writerow(COLUMNS)
    for r in rows:
        writer.writerow(
            (
                r.year,
                r.months,
                f"{r.total:.2f}",
                f"{r.avg_month:.2f}",
            ),
        )


def run(*, as_csv: bool, stream: TextIO | None = None) -> int:
    """Headless entry point used by both ``main()`` and tests."""
    out = stream if stream is not None else sys.stdout
    if not duckdb_repo.DB_PATH.exists():
        msg = (
            f"DB not found at {duckdb_repo.DB_PATH}. Either point "
            "DINARY_DATA_PATH at an existing DuckDB file, or use "
            "`inv report-income --remote` to query the server."
        )
        print(msg, file=sys.stderr)
        return 1

    con = duckdb_repo.get_connection()
    try:
        rows = aggregate_income(con)
    finally:
        con.close()

    if as_csv:
        render_csv(rows, stream=out)
    else:
        render_rich(rows, currency=settings.app_currency, stream=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Show income aggregated by year.",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="emit CSV to stdout instead of a rich table",
    )
    args = parser.parse_args(argv)
    return run(as_csv=args.csv)


if __name__ == "__main__":
    sys.exit(main())

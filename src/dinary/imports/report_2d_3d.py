"""2D to 3D resolution report for migration quality review.

Reads real sheet rows across all years, resolves each row through the
same pipeline as the importer (mapping, heuristics, post-import fixes),
and renders a compact summary for visual inspection. Strictly read-only:
no DB writes, no ID allocation.

Output always goes to stdout. CLI mirrors ``python -m
dinary.reports.*``: rich table by default, ``--csv`` for CSV,
``--json`` for the structured wire format used by
``inv import-report-2d-3d --remote``.

Usage::

    python -m dinary.imports.report_2d_3d \\
        [--detail] [--csv | --json] [--year YYYY]
"""

import argparse
import csv
import dataclasses
import json
import logging
import sys
from collections import defaultdict
from decimal import Decimal
from typing import IO

from rich.console import Console
from rich.table import Table

from dinary.config import read_import_sources
from dinary.imports.expense_import import (
    build_resolution_context,
    iter_parsed_sheet_rows,
    resolve_row_to_3d,
)
from dinary.services import duckdb_repo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Row models
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class DetailRow:
    category: str
    event: str
    tags: str
    sheet_category: str
    sheet_group: str
    resolution_kind: str
    year: int
    month: int
    amount_eur: float
    comment: str


@dataclasses.dataclass(frozen=True, slots=True)
class SummaryRow:
    category: str
    event: str
    tags: str
    rows: int
    sheet_category: str
    sheet_group: str
    resolution_kind: str
    years: str
    amount: str
    comment: str


SUMMARY_COLUMNS = [
    "category",
    "event",
    "tags",
    "rows",
    "sheet_category",
    "sheet_group",
    "resolution_kind",
    "years",
    "amount",
    "comment",
]

DETAIL_COLUMNS = [
    "category",
    "event",
    "tags",
    "sheet_category",
    "sheet_group",
    "resolution_kind",
    "year",
    "month",
    "amount_eur",
    "comment",
]


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def render_years(years: list[int]) -> str:
    """Render years as a compact range string. Sorts defensively."""
    if not years:
        return ""
    sorted_years = sorted(set(years))
    if len(sorted_years) == 1:
        return str(sorted_years[0])
    ranges: list[str] = []
    start = prev = sorted_years[0]
    for y in sorted_years[1:]:
        if y == prev + 1:
            prev = y
        else:
            ranges.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = y
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(ranges)


# Centralized amount formatting so DetailRow / SummaryRow / scalar
# rendering all agree on the precision and format. Private — there is
# no external consumer; if one shows up, promote in one go.
_AMOUNT_FORMAT = "{:.2f}"


def _format_amount(amount: float | Decimal) -> str:
    return _AMOUNT_FORMAT.format(amount)


def render_amount_range(amounts: list[float]) -> str:
    """Render amounts as a single value or ``min..max`` range.

    Dedup goes through ``Decimal`` quantized to two decimal places so
    that floats that print identically (``0.1 + 0.2`` vs ``0.3``) also
    deduplicate identically. Comparison stays on the quantized value
    too — bypassing the float-equality footgun entirely.
    """
    if not amounts:
        return ""
    quantum = Decimal("0.01")
    unique = sorted({Decimal(str(a)).quantize(quantum) for a in amounts})
    if len(unique) == 1:
        return _format_amount(unique[0])
    return f"{_format_amount(unique[0])}..{_format_amount(unique[-1])}"


def render_comments(comments: list[str]) -> str:
    """Render comments as a single value or a multiplicity indicator."""
    unique = sorted(set(comments))
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]
    return f"{len(unique)} variants"


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------


def _get_import_years() -> list[int]:
    return sorted(r.year for r in read_import_sources())


@dataclasses.dataclass(slots=True)
class CollectStats:
    rows: int = 0
    skipped_unresolved: int = 0
    skipped_errors: int = 0
    skipped_years: int = 0


def _collect_year(con, year: int, stats: CollectStats) -> list[DetailRow]:
    """Resolve every parsed sheet row of *year* into a ``DetailRow``.

    *con* must be a config connection that stays alive for the whole
    call. The function never opens or closes connections so callers
    can amortize the singleton-engine ATTACH across all years.

    Failures that take down the whole year (missing context, sheet
    fetch errors, etc.) are caught and logged here so the report can
    still produce useful data for the remaining years.
    """
    ctx = build_resolution_context(con, year)
    if ctx is None:
        logger.warning("No resolution context for year %d, skipping", year)
        stats.skipped_years += 1
        return []

    details: list[DetailRow] = []
    try:
        parsed_rows = list(iter_parsed_sheet_rows(year, con=con))
    except Exception:  # noqa: BLE001 — see comment below
        # iter_parsed_sheet_rows can fail in many distinct ways (gspread
        # APIError on quota / network / auth, malformed sheet structure,
        # an unexpected currency in the layout, ValueError when this
        # year row is missing from import_sources, …). The report
        # is a multi-year diagnostic: a single bad year must not abort
        # the run, so we widen the catch deliberately, log with full
        # traceback, and bump the year-level counter so callers can
        # surface it through the exit code.
        logger.exception("Skipping year %d: failed to fetch/parse sheet rows", year)
        stats.skipped_years += 1
        return []

    for parsed in parsed_rows:
        try:
            resolved = resolve_row_to_3d(
                con,
                sheet_category=parsed.sheet_category,
                sheet_group=parsed.sheet_group,
                comment=parsed.comment,
                amount_eur=parsed.amount_eur,
                year=parsed.year,
                travel_event_id=ctx.travel_event_id,
                business_trip_event_id=ctx.business_trip_event_id,
                relocation_event_id=ctx.relocation_event_id,
                russia_trip_event_id=ctx.russia_trip_event_id,
                beneficiary_raw=parsed.beneficiary_raw,
            )
        except ValueError:
            logger.exception(
                "Resolution raised for (%r, %r) year %d row %d",
                parsed.sheet_category,
                parsed.sheet_group,
                year,
                parsed.row_idx,
            )
            stats.skipped_errors += 1
            continue

        if resolved is None:
            stats.skipped_unresolved += 1
            continue

        stats.rows += 1
        details.append(
            DetailRow(
                category=resolved.category_name,
                event=resolved.event_name or "",
                tags=", ".join(resolved.tag_names),
                sheet_category=parsed.sheet_category,
                sheet_group=parsed.sheet_group,
                resolution_kind=resolved.resolution_kind,
                year=parsed.year,
                month=parsed.month,
                amount_eur=parsed.amount_eur,
                comment=parsed.comment,
            ),
        )
    logger.info("Year %d: collected %d detail rows", year, len(details))
    return details


def collect_detail_rows(
    years: list[int] | None = None,
    stats: CollectStats | None = None,
) -> list[DetailRow]:
    """Collect resolved detail rows for the given (or all) years.

    Opens a single config connection and reuses it across all years
    so the singleton engine attaches config exactly once for the
    whole report run.
    """
    if stats is None:
        stats = CollectStats()
    con = duckdb_repo.get_connection()
    try:
        years_to_process = years if years is not None else _get_import_years()
        all_details: list[DetailRow] = []
        for year in years_to_process:
            all_details.extend(_collect_year(con, year, stats))
        return all_details
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Summary aggregation
# ---------------------------------------------------------------------------

SummaryKey = tuple[str, str, str, str, str, str]


def build_summary(detail_rows: list[DetailRow]) -> list[SummaryRow]:
    """Aggregate detail rows into a compact summary by grouping key."""
    groups: dict[SummaryKey, list[DetailRow]] = defaultdict(list)
    for row in detail_rows:
        key: SummaryKey = (
            row.category,
            row.event,
            row.tags,
            row.sheet_category,
            row.sheet_group,
            row.resolution_kind,
        )
        groups[key].append(row)

    summaries: list[SummaryRow] = []
    for key, rows in groups.items():
        category, event, tags, sheet_category, sheet_group, resolution_kind = key
        years_list = sorted({r.year for r in rows})
        amounts_list = [r.amount_eur for r in rows]
        comments_list = [r.comment for r in rows if r.comment]

        summaries.append(
            SummaryRow(
                category=category,
                event=event,
                tags=tags,
                rows=len(rows),
                sheet_category=sheet_category,
                sheet_group=sheet_group,
                resolution_kind=resolution_kind,
                years=render_years(years_list),
                amount=render_amount_range(amounts_list),
                comment=render_comments(comments_list),
            ),
        )

    summaries.sort(
        key=lambda s: (
            s.category,
            s.event,
            s.tags,
            s.sheet_category,
            s.sheet_group,
            s.resolution_kind,
        ),
    )
    return summaries


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_csv(
    rows: list[SummaryRow] | list[DetailRow],
    columns: list[str],
    output: IO[str] | None = None,
) -> None:
    """Emit the report as CSV (header + one row per entry)."""
    dest = output or sys.stdout
    writer = csv.writer(dest)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(dataclasses.astuple(row))


def render_json(
    rows: list[SummaryRow] | list[DetailRow],
    columns: list[str],
    output: IO[str] | None = None,
) -> None:
    """Emit the report as a JSON envelope.

    Wire format for ``inv import-report-2d-3d --remote``: the remote
    emits this payload, the local side renders it. Envelope shape::

        {
            "detail": bool,        # True → DetailRow, False → SummaryRow
            "columns": [...],      # field order for display
            "rows": [{field: value}, ...],
        }

    ``detail`` is the row-type discriminator so :func:`rows_from_json`
    can pick the right dataclass without sniffing field names.
    ``ensure_ascii=False`` keeps Cyrillic fields
    (``"путешествия"``, ``"отпуск-2026"``) readable in raw ``--json``
    output and avoids a ~6× payload inflation from ``\\uXXXX`` escapes.
    """
    dest = output or sys.stdout
    detail = columns is DETAIL_COLUMNS
    payload = {
        "detail": detail,
        "columns": list(columns),
        "rows": [dataclasses.asdict(row) for row in rows],
    }
    json.dump(payload, dest, ensure_ascii=False)
    dest.write("\n")


def rows_from_json(
    payload: dict,
) -> list[SummaryRow] | list[DetailRow]:
    """Inverse of :func:`render_json`.

    Dispatches on ``payload["detail"]`` to instantiate the correct
    dataclass so downstream renderers receive a homogeneous list
    and don't need to branch on row shape.
    """
    if payload.get("detail"):
        return [DetailRow(**row) for row in payload["rows"]]
    return [SummaryRow(**row) for row in payload["rows"]]


# ---------------------------------------------------------------------------
# rich rendering
# ---------------------------------------------------------------------------

#: Column names that should be right-aligned in the rich table.
#: Kept tiny and declarative so new numeric columns (e.g. a future
#: "row_count_eur") only need to be listed here, not in per-call
#: ``add_column`` sites.
_RIGHT_ALIGNED_COLUMNS = frozenset({"rows", "year", "month", "amount", "amount_eur"})

#: Colour map for the ``resolution_kind`` column. Keyed on the
#: *primary* kind (first ``+``-separated segment) emitted by
#: ``dinary.imports.expense_import.resolve_row_to_3d``:
#:
#: * ``mapping``    — row was resolved via the explicit import mapping
#:   (happy path, green).
#: * ``derivation`` — mapping missed, a heuristic
#:   ``canonical_category_for_source`` rule resolved it (yellow).
#:
#: Anything else falls through uncoloured so unknown primary kinds
#: degrade gracefully rather than raising.
_KIND_STYLE = {
    "mapping": "green",
    "derivation": "yellow",
}


def _style_for_resolution_kind(kind: str) -> str:
    """Return a rich style string for ``resolution_kind`` cell text.

    Splits on ``+`` to honour compound labels like
    ``"mapping+heuristic"`` / ``"derivation+postfix"`` by colouring
    on the primary segment only. Empty / unknown kinds return ``""``
    which rich treats as "no style".
    """
    if not kind:
        return ""
    primary = kind.split("+", 1)[0]
    return _KIND_STYLE.get(primary, "")


#: Forced console dimensions for the rich renderer.
#:
#: The primary consumer of this module is ``inv import-report-2d-3d``,
#: which runs Python on the server and pipes stdout back through SSH.
#: In that pipe rich sees a non-TTY file and, if ``TERM=dumb`` (or no
#: ``TERM`` at all), short-circuits to ``(80, 25)`` regardless of any
#: ``width=`` override — too narrow for our 10-column summary and it
#: clips category / tag / comment cells behind ``…``.
#:
#: Passing **both** ``width`` and ``height`` to ``Console`` makes it
#: take the direct-return branch in ``Console.size`` and honour the
#: override unconditionally, so the remote rendering carries full
#: content that operators eyeball in their own (wider) local terminal.
_RICH_CONSOLE_WIDTH = 220
_RICH_CONSOLE_HEIGHT = 25


def render_rich(
    rows: list[SummaryRow] | list[DetailRow],
    columns: list[str],
    output: IO[str] | None = None,
) -> None:
    """Render *rows* as a ``rich.Table``.

    Stdout-only format: unlike ``csv`` / ``md``, rich output goes
    straight to the console and never lands on disk. Numeric
    columns (``rows`` / ``year`` / ``month`` / ``amount`` /
    ``amount_eur``) are right-aligned; ``resolution_kind`` cells
    are colour-coded by primary kind so an operator can spot
    fallback-to-derivation lines at a glance.

    When *rows* is empty the function prints a dim placeholder,
    matching the other renderers' contract of always emitting
    *something* so stdout is never silently empty.
    """
    console = Console(
        file=output,
        width=_RICH_CONSOLE_WIDTH,
        height=_RICH_CONSOLE_HEIGHT,
    )
    title = "2D→3D resolution report — " + ("detail" if columns is DETAIL_COLUMNS else "summary")
    table = Table(title=title, show_lines=False)
    for column in columns:
        justify = "right" if column in _RIGHT_ALIGNED_COLUMNS else "left"
        table.add_column(column, justify=justify)

    try:
        kind_index = columns.index("resolution_kind")
    except ValueError:
        kind_index = -1

    for row in rows:
        values = [str(v) for v in dataclasses.astuple(row)]
        if kind_index >= 0:
            kind_value = values[kind_index]
            style = _style_for_resolution_kind(kind_value)
            if style:
                values[kind_index] = f"[{style}]{kind_value}[/{style}]"
        table.add_row(*values)

    console.print(table)
    if not rows:
        console.print("[dim](no rows)[/dim]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def generate_report(
    *,
    detail: bool = False,
    as_csv: bool = False,
    as_json: bool = False,
    years: list[int] | None = None,
    stream: IO[str] | None = None,
) -> CollectStats:
    """Generate and render the 2D-to-3D resolution report to stdout.

    Strictly read-only: assumes ``data/dinary.duckdb`` already exists
    (populated by ``inv import-catalog``). Raises ``FileNotFoundError``
    if it doesn't, instead of silently creating an empty database.

    ``as_csv`` and ``as_json`` are mutually exclusive; see
    :func:`dinary.reports.income.run` for the rationale.
    """
    if as_csv and as_json:
        msg = "--csv and --json are mutually exclusive"
        raise ValueError(msg)
    if not duckdb_repo.DB_PATH.exists():
        msg = f"DB not found at {duckdb_repo.DB_PATH}. Run `inv import-catalog` first."
        raise FileNotFoundError(msg)

    out = stream if stream is not None else sys.stdout

    stats = CollectStats()
    detail_rows = collect_detail_rows(years=years, stats=stats)

    if detail:
        columns = DETAIL_COLUMNS
        display_rows: list[SummaryRow] | list[DetailRow] = detail_rows
    else:
        columns = SUMMARY_COLUMNS
        display_rows = build_summary(detail_rows)

    if as_json:
        render_json(display_rows, columns, output=out)
    elif as_csv:
        render_csv(display_rows, columns, output=out)
    else:
        render_rich(display_rows, columns, output=out)

    print(
        f"Stats: rows={stats.rows} unresolved={stats.skipped_unresolved} "
        f"errors={stats.skipped_errors} skipped_years={stats.skipped_years}",
        file=sys.stderr,
    )
    return stats


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(description="2D->3D resolution report")
    parser.add_argument("--detail", action="store_true", help="per-row output")
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
            "emit a JSON envelope to stdout (wire format used by "
            "``inv import-report-2d-3d --remote``)"
        ),
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="limit to a single year",
    )
    args = parser.parse_args()

    stats = generate_report(
        detail=args.detail,
        as_csv=args.csv,
        as_json=args.json,
        years=[args.year] if args.year is not None else None,
    )
    # Non-zero exit when something went wrong, so CI / scripted callers
    # don't silently treat a partially-broken run as success.
    if stats.skipped_errors or stats.skipped_years:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""2D to 3D resolution report for migration quality review.

Reads real sheet rows across all years, resolves each row through the
same pipeline as the importer (mapping, heuristics, post-import fixes),
and renders a compact summary for visual inspection.  The report is
strictly read-only: no DB writes, no ID allocation.

Usage::

    python -m dinary.imports.report_2d_3d \\
        [--detail] [--fmt stdout|csv|md] [--output PATH] [--year YYYY]
"""

import argparse
import csv
import dataclasses
import io
import logging
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

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


def _get_import_years(con) -> list[int]:
    rows = con.execute(
        "SELECT year FROM import_sources ORDER BY year",
    ).fetchall()
    return [r[0] for r in rows]


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
        parsed_rows = list(iter_parsed_sheet_rows(year, config_con=con))
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
                month=parsed.month,
                row_idx=parsed.row_idx,
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
    con = duckdb_repo.get_config_connection(read_only=True)
    try:
        years_to_process = years if years is not None else _get_import_years(con)
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


def _format_table_row(values: list[str], widths: list[int]) -> str:
    return "  ".join(str(v).ljust(w) for v, w in zip(values, widths, strict=False))


def render_stdout(
    rows: list[SummaryRow] | list[DetailRow],
    columns: list[str],
    output: io.TextIOBase | None = None,
) -> None:
    """Print a plain-text table to *output* (defaults to ``sys.stdout``)."""
    dest = output or sys.stdout
    if not rows:
        dest.write("No rows.\n")
        return

    str_rows = [[str(v) for v in dataclasses.astuple(r)] for r in rows]
    widths = [max(len(columns[i]), *(len(sr[i]) for sr in str_rows)) for i in range(len(columns))]

    dest.write(_format_table_row(columns, widths) + "\n")
    dest.write("  ".join("-" * w for w in widths) + "\n")
    for sr in str_rows:
        dest.write(_format_table_row(sr, widths) + "\n")


def render_csv(
    rows: list[SummaryRow] | list[DetailRow],
    columns: list[str],
    output: io.TextIOBase,
) -> None:
    writer = csv.writer(output)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(dataclasses.astuple(row))


def render_markdown(
    rows: list[SummaryRow] | list[DetailRow],
    columns: list[str],
    output: io.TextIOBase,
) -> None:
    if not rows:
        output.write("*No rows.*\n")
        return
    output.write("| " + " | ".join(columns) + " |\n")
    output.write("| " + " | ".join("---" for _ in columns) + " |\n")
    for row in rows:
        values = dataclasses.astuple(row)
        escaped = [str(v).replace("|", "\\|") for v in values]
        output.write("| " + " | ".join(escaped) + " |\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


# File-output formats. The report emits to stdout when not in this set.
_FILE_FORMATS = ("csv", "md")


def _default_output_path(output_format: str) -> Path:
    """Resolve the default output path for a file-emitting format.

    Computed lazily on every call so monkeypatching
    ``duckdb_repo.DATA_DIR`` (per-test ``tmp_path``) takes effect
    without having to also patch a module-level cached constant.
    """
    return duckdb_repo.DATA_DIR / "reports" / f"import_report_2d_3d.{output_format}"


def generate_report(
    *,
    detail: bool = False,
    output_format: str = "stdout",
    output_path: str | None = None,
    years: list[int] | None = None,
) -> CollectStats:
    """Generate and render the 2D-to-3D resolution report.

    Strictly read-only: assumes ``config.duckdb`` already exists
    (rebuilt by ``inv import-catalog``). Raises ``FileNotFoundError``
    if it doesn't, instead of silently creating an empty config.

    ``output_path`` is only honored for file-emitting formats
    (``csv``, ``md``); passing it together with ``output_format=stdout``
    raises ``ValueError`` so a forgotten ``--fmt`` doesn't silently
    discard the requested file.
    """
    if not duckdb_repo.CONFIG_DB.exists():
        msg = f"config DB not found at {duckdb_repo.CONFIG_DB}. Run `inv import-catalog` first."
        raise FileNotFoundError(msg)
    if output_path and output_format not in _FILE_FORMATS:
        msg = (
            f"output_path={output_path!r} requires output_format in "
            f"{_FILE_FORMATS}, got {output_format!r}. "
            "Did you forget --fmt csv or --fmt md?"
        )
        raise ValueError(msg)

    stats = CollectStats()
    detail_rows = collect_detail_rows(years=years, stats=stats)

    if detail:
        columns = DETAIL_COLUMNS
        display_rows: list[SummaryRow] | list[DetailRow] = detail_rows
    else:
        columns = SUMMARY_COLUMNS
        display_rows = build_summary(detail_rows)

    if output_format in _FILE_FORMATS:
        path = Path(output_path) if output_path else _default_output_path(output_format)
        path.parent.mkdir(parents=True, exist_ok=True)
        renderer = render_csv if output_format == "csv" else render_markdown
        # csv.writer adds its own line terminators, so opening with
        # newline="" prevents Python from translating them and producing
        # blank lines on Windows. Markdown is plain text, so the default
        # newline translation (None) is fine.
        newline_arg = "" if output_format == "csv" else None
        with open(path, "w", newline=newline_arg, encoding="utf-8") as f:
            renderer(display_rows, columns, f)
        # stderr so the report file path doesn't pollute redirected stdout.
        print(f"Written {len(display_rows)} rows to {path}", file=sys.stderr)
    else:
        render_stdout(display_rows, columns)

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
    parser.add_argument(
        "--fmt",
        default="stdout",
        choices=["stdout", *_FILE_FORMATS],
        help="output format",
    )
    parser.add_argument("--output", default="", help="output file path (csv/md only)")
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="limit to a single year",
    )
    args = parser.parse_args()

    stats = generate_report(
        detail=args.detail,
        output_format=args.fmt,
        output_path=args.output or None,
        years=[args.year] if args.year is not None else None,
    )
    # Non-zero exit when something went wrong, so CI / scripted callers
    # don't silently treat a partially-broken run as success.
    if stats.skipped_errors or stats.skipped_years:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

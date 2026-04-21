"""Rich renderer for ``verify_income_equivalence`` / ``*-all``.

Takes the dict (or list of dicts) that
:func:`dinary.imports.verify_income.verify_income_equivalence`
returns and renders it as a compact summary + drill-down. Mirrors
:mod:`dinary.reports.verify_budget` but handles the additional
early-exit "error" branch that ``verify_income_equivalence`` uses
when ``import_sources.json`` is missing the year / the layout is
unknown.
"""

import json
import sys
from typing import TextIO

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

#: Minimum keys we need to recognize a successful verification
#: payload. The error branch has ``ok=False`` and an ``error`` key
#: but none of the totals, so we dispatch on ``"error" in payload``
#: first and only rely on this set for the happy path.
_REQUIRED_KEYS = frozenset({"year", "ok"})


def _looks_like_result(payload: object) -> bool:
    return isinstance(payload, dict) and _REQUIRED_KEYS.issubset(payload.keys())


def _is_error_payload(payload: dict) -> bool:
    return "error" in payload


def _fmt_amount(value: object) -> str:
    if isinstance(value, int | float):
        return f"{value:,.2f}"
    return str(value) if value is not None else ""


def _status_markup(ok: bool) -> str:
    return "[bold green]OK[/bold green]" if ok else "[bold red]FAIL[/bold red]"


def _render_month_diffs(console: Console, result: dict) -> None:
    """Render the per-month diff table if ``month_diffs`` is non-empty.

    The verifier compares totals aggregated per month, so each diff
    row has (month, sheet_acc, db_acc, diff) in the accounting
    currency. Row count is added to the title so the operator sees
    "2 row(s)" at a glance without counting lines.
    """
    rows = result.get("month_diffs") or []
    if not rows:
        return
    table = Table(
        title=f"Month diffs — {len(rows)} row(s)",
        show_lines=False,
    )
    table.add_column("Month", justify="right", style="cyan")
    table.add_column("Sheet", justify="right")
    table.add_column("DB", justify="right")
    table.add_column("Diff", justify="right", style="red")
    for row in rows:
        table.add_row(
            str(row.get("month", "")),
            _fmt_amount(row.get("sheet_acc")),
            _fmt_amount(row.get("db_acc")),
            _fmt_amount(row.get("diff")),
        )
    console.print(table)


def render_single(
    result: dict,
    *,
    stream: TextIO | None = None,
) -> None:
    """Render a single-year income-verify result.

    Dispatches on payload shape:

    * Error branch (``"error"`` key present) → single red panel
      with the message. No totals / diffs are defined in this case.
    * Success branch → summary panel with totals + optional
      ``month_diffs`` table for failing months.
    """
    console = Console(file=stream)
    if not _looks_like_result(result):
        console.print(
            Panel(
                f"Unexpected payload shape: {result!r}",
                title="verify-income-equivalence",
                border_style="red",
            ),
        )
        return

    year = result.get("year")
    ok = bool(result.get("ok"))

    if _is_error_payload(result):
        console.print(
            Panel(
                str(result.get("error", "(no detail)")),
                title=f"Verify income — year {year} — [bold red]FAIL[/bold red]",
                border_style="red",
            ),
        )
        return

    sheet_total = result.get("total_sheet_acc")
    db_total = result.get("total_db_acc")
    diff = None
    if isinstance(sheet_total, int | float) and isinstance(db_total, int | float):
        diff = abs(sheet_total - db_total)

    currency = result.get("accounting_currency", "")
    summary_lines = [
        f"Status:           {_status_markup(ok)}",
        f"Currency:         {currency}",
        f"Months in sheet:  {result.get('months_in_sheet', 0)}",
        f"Months in DB:     {result.get('months_in_db', 0)}",
        f"Total sheet:      {_fmt_amount(sheet_total)}",
        f"Total DB:         {_fmt_amount(db_total)}",
        f"Total diff:       {_fmt_amount(diff)}",
    ]
    console.print(
        Panel.fit(
            "\n".join(summary_lines),
            title=f"Verify income — year {year}",
            border_style="green" if ok else "red",
        ),
    )
    _render_month_diffs(console, result)


def render_batch(
    results: list[dict],
    *,
    stream: TextIO | None = None,
) -> None:
    """Render a multi-year income-verify result.

    Error-branch entries (``"error"`` key present) appear as a
    single "error: …" cell in the Status column so the summary
    stays one-row-per-year; the full message then shows up in the
    drill-down section together with other failing years.
    """
    console = Console(file=stream)
    if not results:
        console.print("[dim](no years to verify)[/dim]")
        return

    summary = Table(
        title=f"Income equivalence — {len(results)} year(s)",
        show_lines=False,
    )
    summary.add_column("Year", justify="right", style="cyan")
    summary.add_column("Currency")
    summary.add_column("Months (sheet / db)", justify="right")
    summary.add_column("Total sheet", justify="right")
    summary.add_column("Total DB", justify="right")
    summary.add_column("Diff", justify="right")
    summary.add_column("Status")

    for r in results:
        ok = bool(r.get("ok"))
        if _is_error_payload(r):
            summary.add_row(
                str(r.get("year", "?")),
                "-",
                "-",
                "-",
                "-",
                "-",
                "[bold red]ERROR[/bold red]",
            )
            continue
        sheet_total = r.get("total_sheet_acc")
        db_total = r.get("total_db_acc")
        diff: object = ""
        if isinstance(sheet_total, int | float) and isinstance(db_total, int | float):
            diff = abs(sheet_total - db_total)
        summary.add_row(
            str(r.get("year", "?")),
            str(r.get("accounting_currency", "")),
            f"{r.get('months_in_sheet', 0)} / {r.get('months_in_db', 0)}",
            _fmt_amount(sheet_total),
            _fmt_amount(db_total),
            _fmt_amount(diff),
            _status_markup(ok),
        )
    console.print(summary)

    failing = [r for r in results if not r.get("ok")]
    if failing:
        console.print()
        console.print(
            f"[bold]Drill-down for {len(failing)} failing year(s):[/bold]",
        )
        for r in failing:
            console.print()
            render_single(r, stream=stream)


def exit_code_for_single(result: dict) -> int:
    return 0 if result.get("ok") else 1


def exit_code_for_batch(results: list[dict]) -> int:
    """0 iff every entry has ``ok=True``; 0 on an empty list."""
    return 0 if all(r.get("ok") for r in results) else 1


def print_json(payload: object, *, stream: TextIO | None = None) -> None:
    """Write the raw JSON payload back out (back-compat escape hatch)."""
    out = stream if stream is not None else sys.stdout
    out.write(json.dumps(payload, indent=2, ensure_ascii=False))
    out.write("\n")

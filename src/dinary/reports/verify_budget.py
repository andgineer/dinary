"""Rich renderer for ``verify_bootstrap_import`` / ``verify-bootstrap-import-all``.

Takes the pre-parsed dict (or list of dicts) that
:func:`dinary.imports.verify_equivalence.verify_bootstrap_import`
returns and renders it as a compact summary + drill-down tables.

This module is dev-only tooling: it lives inside ``dinary.reports``
(never imported by the FastAPI runtime) so depending on ``rich`` at
module level is safe — see the sibling note in
:mod:`dinary.reports.expenses`.
"""

import dataclasses
import json
import sys
from typing import TextIO

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

#: Keys we know live inside a ``verify_bootstrap_import`` payload.
#: Used by ``_looks_like_result`` to tell a single-year dict apart
#: from accidental data (e.g. an error wrapper). We purposefully
#: only require the set that is *always* present on the success
#: path; the diff-list keys are optional because the verifier omits
#: them on early-exit errors if that ever changes.
_REQUIRED_KEYS = frozenset({"year", "ok", "months_checked"})


def _looks_like_result(payload: object) -> bool:
    return isinstance(payload, dict) and _REQUIRED_KEYS.issubset(payload.keys())


@dataclasses.dataclass(frozen=True, slots=True)
class _DrillSpec:
    """Column layout for one of the four drill-down tables.

    Kept as data (not code) so the four drill-downs share the same
    render loop and the column set is declarative — new fields in
    the verifier payload only need one line added here.
    """

    key: str
    title: str
    columns: tuple[tuple[str, str], ...]  # (header, payload_field)


_DRILL_SPECS: tuple[_DrillSpec, ...] = (
    _DrillSpec(
        key="missing_rows",
        title="Missing in DB (sheet has it, DB does not)",
        columns=(
            ("Month", "month"),
            ("Sheet category", "sheet_category"),
            ("Sheet group", "sheet_group"),
            ("Sheet amount", "sheet_amount"),
        ),
    ),
    _DrillSpec(
        key="extra_rows",
        title="Extra in DB (DB has it, sheet does not)",
        columns=(
            ("Month", "month"),
            ("Sheet category", "sheet_category"),
            ("Sheet group", "sheet_group"),
            ("DB amount", "db_amount"),
        ),
    ),
    _DrillSpec(
        key="amount_diffs",
        title="Amount diffs (present in both, amounts differ)",
        columns=(
            ("Month", "month"),
            ("Sheet category", "sheet_category"),
            ("Sheet group", "sheet_group"),
            ("Sheet amount", "sheet_amount"),
            ("DB amount", "db_amount"),
        ),
    ),
    _DrillSpec(
        key="comment_diffs",
        title="Comment diffs (does not affect OK/FAIL status)",
        columns=(
            ("Month", "month"),
            ("Sheet category", "sheet_category"),
            ("Sheet group", "sheet_group"),
            ("Sheet comment", "sheet_comment"),
            ("DB comment", "db_comment"),
        ),
    ),
)


def _count(result: dict, key: str) -> int:
    value = result.get(key)
    return len(value) if isinstance(value, list) else 0


def _fmt_amount(value: object) -> str:
    """Format a numeric amount cell; pass non-numeric through as-is."""
    if isinstance(value, int | float):
        return f"{value:,.2f}"
    return str(value) if value is not None else ""


def _status_markup(ok: bool) -> str:
    return "[bold green]OK[/bold green]" if ok else "[bold red]FAIL[/bold red]"


def _render_drill(
    console: Console,
    result: dict,
    spec: _DrillSpec,
) -> None:
    """Render one drill-down table, or nothing if its payload is empty."""
    rows = result.get(spec.key) or []
    if not rows:
        return
    table = Table(title=f"{spec.title} — {len(rows)} row(s)", show_lines=False)
    for header, _ in spec.columns:
        if header.endswith(("amount", "Amount")):
            table.add_column(header, justify="right")
        elif header == "Month":
            table.add_column(header, justify="right", style="cyan")
        else:
            table.add_column(header)
    for row in rows:
        table.add_row(
            *(
                _fmt_amount(row.get(field)) if "amount" in field else str(row.get(field, ""))
                for _, field in spec.columns
            ),
        )
    console.print(table)


def render_single(
    result: dict,
    *,
    stream: TextIO | None = None,
) -> None:
    """Render a single-year verify result: summary panel + drill-downs.

    The summary counts never lie — they reflect whatever the
    verifier actually put in the payload — so a zero in, say,
    ``amount_diffs`` means "verifier confirmed equivalence for
    amounts", not "renderer skipped that column". Drill-down tables
    appear only for non-empty diff lists; an all-green year prints
    just the summary panel.
    """
    console = Console(file=stream)
    if not _looks_like_result(result):
        console.print(
            Panel(
                f"Unexpected payload shape: {result!r}",
                title="verify-bootstrap-import",
                border_style="red",
            ),
        )
        return

    year = result.get("year")
    ok = bool(result.get("ok"))
    summary_lines = [
        f"Status:         {_status_markup(ok)}",
        f"Months checked: {result.get('months_checked', 0)}",
        f"Missing rows:   {_count(result, 'missing_rows')}",
        f"Extra rows:     {_count(result, 'extra_rows')}",
        f"Amount diffs:   {_count(result, 'amount_diffs')}",
        f"Comment diffs:  {_count(result, 'comment_diffs')} "
        "[dim](informational — does not flip OK)[/dim]",
    ]
    console.print(
        Panel.fit(
            "\n".join(summary_lines),
            title=f"Verify bootstrap import — year {year}",
            border_style="green" if ok else "red",
        ),
    )
    for spec in _DRILL_SPECS:
        _render_drill(console, result, spec)


def render_batch(
    results: list[dict],
    *,
    stream: TextIO | None = None,
) -> None:
    """Render a multi-year verify result: one summary table then drill-downs.

    The summary table always lists every year (so the operator sees
    the full coordinated-reset picture at a glance); drill-down
    tables print only for failing years to keep the terminal
    focused on what actually needs attention.
    """
    console = Console(file=stream)
    if not results:
        console.print("[dim](no years to verify)[/dim]")
        return

    summary = Table(
        title=f"Bootstrap import equivalence — {len(results)} year(s)",
        show_lines=False,
    )
    summary.add_column("Year", justify="right", style="cyan")
    summary.add_column("Months", justify="right")
    summary.add_column("Missing", justify="right")
    summary.add_column("Extra", justify="right")
    summary.add_column("Amount", justify="right")
    summary.add_column("Comments", justify="right")
    summary.add_column("Status")

    for r in results:
        ok = bool(r.get("ok"))
        missing = _count(r, "missing_rows")
        extra = _count(r, "extra_rows")
        amount = _count(r, "amount_diffs")
        comments = _count(r, "comment_diffs")
        summary.add_row(
            str(r.get("year", "?")),
            str(r.get("months_checked", 0)),
            f"[red]{missing}[/red]" if missing else "0",
            f"[red]{extra}[/red]" if extra else "0",
            f"[red]{amount}[/red]" if amount else "0",
            f"[yellow]{comments}[/yellow]" if comments else "0",
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
    """Return 0 iff ``result.ok`` is truthy, else 1."""
    return 0 if result.get("ok") else 1


def exit_code_for_batch(results: list[dict]) -> int:
    """Return 0 iff every entry in *results* has ``ok=True``, else 1.

    Defensive: an empty list returns 0 (nothing to verify = nothing
    broken), matching the remote-side ``all(r["ok"] for r in [])``
    semantics we previously had.
    """
    return 0 if all(r.get("ok") for r in results) else 1


def print_json(payload: object, *, stream: TextIO | None = None) -> None:
    """Write the raw JSON payload back out (back-compat escape hatch)."""
    out = stream if stream is not None else sys.stdout
    out.write(json.dumps(payload, indent=2, ensure_ascii=False))
    out.write("\n")

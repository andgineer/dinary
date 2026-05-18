"""Import and verify tasks."""

import json as _json
import sys
from datetime import datetime as _dt

from invoke import task

from tasks.imports import report_2d_3d as _report_2d_3d_module
from tasks.reports import verify_budget, verify_income
from tasks.ssh_utils import remote_snapshot_cmd, ssh_capture_bytes, ssh_json, ssh_run


def _require_yes(yes: bool, message: str) -> bool:
    """Gate destructive tasks behind an explicit `--yes` flag.

    Exits non-zero on the missing-flag path so CI / scripts cannot mistake a
    skipped destructive action for success. Returns True when allowed to
    proceed (so the call site reads naturally as `if not _require_yes(...)`).
    """
    if yes:
        return True
    print(message)
    print("Re-run with --yes to confirm. Aborting.")
    sys.exit(1)


def _coerce_year(year) -> int:
    """Validate and coerce a CLI `year` string to int.

    Tasks ssh `f"...({year})..."` into a remote Python invocation, so
    accepting only digits prevents shell-string injection via `--year=`.
    """
    if not year:
        return _dt.now().year
    try:
        return int(year)
    except (TypeError, ValueError):
        print(f"--year must be an integer, got {year!r}")
        sys.exit(1)


def _require_year(year) -> int:
    """Like `_coerce_year` but refuses a blank value.

    Use for destructive tasks (`import-budget`, `import-income`) where
    silently defaulting to the current year would be a footgun: an
    operator who forgets `--year=2024` would otherwise wipe the
    most-active year by accident.
    """
    if not year:
        print(
            "--year is required for destructive tasks (e.g. --year=2024).\n"
            "Refusing to default to the current year.",
        )
        sys.exit(1)
    return _coerce_year(year)


@task(name="import-catalog")
def import_catalog(c, yes=False):
    """Re-seed taxonomy from Google Sheets without touching expense/income data. Requires --yes."""
    if not _require_yes(
        yes,
        "WARNING: import-catalog will re-sync the taxonomy from Google Sheets. "
        "Existing ledger data (expenses, tags, sheet logging queue, income) is "
        "preserved. Vocabulary not present in the seed files will be marked "
        "inactive. The ``import_mapping`` table will be rebuilt. PWA clients "
        "will be forced to refresh on next /api/catalog call.",
    ):
        return
    ssh_run(
        c,
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
        "from tasks.imports.seed import rebuild_config_from_sheets; "
        "import json; print(json.dumps(rebuild_config_from_sheets()))'",
    )


@task(name="import-budget")
def import_budget(c, year="", yes=False):
    """DESTRUCTIVE: Delete and re-import expenses for --year from Google Sheet. Requires --yes."""
    year_int = _require_year(year)
    if not _require_yes(
        yes,
        f"WARNING: import-budget will DELETE every expense for {year_int} in "
        "data/dinary.db and re-import from the Google Sheet. Other years "
        "are untouched. There is no manual-vs-import distinction anymore; "
        "nothing in the targeted year is preserved.",
    ):
        return
    ssh_run(
        c,
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
        "from tasks.imports.expense_import import import_year; "
        f"import json; print(json.dumps(import_year({year_int})))'",
    )


@task(name="import-budget-all")
def import_budget_all(c, yes=False):
    """DESTRUCTIVE: Re-import expenses for all years from Google Sheets. Requires --yes.

    See https://andgineer.github.io/dinary/operations for the full coordinated reset flow.
    """
    if not _require_yes(
        yes,
        "WARNING: import-budget-all will DELETE every expense row for every "
        "year registered in .deploy/import_sources.json and re-import each year "
        "from the Google Sheet. Other ledger data (income, sheet logging queue) "
        "is preserved; within each affected year nothing is preserved.",
    ):
        return
    ssh_run(
        c,
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
        "from dinary.config import read_import_sources; "
        "from tasks.imports.expense_import import import_year; "
        "import json; "
        "years = sorted(r.year for r in read_import_sources() if r.year > 0); "
        '[print(json.dumps({"year": y, **import_year(y)})) for y in years]\'',
    )


@task(name="import-verify-bootstrap")
def verify_bootstrap_import(c, year="", json=False):  # noqa: A002
    """Verify server expense DB matches Google Sheet for --year. Exits non-zero on diffs.

    --json for raw output.
    """
    year_int = _require_year(year)
    result = ssh_json(
        c,
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
        "from tasks.imports.verify_equivalence import verify_bootstrap_import; "
        f"import json; print(json.dumps(verify_bootstrap_import({year_int}), "
        "indent=2, ensure_ascii=False))'",
    )
    if json:
        verify_budget.print_json(result)
    else:
        verify_budget.render_single(result)
    sys.exit(verify_budget.exit_code_for_single(result))


@task(name="import-verify-bootstrap-all")
def verify_bootstrap_import_all(c, json=False):  # noqa: A002
    """Verify server expenses match Google Sheets for every registered year. --json for raw output."""
    results = ssh_json(
        c,
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
        "from dinary.config import read_import_sources; "
        "from tasks.imports.verify_equivalence import verify_bootstrap_import; "
        "import json; "
        "years = sorted(r.year for r in read_import_sources() if r.year > 0); "
        'results = [{**verify_bootstrap_import(y), "year": y} for y in years]; '
        "print(json.dumps(results, indent=2, ensure_ascii=False))'",
    )
    if json:
        verify_budget.print_json(results)
    else:
        verify_budget.render_batch(results)
    sys.exit(verify_budget.exit_code_for_batch(results))


@task(name="import-income")
def import_income(c, year="", yes=False):
    """DESTRUCTIVE: Wipe and re-import income for a single year from Google Sheet."""
    year_int = _require_year(year)
    if not _require_yes(
        yes,
        f"WARNING: import-income will DELETE every income row for {year_int} and re-import "
        "from Google Sheets.",
    ):
        return
    ssh_run(
        c,
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
        "from tasks.imports.income_import import import_year_income; "
        f"import json; print(json.dumps(import_year_income({year_int})))'",
    )


@task(name="import-income-all")
def import_income_all(c, yes=False):
    """DESTRUCTIVE: Re-import income for all years with a registered income worksheet."""
    if not _require_yes(
        yes,
        "WARNING: import-income-all will re-import income for EVERY year with a "
        "registered income source. Existing income rows are dropped first.",
    ):
        return
    ssh_run(
        c,
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
        "from dinary.config import read_import_sources; "
        "from tasks.imports.income_import import import_year_income; "
        "import json; "
        "years = sorted("
        "r.year for r in read_import_sources() "
        "if r.year > 0 and r.income_worksheet_name"
        "); "
        "[print(json.dumps(import_year_income(y))) for y in years]'",
    )


@task(name="import-verify-income")
def verify_income_equivalence(c, year="", json=False):  # noqa: A002
    """Verify server income matches Google Sheet for --year. Exits non-zero on diffs.

    --json for raw output.
    """
    year_int = _require_year(year)
    result = ssh_json(
        c,
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
        "from tasks.imports.verify_income import verify_income_equivalence; "
        f"import json; print(json.dumps(verify_income_equivalence({year_int}), "
        "indent=2, ensure_ascii=False))'",
    )
    if json:
        verify_income.print_json(result)
    else:
        verify_income.render_single(result)
    sys.exit(verify_income.exit_code_for_single(result))


@task(name="import-verify-income-all")
def verify_income_equivalence_all(c, json=False):  # noqa: A002
    """Verify server income matches Google Sheets for every income year. --json for raw output."""
    results = ssh_json(
        c,
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
        "from dinary.config import read_import_sources; "
        "from tasks.imports.verify_income import verify_income_equivalence; "
        "import json; "
        "years = sorted("
        "r.year for r in read_import_sources() "
        "if r.year > 0 and r.income_worksheet_name"
        "); "
        'results = [{**verify_income_equivalence(y), "year": y} for y in years]; '
        "print(json.dumps(results, indent=2, ensure_ascii=False))'",
    )
    if json:
        verify_income.print_json(results)
    else:
        verify_income.render_batch(results)
    sys.exit(verify_income.exit_code_for_batch(results))


def _render_2d3d_locally(raw: bytes, *, as_csv: bool) -> None:
    """Render a JSON envelope produced by ``tasks.imports.report_2d_3d --json``.

    The envelope carries both the row shape discriminator (``detail``)
    and the column order, so the caller doesn't need to know whether
    summary or detail rows came back.
    """
    payload = _json.loads(raw.decode("utf-8"))
    rows = _report_2d_3d_module.rows_from_json(payload)
    columns = payload["columns"]
    if as_csv:
        _report_2d_3d_module.render_csv(rows, columns, output=sys.stdout)
    else:
        _report_2d_3d_module.render_rich(rows, columns, output=sys.stdout)


@task(name="import-report-2d-3d")
def import_report_2d_3d(
    c,
    detail=False,
    csv=False,  # noqa: A002
    json=False,
    year="",
    remote=False,
):
    """Show 2D→3D category resolution report over the imported sheets.

    Flags: --detail, --csv, --json, --year Y, --remote.
    """
    if csv and json:
        print("--csv and --json are mutually exclusive", file=sys.stderr)
        sys.exit(1)

    def _build_flags(*, force_json: bool) -> list[str]:
        flags: list[str] = []
        if detail:
            flags.append("--detail")
        if force_json or json:
            flags.append("--json")
        elif csv:
            flags.append("--csv")
        if year:
            flags.extend(["--year", str(int(year))])
        return flags

    if not remote:
        cmd = "uv run python -m tasks.imports.report_2d_3d"
        local_flags = _build_flags(force_json=False)
        if local_flags:
            cmd = f"{cmd} {' '.join(local_flags)}"
        c.run(cmd)
        return

    raw = ssh_capture_bytes(
        remote_snapshot_cmd("tasks.imports.report_2d_3d", _build_flags(force_json=True)),
    )

    if json:
        sys.stdout.buffer.write(raw)
        return

    _render_2d3d_locally(raw, as_csv=csv)

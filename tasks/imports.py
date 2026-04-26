"""Import and verify tasks."""

import json as _json
import sys
from datetime import datetime as _dt

from invoke import task

from dinary.imports import report_2d_3d as _report_2d_3d_module
from dinary.reports import verify_budget, verify_income

from .reports import remote_snapshot_cmd
from .ssh_utils import ssh_capture_bytes, ssh_json, ssh_run


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
    """FK-safe in-place catalog sync: re-seed taxonomy without deleting the DB.

    Never deletes ``data/dinary.db``. Ledger tables
    (``expenses``/``expense_tags``/``sheet_logging_jobs``/``income``)
    stay intact; catalog rows are upserted by natural key so existing
    integer ids are preserved. Vocabulary no longer present in the
    seed files is marked ``is_active=FALSE``. The ``import_mapping``
    table is rebuilt from scratch against the current active taxonomy
    ids; the runtime ``sheet_mapping`` table is owned by the
    ``map`` worksheet tab and is not touched here.

    Bumps ``app_metadata.catalog_version`` by +1 only when the
    rebuild observably changed the catalog (hash-gated); the PWA
    picks up the new value via ``GET /api/catalog`` and
    ``POST /api/expenses``.
    """
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
        "from dinary.imports.seed import rebuild_config_from_sheets; "
        "import json; print(json.dumps(rebuild_config_from_sheets()))'",
    )


@task(name="import-budget")
def import_budget(c, year="", yes=False):
    """DESTRUCTIVE: Delete every expense for the given year and re-import from Sheet.

    Operates on the single ``data/dinary.db`` file: the year's rows in
    ``expenses``/``expense_tags``/``sheet_logging_jobs`` are removed and
    re-imported from Google Sheets. Other years stay untouched.
    """
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
        "from dinary.imports.expense_import import import_year; "
        f"import json; print(json.dumps(import_year({year_int})))'",
    )


@task(name="import-budget-all")
def import_budget_all(c, yes=False):
    """DESTRUCTIVE: Re-import every year registered in ``.deploy/import_sources.json``.

    Iterates positive-year entries from the server-side
    ``.deploy/import_sources.json`` (in ascending order) and
    re-imports each one via ``import_year``, which deletes just that
    year's expense rows before re-importing. Intended for use inside
    the coordinated reset flow after ``import-catalog --yes``.
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
        "from dinary.imports.expense_import import import_year; "
        "import json; "
        "years = sorted(r.year for r in read_import_sources() if r.year > 0); "
        '[print(json.dumps({"year": y, **import_year(y)})) for y in years]\'',
    )


@task(name="import-verify-bootstrap")
def verify_bootstrap_import(c, year="", json=False):  # noqa: A002
    """Verify that bootstrap-imported budget DB reproduces sheet aggregates (on server).

    Renders a rich summary panel with drill-down tables for
    missing / extra / amount / comment diffs. Exits non-zero iff
    ``ok=False`` on the verifier payload. Pass ``--json`` to emit
    the raw ``json.dumps(..., indent=2)`` blob instead of the rich
    view (back-compat for scripted consumers).
    """
    year_int = _require_year(year)
    result = ssh_json(
        c,
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
        "from dinary.imports.verify_equivalence import verify_bootstrap_import; "
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
    """Run import-verify-bootstrap for every positive-year entry in ``.deploy/import_sources.json``.

    Used by the coordinated reset flow so verification covers every rebuilt
    year, not just the current calendar year. Renders a rich summary
    table across all years with per-failing-year drill-downs. Exits
    non-zero if any single year fails. Pass ``--json`` to emit the
    raw JSON array (back-compat for scripted consumers).
    """
    results = ssh_json(
        c,
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
        "from dinary.config import read_import_sources; "
        "from dinary.imports.verify_equivalence import verify_bootstrap_import; "
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
        "from dinary.imports.income_import import import_year_income; "
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
        "from dinary.imports.income_import import import_year_income; "
        "import json; "
        "years = sorted("
        "r.year for r in read_import_sources() "
        "if r.year > 0 and r.income_worksheet_name"
        "); "
        "[print(json.dumps(import_year_income(y))) for y in years]'",
    )


@task(name="import-verify-income")
def verify_income_equivalence(c, year="", json=False):  # noqa: A002
    """Verify that imported income matches the source Google Sheet (on server).

    Renders a rich summary panel with per-month diff table when
    diffs are present. Exits non-zero on ``ok=False`` (including
    the early-exit error branches, e.g. missing ``import_sources``
    entry). Pass ``--json`` for the raw JSON escape hatch.
    """
    year_int = _require_year(year)
    result = ssh_json(
        c,
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
        "from dinary.imports.verify_income import verify_income_equivalence; "
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
    """Run import-verify-income for every year that has an income worksheet.

    Used by the coordinated reset flow so verification covers every rebuilt
    income year. Renders a rich summary table with per-failing-year
    drill-downs. Exits non-zero if any single year fails. Pass
    ``--json`` for the raw JSON array (back-compat for scripted
    consumers).
    """
    results = ssh_json(
        c,
        "cd ~/dinary && source ~/.local/bin/env && uv run python -c '"
        "from dinary.config import read_import_sources; "
        "from dinary.imports.verify_income import verify_income_equivalence; "
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
    """Render a JSON envelope produced by ``dinary.imports.report_2d_3d --json``.

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
def import_report_2d_3d(  # noqa: PLR0913
    c,
    detail=False,
    csv=False,  # noqa: A002
    json=False,
    year="",
    remote=False,
):
    """Show the 2D->3D resolution report over the imported sheets.

    Flags (all optional):
        --detail   per-row output instead of aggregated summary
        --csv      emit CSV to stdout instead of a rich table
        --json     emit a JSON envelope to stdout (mutex with --csv)
        --year Y   restrict to a single calendar year
        --remote   run the report on the server over SSH (default:
                   runs locally against ``data/dinary.db``)

    ``--remote`` uses the same JSON-over-SSH transport as
    ``inv report-*`` (see :func:`_run_report_module`): the server
    always emits ``--json`` against a consistent SQLite snapshot of
    the live DB and the local process renders. That is the only way
    to keep Cyrillic / box-drawing glyphs intact across the SSH
    pipe.
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
        cmd = "uv run python -m dinary.imports.report_2d_3d"
        local_flags = _build_flags(force_json=False)
        if local_flags:
            cmd = f"{cmd} {' '.join(local_flags)}"
        c.run(cmd)
        return

    raw = ssh_capture_bytes(
        remote_snapshot_cmd("dinary.imports.report_2d_3d", _build_flags(force_json=True)),
    )

    if json:
        sys.stdout.buffer.write(raw)
        return

    _render_2d3d_locally(raw, as_csv=csv)

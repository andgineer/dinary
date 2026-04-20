"""Optional sheet logging: append expense rows to Google Sheets.

Enabled when ``DINARY_SHEET_LOGGING_SPREADSHEET`` is set (spreadsheet ID
or full browser URL). Disabled (silent no-op) when empty.

Single hot path: drain ``sheet_logging_jobs`` for one expense at a time,
use the dedicated ``logging_mapping`` table (year-agnostic, 3D→2D) to
pick ``(sheet_category, sheet_group)``, then append the row into the
**first visible worksheet** of the configured spreadsheet. If no mapping
exists for a category, the category name itself is used as a guaranteed
fallback.

There is no full-month rebuild and no DB-to-sheet reconciliation. The
historical sheets are read once during bootstrap import and then become
append-only.

Two callers run the same ``_drain_one_job`` codepath:

  1. fire-and-forget task scheduled by ``POST /api/expenses``
     (opportunistic fast path that hides Sheets latency from the client);
  2. ``inv drain-logging`` CLI (sweep-everything-pending; recovers
     anything the async worker missed because of a process crash,
     network blip, etc.).

Both authenticate via the same gspread client and share the
``sheet_logging_jobs`` claim/release semantics so two workers cannot
double-append the same expense row.

Idempotency: each appended row carries an opaque audit trail in column
J of the form ``"[exp:<expense_id>] [exp:<expense_id>] …"``. Before
extending the formula in column B, ``append_expense_atomic`` reads
column J and skips the write if our marker is already there. This
closes the "Sheets API call succeeded but the response was lost to a
timeout" hole that would otherwise turn a single ``inv drain-logging``
retry into a duplicate amount.
"""

import asyncio
import enum
import logging
from datetime import date
from decimal import Decimal

import gspread

from dinary.config import settings, spreadsheet_id_from_setting
from dinary.services import duckdb_repo
from dinary.services.exchange_rate import fetch_eur_rsd_rate
from dinary.services.sheets import (
    COL_RATE_EUR,
    append_expense_atomic,
    ensure_category_row,
    fetch_row_years,
    find_month_range,
    get_month_rate,
    get_sheet,
)

logger = logging.getLogger(__name__)


def get_logging_spreadsheet_id() -> str | None:
    """Return the configured spreadsheet ID or ``None`` if logging is disabled."""
    return spreadsheet_id_from_setting(settings.sheet_logging_spreadsheet)


def is_sheet_logging_enabled() -> bool:
    """Return whether runtime sheet logging is enabled."""
    return get_logging_spreadsheet_id() is not None


class DrainResult(enum.Enum):
    """Outcome of `_drain_one_job`.

    A bool was insufficient because the post-append claim-stolen recovery
    path genuinely succeeded at the side effect (the Sheets row is there)
    but cannot be reported as plain success: a duplicate row was likely
    written by the claim thief, and the operator needs to know that
    distinct from a clean append. The orphan-queue-row case is also
    distinct from APPENDED — no Sheets I/O happened and counting it as
    "appended" would inflate that operational metric.
    """

    APPENDED = "appended"
    """Sheets append succeeded, queue row cleared by our own claim_token."""

    ALREADY_LOGGED = "already_logged"
    """The idempotency marker for this ``expense_id`` was already on the
    target row, so we skipped the duplicate write and just cleared the
    queue row. This is the "timeout-after-success on a previous attempt"
    recovery path — no Sheets write happened on this attempt, but the
    expense IS recorded in the sheet."""

    FAILED = "failed"
    """Sheets append failed (or never happened); queue row stays pending
    for the next sweep, no Sheets side effect."""

    RECOVERED_WITH_DUPLICATE = "recovered_with_duplicate"
    """Sheets append succeeded but our claim was stolen before we could
    clear the queue row; we force-deleted to prevent a *third* append.
    The idempotency marker we wrote means the next worker will detect
    the duplicate and skip it — but if the claim thief was already past
    its own marker check before we wrote ours, it will still produce
    a second sheet row. Audit and dedupe."""

    NOOP_ORPHAN = "noop_orphan"
    """Queue row pointed at a non-existent expense (orphan from a manual
    DELETE or partial rebuild). Queue row was cleared (or left for the
    next sweep if our claim was stolen mid-clear). No Sheets I/O — must
    not be counted as `appended`."""


# ---------------------------------------------------------------------------
# Hot path: one expense → one sheet row append
# ---------------------------------------------------------------------------


def schedule_logging(expense_id: str, year: int) -> None:
    """Fire-and-forget background drain for `expense_id`.

    No-op when ``DINARY_SHEET_LOGGING_SPREADSHEET`` is unset. Returns
    immediately; errors are logged and the queue row stays for the next
    sweep.
    """
    if not is_sheet_logging_enabled():
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_async_drain_one(expense_id, year))
    except RuntimeError:
        logger.info(
            "No running event loop; expense %s queued for next `inv drain-logging` sweep",
            expense_id,
        )


async def _async_drain_one(expense_id: str, year: int) -> None:
    try:
        await asyncio.to_thread(_drain_one_job, expense_id, year)
    except Exception:
        logger.exception("Background drain failed for expense %s (year=%d)", expense_id, year)


def _drain_one_job(  # noqa: C901, PLR0911, PLR0912, PLR0915
    expense_id: str,
    year: int,
    *,
    spreadsheet_id: str | None = None,
) -> DrainResult:
    """Atomically claim, append, and clear one queue row.

    The complexity ruff lints (C901/PLR0912/PLR0915) are silenced
    deliberately: this function is the linear choreography of a
    single-row drain — claim → look up expense → logging-project →
    append → clear. Each branch is one of the documented `DrainResult`
    states and splitting them into helpers would scatter the
    state-machine across files without making any single piece
    easier to reason about. See the docstring for the canonical
    state list.

    Returns:
      * `DrainResult.APPENDED`: clean success — Sheets row written and
        queue row deleted by our own claim_token.
      * `DrainResult.FAILED`: nothing was written (or write errored);
        queue row stays `pending` for the next sweep. Includes the
        unclaimable case (peer worker holds the claim) and the
        logging-projection-miss case (unknown ``category_id``).
      * `DrainResult.RECOVERED_WITH_DUPLICATE`: Sheets append succeeded
        but our claim was stolen before we could clear; we force-deleted
        the queue row to prevent a *third* append. Audit the sheet to
        dedupe the row the thief wrote.
      * `DrainResult.NOOP_ORPHAN`: queue row pointed at a non-existent
        expense (orphan from a manual DELETE or a partial rebuild). No
        Sheets I/O happened; queue row was cleared (or left for the
        next sweep if our claim was stolen mid-clear). Distinct from
        APPENDED so the operational `appended` counter only reflects
        actual sheet writes.

    ``spreadsheet_id`` is pre-resolved by ``drain_pending`` once per
    sweep so the env-var is read only once. When called as a one-shot
    (the ``schedule_logging`` background path), we resolve here from
    settings.
    """
    if spreadsheet_id is None:
        spreadsheet_id = get_logging_spreadsheet_id()
    # Defensive: both callers already gate on logging being enabled, so
    # this is a soft-fail safety net (queue row stays for next sweep)
    # rather than an `assert` that would crash the background worker.
    if spreadsheet_id is None:
        return DrainResult.FAILED

    con = duckdb_repo.get_budget_connection(year)
    claim_token: str | None = None
    try:
        try:
            claim_token = duckdb_repo.claim_logging_job(con, expense_id)
            if claim_token is None:
                logger.debug("Job %s not claimable (gone or claimed elsewhere)", expense_id)
                return DrainResult.FAILED

            expense = duckdb_repo.get_expense_by_id(con, expense_id)
            if expense is None:
                logger.warning("Queue row for missing expense %s; clearing", expense_id)
                # If a stale-claim sweep stole the row between our claim
                # above and this clear, `cleared` will be False and the
                # next sweep will retry. Report NOOP_ORPHAN regardless —
                # from this worker's perspective the row no longer needs
                # any work, and there was no Sheets side effect either
                # way, so no dedupe risk. Distinct from APPENDED so the
                # `appended` counter only reflects real Sheets writes.
                cleared = duckdb_repo.clear_logging_job(con, expense_id, claim_token)
                if not cleared:
                    logger.warning(
                        "clear_logging_job for missing expense %s reported no row "
                        "removed (claim may have been stolen); leaving it for "
                        "the next sweep",
                        expense_id,
                    )
                return DrainResult.NOOP_ORPHAN

            tag_ids = duckdb_repo.get_expense_tags(con, expense_id)

            config_con = duckdb_repo.get_config_connection(read_only=True)
            try:
                projection = duckdb_repo.logging_projection(
                    config_con,
                    category_id=expense.category_id,
                    event_id=expense.event_id,
                    tag_ids=tag_ids,
                )
                if projection is None:
                    cat_name = duckdb_repo.get_category_name(
                        config_con,
                        expense.category_id,
                    )
                    if cat_name is None:
                        logger.error(
                            "Logging projection: unknown category_id=%d for expense %s",
                            expense.category_id,
                            expense_id,
                        )
                        duckdb_repo.release_logging_claim(
                            con,
                            expense_id,
                            claim_token,
                        )
                        return DrainResult.FAILED
                    projection = (cat_name, "")
            finally:
                config_con.close()

            sheet_category, sheet_group = projection
        except Exception:
            if claim_token is not None:
                try:
                    duckdb_repo.release_logging_claim(con, expense_id, claim_token)
                except Exception:
                    logger.exception("Failed to release claim for %s", expense_id)
            raise

        # Sheet I/O happens outside the DuckDB transaction so a slow Sheets
        # request can't hold the budget DB lock.
        try:
            rate = _fetch_rate_blocking(expense.datetime.date())
            wrote_new_row = _append_row_to_sheet(
                spreadsheet_id=spreadsheet_id,
                expense_id=expense_id,
                month=expense.datetime.month,
                sheet_category=sheet_category,
                sheet_group=sheet_group,
                amount=float(expense.amount_original),
                comment=expense.comment or "",
                expense_date=expense.datetime.date(),
                rate=rate,
            )
            cleared = duckdb_repo.clear_logging_job(con, expense_id, claim_token)
            if not cleared:
                # We appended to Sheets but our token-protected DELETE
                # found nothing to delete. There are two ways into this
                # branch:
                #
                #   (a) Stolen claim: a stale-claim sweep (or peer worker)
                #       reclaimed the row between our append and this
                #       DELETE. The thief either already did, or will,
                #       perform a *second* sheet append — that duplicate
                #       is unrecoverable here.
                #
                #   (b) Operator wipe: someone ran `DELETE FROM
                #       sheet_logging_jobs ...` (or `inv import-budget`)
                #       while we were appending. No thief, no duplicate;
                #       just a lost queue row our token would have
                #       removed.
                #
                # We can't tell (a) from (b) here without more state, so
                # we surface BOTH as RECOVERED_WITH_DUPLICATE. A false
                # positive in case (b) costs the operator a sheet audit;
                # a false negative in case (a) would silently leak a
                # duplicate. The safe direction is to over-warn.
                #
                # Force-delete by `expense_id` only — the claim_token
                # check is what just failed, and we have ground truth
                # that the append succeeded. Logging the deleted-count
                # lets the operator distinguish "we cleaned up after a
                # thief" (deleted=True) from "row was already gone"
                # (deleted=False), which weakly hints at (b).
                deleted = duckdb_repo.force_clear_logging_job(con, expense_id)
                if deleted:
                    logger.error(
                        "Append succeeded for %s but clear_logging_job lost the "
                        "token race; force-deleted the queue row to prevent "
                        "a third append. A duplicate sheet row was probably "
                        "written by the claim thief — audit and dedupe.",
                        expense_id,
                    )
                else:
                    logger.warning(
                        "Append succeeded for %s but the queue row was "
                        "already gone when we tried to clear it (operator "
                        "wipe, or thief already cleared). No third append "
                        "is possible. If a thief actually cleared, a "
                        "duplicate sheet row exists — audit to confirm.",
                        expense_id,
                    )
                return DrainResult.RECOVERED_WITH_DUPLICATE
            return DrainResult.APPENDED if wrote_new_row else DrainResult.ALREADY_LOGGED
        except Exception:
            logger.exception("Append to sheet failed for expense %s", expense_id)
            try:
                duckdb_repo.release_logging_claim(con, expense_id, claim_token)
            except Exception:
                logger.exception("Failed to release claim for %s", expense_id)
            return DrainResult.FAILED
    finally:
        # Single close path: every early `return` and every exception now
        # routes through here, eliminating the FD leak that earlier nested
        # `try/finally` layers missed on the not-claimable / missing-expense
        # / no-target branches.
        con.close()


def _fetch_rate_blocking(expense_date: date) -> Decimal | None:
    try:
        return asyncio.run(fetch_eur_rsd_rate(expense_date.replace(day=1)))
    except (OSError, ValueError):
        return None


def _append_row_to_sheet(  # noqa: PLR0913
    *,
    spreadsheet_id: str,
    expense_id: str,
    month: int,
    sheet_category: str,
    sheet_group: str,
    amount: float,
    comment: str,
    expense_date: date,
    rate: Decimal | None,
) -> bool:
    """Project one expense onto Google Sheets. Idempotent per ``expense_id``.

    The row write is gated by an idempotency marker stored in column J
    (see ``append_expense_atomic``). If a previous attempt for this
    ``expense_id`` already reached Sheets — even if the response was
    lost to a timeout and the queue row went back to ``pending`` — this
    call detects the existing marker and skips the formula/comment
    update, leaving the queue row clearable upstream.

    The rate update is intentionally outside the atomic write: it is
    "set if missing" and re-running it with the same value is a no-op
    in Sheets, so duplication is harmless.

    Year-aware matching: column G stores month 1..12 only, so without
    extra context January 2026 and January 2027 collapse to the same
    block — a 2027 expense would land on a 2026 row. We pull column A
    unformatted via ``fetch_row_years`` (Google shows it as "Apr-1",
    but the underlying value is a date serial) and pass the resulting
    year-per-row list into ``ensure_category_row`` /
    ``find_month_range`` / ``get_month_rate`` so all matching is
    constrained to the correct year.

    Cost: one extra column-A ``batch_get`` per drained expense on top
    of the existing ``get_all_values`` traffic. Sheets' default
    60-reads/min quota stays comfortable: ``inv drain-logging`` is a
    low-frequency catch-up sweep, and the inline ``schedule_logging``
    fast path runs once per ``POST /api/expenses``.

    Year-list maintenance after insert: when ``ensure_category_row``
    inserts a row, ``all_values`` grows by one and the original
    ``years_by_row`` is one short and shifted at the insert site. We
    splice ``expense_date.year`` into the year list at index
    ``row - 1``, mirroring exactly what ``insert_logging_row`` wrote
    to column A. Without this the next ``find_month_range`` would
    either miss the freshly inserted block (silently skipping the rate
    write) or pick a now-shifted row whose year doesn't match —
    overwriting another year's rate.
    """
    ss = get_sheet(spreadsheet_id)
    ws = ss.sheet1
    all_values = ws.get_all_values()
    years_by_row = fetch_row_years(ws, len(all_values))
    # ``fetch_row_years`` pads to ``len(all_values)``; assert the
    # contract here so a future bug in the helper trips the test
    # suite instead of silently wildcarding rows.
    assert len(years_by_row) == len(all_values), (
        "fetch_row_years contract: returned list must align with all_values"
    )
    target_year = expense_date.year
    rows_before_insert = len(all_values)

    row, all_values = ensure_category_row(
        ws,
        all_values,
        month,
        sheet_category,
        sheet_group,
        expense_date,
        years_by_row=years_by_row,
    )

    if len(all_values) > rows_before_insert:
        # ``ensure_category_row`` only ever inserts a single row at
        # ``row``; splice the corresponding year so the post-insert
        # ``years_by_row`` realigns with ``all_values`` 1:1. See the
        # docstring for the corruption case this prevents.
        years_by_row = years_by_row[: row - 1] + [target_year] + years_by_row[row - 1 :]
        assert len(years_by_row) == len(all_values), (
            "post-splice invariant: years_by_row must realign with all_values"
        )

    rate_str = get_month_rate(
        all_values,
        month,
        target_year=target_year,
        years_by_row=years_by_row,
    )
    if not rate_str and rate:
        month_range = find_month_range(
            all_values,
            month,
            target_year=target_year,
            years_by_row=years_by_row,
        )
        if month_range:
            ws.update_cell(month_range[0], COL_RATE_EUR, str(rate))

    written = append_expense_atomic(
        ws,
        row,
        expense_id=expense_id,
        amount_rsd=amount,
        comment=comment,
    )

    if written:
        logger.info(
            "Appended +%s for %s/%s in %d-%02d (expense %s)",
            amount,
            sheet_category,
            sheet_group,
            expense_date.year,
            month,
            expense_id,
        )
    else:
        logger.info(
            "Skipped duplicate append for %s/%s in %d-%02d (expense %s, marker present)",
            sheet_category,
            sheet_group,
            expense_date.year,
            month,
            expense_id,
        )
    return written


# ---------------------------------------------------------------------------
# `inv drain-logging` driver: sweep every yearly DB and drain its queue
# ---------------------------------------------------------------------------


def drain_pending() -> dict:  # noqa: C901, PLR0912
    """Drain every `sheet_logging_jobs` row across all `budget_*.duckdb` files.

    Side effect of the per-year `get_budget_connection(year)` call below:
    each year DB we touch lazily applies any pending budget migrations
    (yoyo-managed). That's how the post-deploy `0002_rename_sheet_sync_jobs`
    rename actually lands on production year DBs — `inv drain-logging`
    visits each year and `init_budget_db(year)` migrates it on the way.

    Returns ``{"disabled": True}`` immediately when
    ``DINARY_SHEET_LOGGING_SPREADSHEET`` is not set.

    Otherwise returns a summary dict with cross-year totals:

      * `years`: number of `budget_*.duckdb` files we visited.
      * `attempted`: number of queue rows we tried to drain (NOT
        "successfully claimed" — an unclaimable row still bumps this).
      * `appended`: clean Sheets append + queue clear.
      * `already_logged`: idempotency marker found, write skipped, queue
        cleared. Means a previous attempt reached Sheets even if its
        response was lost.
      * `failed`: nothing was written (or write errored); queue row
        stays `pending` for the next sweep.
      * `recovered_with_duplicate`: Sheets append succeeded but our
        claim was stolen before we could clear; force-deleted queue row.
      * `noop_orphan`: queue row pointed at a non-existent expense.
    """
    spreadsheet_id = get_logging_spreadsheet_id()
    if spreadsheet_id is None:
        return {"disabled": True}

    summary = {
        "years": 0,
        "attempted": 0,
        "appended": 0,
        "already_logged": 0,
        "failed": 0,
        "recovered_with_duplicate": 0,
        "noop_orphan": 0,
    }
    data_dir = duckdb_repo.DATA_DIR
    if not data_dir.exists():
        return summary

    for db_path in sorted(data_dir.glob("budget_*.duckdb")):
        stem = db_path.stem
        try:
            year = int(stem.replace("budget_", ""))
        except ValueError:
            continue
        summary["years"] += 1

        con = duckdb_repo.get_budget_connection(year)
        try:
            expense_ids = duckdb_repo.list_logging_jobs(con)
        finally:
            con.close()

        for expense_id in expense_ids:
            summary["attempted"] += 1
            try:
                outcome = _drain_one_job(expense_id, year, spreadsheet_id=spreadsheet_id)
            except gspread.exceptions.GSpreadException:
                logger.exception("Sheets error draining expense %s", expense_id)
                summary["failed"] += 1
                continue
            except Exception:
                logger.exception("Unexpected error draining expense %s", expense_id)
                summary["failed"] += 1
                continue
            if outcome is DrainResult.APPENDED:
                summary["appended"] += 1
            elif outcome is DrainResult.ALREADY_LOGGED:
                summary["already_logged"] += 1
            elif outcome is DrainResult.RECOVERED_WITH_DUPLICATE:
                summary["recovered_with_duplicate"] += 1
            elif outcome is DrainResult.NOOP_ORPHAN:
                summary["noop_orphan"] += 1
            else:
                summary["failed"] += 1

    return summary

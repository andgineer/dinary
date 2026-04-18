"""Google Sheets export-only sync.

Single hot path: drain `sheet_sync_jobs` for one expense at a time, use
forward projection to pick `(sheet_category, sheet_group)` in the latest
configured sheet year, then append the row.

There is no full-month rebuild and no DB-to-sheet reconciliation. The
historical sheets are read once during bootstrap import and then become
append-only.

Two callers run the same `_drain_one_job` codepath:

  1. fire-and-forget task scheduled by `POST /api/expenses` (opportunistic
     fast path that hides Sheets latency from the client);
  2. `inv sync` CLI (sweep-everything-pending; recovers anything the
     async worker missed because of a process crash, network blip, etc.).

Both authenticate via the same gspread client and share the
`sheet_sync_jobs` claim/release semantics so two workers cannot
double-append the same expense row.
"""

import asyncio
import dataclasses
import enum
import logging
import threading
import time
from datetime import date
from decimal import Decimal

import gspread

from dinary.services import duckdb_repo
from dinary.services.exchange_rate import fetch_eur_rsd_rate
from dinary.services.sheets import (
    COL_RATE_EUR,
    append_comment,
    append_to_rsd_formula,
    create_month_rows,
    find_category_row,
    find_month_range,
    get_month_rate,
    get_sheet,
)

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, slots=True)
class ExportTarget:
    """Resolved (latest_sheet_year, spreadsheet_id, worksheet_name) tuple.

    Atomic so callers cannot pass a half-resolved set of fields and silently
    fall through the "any-None -> re-resolve" path in `_drain_one_job`.
    """

    latest_year: int
    spreadsheet_id: str
    worksheet_name: str


class DrainResult(enum.Enum):
    """Outcome of `_drain_one_job`.

    A bool was insufficient because the post-append claim-stolen recovery
    path (Bug NN) genuinely succeeded at the side effect (the Sheets row
    is there) but cannot be reported as plain success: a duplicate row
    was likely written by the claim thief, and the operator needs to know
    that distinct from a clean append. The orphan-queue-row case is also
    distinct from APPENDED — no Sheets I/O happened and counting it as
    "appended" would inflate that operational metric.
    """

    APPENDED = "appended"
    """Sheets append succeeded, queue row cleared by our own claim_token."""

    FAILED = "failed"
    """Sheets append failed (or never happened); queue row stays pending
    for the next sweep, no Sheets side effect."""

    RECOVERED_WITH_DUPLICATE = "recovered_with_duplicate"
    """Sheets append succeeded but our claim was stolen before we could
    clear the queue row; we force-deleted to prevent a *third* append.
    The thief either already wrote (or will write) a second row — audit
    the sheet to dedupe."""

    NOOP_ORPHAN = "noop_orphan"
    """Queue row pointed at a non-existent expense (orphan from a manual
    DELETE or partial rebuild). Queue row was cleared (or left for the
    next sweep if our claim was stolen mid-clear). No Sheets I/O — must
    not be counted as `appended`."""


# TTL cache so the per-POST `schedule_sync` fast path doesn't re-query
# `sheet_import_sources` for every write. The cache is intentionally short
# because `inv rebuild-catalog` runs out-of-process (over SSH) and cannot
# invalidate this in-memory cache, so we rely on the TTL to pick up new
# values within a bounded delay. Setting it to 0 (or calling
# `invalidate_export_target_cache`) forces a re-resolve on the next call.
#
# The lock guards the read-then-write pattern in `get_export_target` so a
# future caller that runs `_drain_one_job` from a `ThreadPoolExecutor`
# (plausible if Sheets latency becomes a bottleneck) cannot observe a
# torn `(timestamp, ExportTarget)` tuple. Today the only callers are
# event-loop tasks and the single-threaded `inv sync` driver, but the
# lock is cheap and the failure mode without it is silent.
_EXPORT_TARGET_TTL_SECONDS = 60.0


class _ExportTargetCache:
    """Mutable holder for the TTL-cached `ExportTarget`.

    Wrapping the cache in a class instance (rather than a bare module
    global) lets us mutate `entry` without `global` declarations at
    every call site — pylint's PLW0603 flags `global` for good reason
    (it's the most common source of "why is this changing under me?"
    bugs), and the cleanest way to silence it without disabling the
    rule is to give the mutable state an attribute home.
    """

    __slots__ = ("entry", "lock")

    def __init__(self) -> None:
        self.entry: tuple[float, ExportTarget] | None = None
        self.lock = threading.Lock()


_EXPORT_TARGET_CACHE = _ExportTargetCache()


# ---------------------------------------------------------------------------
# Hot path: one expense → one sheet row append
# ---------------------------------------------------------------------------


def schedule_sync(expense_id: str, year: int) -> None:
    """Fire-and-forget background drain for `expense_id`.

    Returns immediately; errors are logged and the queue row stays for the
    next sweep. When called from a sync context (no running event loop, e.g.
    a test client or `inv` shell wrapper), logs at INFO so the operator
    knows the row is waiting for the next `inv sync` sweep instead of
    silently disappearing into a `debug` log line.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_async_drain_one(expense_id, year))
    except RuntimeError:
        logger.info(
            "No running event loop; expense %s queued for next `inv sync` sweep",
            expense_id,
        )


async def _async_drain_one(expense_id: str, year: int) -> None:
    try:
        await asyncio.to_thread(_drain_one_job, expense_id, year)
    except Exception:
        logger.exception("Background drain failed for expense %s (year=%d)", expense_id, year)


def _resolve_export_target(latest_export_year: int) -> ExportTarget:
    """Build an `ExportTarget` from the row at `latest_export_year`.

    Returns the dataclass directly (not a tuple) so the caller never has
    to re-pack positional fields — the tuple shape was an attractive
    nuisance for "did latest_year line up with spreadsheet_id?" bugs.
    """
    src = duckdb_repo.get_import_source(latest_export_year)
    if src is None or not src.spreadsheet_id or not src.worksheet_name:
        msg = (
            f"Latest export year {latest_export_year} is not a valid sheet target; "
            "fix sheet_import_sources and re-run rebuild-catalog."
        )
        raise RuntimeError(msg)
    return ExportTarget(
        latest_year=latest_export_year,
        spreadsheet_id=src.spreadsheet_id,
        worksheet_name=src.worksheet_name,
    )


def _latest_export_year() -> int:
    config_con = duckdb_repo.get_config_connection(read_only=True)
    try:
        row = config_con.execute(
            "SELECT MAX(year) FROM sheet_import_sources WHERE year > 0",
        ).fetchone()
    finally:
        config_con.close()
    if not row or row[0] is None:
        msg = "No positive year in sheet_import_sources; cannot determine export target."
        raise RuntimeError(msg)
    return int(row[0])


def get_export_target(*, force_refresh: bool = False) -> ExportTarget:
    """Resolve the current export target, with a short in-process TTL cache.

    `schedule_sync` calls this on every POST, so without caching every
    write would round-trip to `sheet_import_sources` twice (once for
    MAX(year), once for the row). The cache is invalidated on RuntimeError
    so a misconfigured sheet doesn't get pinned for the full TTL window;
    callers can also pass `force_refresh=True`.

    Concurrency model (K6): the lock guards each cache read and each
    cache write, but is *intentionally released* around the
    `_resolve_export_target` DB roundtrip so a slow resolve cannot
    serialize unrelated POSTs. Consequence: this function is NOT a
    singleflight — N concurrent first-callers (e.g. server restart
    before TTL warms, then a flurry of POSTs) will all run the DB
    roundtrip and all publish identical results back. Correctness is
    preserved (last writer wins; every value is the same), but the
    cache offers no thundering-herd protection for the cold case. If
    that ever becomes a hot-path issue, the standard fix is a per-key
    `Future` in a lock-guarded dict so only one resolver runs.
    """
    cache = _EXPORT_TARGET_CACHE
    with cache.lock:
        now = time.monotonic()
        if (
            not force_refresh
            and cache.entry is not None
            and now - cache.entry[0] < _EXPORT_TARGET_TTL_SECONDS
        ):
            return cache.entry[1]
    # Drop the lock around the DB roundtrip so a slow `_resolve_export_target`
    # cannot block other readers; we re-acquire to publish the result.
    try:
        latest_year = _latest_export_year()
        target = _resolve_export_target(latest_year)
    except RuntimeError:
        # Drop any stale entry so the next caller re-resolves immediately
        # instead of waiting for the TTL to expire on a known-broken value.
        with cache.lock:
            cache.entry = None
        raise
    with cache.lock:
        cache.entry = (time.monotonic(), target)
    return target


def invalidate_export_target_cache() -> None:
    """Force the next `get_export_target` call to re-resolve from DB.

    Public because tests and operator scripts may need to clear the
    in-memory cache without restarting the server (e.g. after running
    `inv rebuild-catalog` against the running process).
    """
    cache = _EXPORT_TARGET_CACHE
    with cache.lock:
        cache.entry = None


def _drain_one_job(  # noqa: C901, PLR0912, PLR0915
    expense_id: str,
    year: int,
    *,
    target: ExportTarget | None = None,
) -> DrainResult:
    """Atomically claim, append, and clear one queue row.

    The complexity ruff lints (C901/PLR0912/PLR0915) are silenced
    deliberately: this function is the linear choreography of a
    single-row drain — claim → look up expense → forward-project →
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
        forward-projection-misses case (no `sheet_mapping` row for
        `(category_id, latest_year)`).
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

    `target` is pre-resolved by `sync_pending` once per sweep so a
    misconfigured `sheet_import_sources` doesn't produce N copies of the
    same RuntimeError in the journal. When called as a one-shot (the
    `schedule_sync` background path), we resolve here via `get_export_target`.
    """
    if target is None:
        target = get_export_target()
    latest_year = target.latest_year
    spreadsheet_id = target.spreadsheet_id
    worksheet_name = target.worksheet_name

    con = duckdb_repo.get_budget_connection(year)
    claim_token: str | None = None
    try:
        try:
            claim_token = duckdb_repo.claim_sync_job(con, expense_id)
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
                cleared = duckdb_repo.clear_sync_job(con, expense_id, claim_token)
                if not cleared:
                    logger.warning(
                        "clear_sync_job for missing expense %s reported no row "
                        "removed (claim may have been stolen); leaving it for "
                        "the next sweep",
                        expense_id,
                    )
                return DrainResult.NOOP_ORPHAN

            tag_ids = duckdb_repo.get_expense_tags(con, expense_id)

            config_con = duckdb_repo.get_config_connection(read_only=True)
            try:
                projection = duckdb_repo.forward_projection(
                    config_con,
                    latest_sheet_year=latest_year,
                    category_id=expense.category_id,
                    event_id=expense.event_id,
                    tag_ids=tag_ids,
                )
            finally:
                config_con.close()

            if projection is None:
                logger.error(
                    "Forward projection returned no target for expense %s "
                    "(category_id=%d, event_id=%s, tags=%s)",
                    expense_id,
                    expense.category_id,
                    expense.event_id,
                    tag_ids,
                )
                duckdb_repo.release_sync_claim(con, expense_id, claim_token)
                return DrainResult.FAILED

            sheet_category, sheet_group = projection
        except Exception:
            if claim_token is not None:
                try:
                    duckdb_repo.release_sync_claim(con, expense_id, claim_token)
                except Exception:
                    logger.exception("Failed to release claim for %s", expense_id)
            raise

        # Sheet I/O happens outside the DuckDB transaction so a slow Sheets
        # request can't hold the budget DB lock.
        try:
            rate = _fetch_rate_blocking(expense.datetime.date())
            _append_row_to_sheet(
                spreadsheet_id=spreadsheet_id,
                worksheet_name=worksheet_name,
                month=expense.datetime.month,
                sheet_category=sheet_category,
                sheet_group=sheet_group,
                amount=float(expense.amount_original),
                comment=expense.comment or "",
                expense_date=expense.datetime.date(),
                rate=rate,
            )
            cleared = duckdb_repo.clear_sync_job(con, expense_id, claim_token)
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
                #       sheet_sync_jobs ...` (or `inv rebuild-budget`)
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
                deleted = duckdb_repo.force_clear_sync_job(con, expense_id)
                if deleted:
                    logger.error(
                        "Append succeeded for %s but clear_sync_job lost the "
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
            return DrainResult.APPENDED
        except Exception:
            logger.exception("Append to sheet failed for expense %s", expense_id)
            try:
                duckdb_repo.release_sync_claim(con, expense_id, claim_token)
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
    worksheet_name: str,
    month: int,
    sheet_category: str,
    sheet_group: str,
    amount: float,
    comment: str,
    expense_date: date,
    rate: Decimal | None,
) -> None:
    ss = get_sheet(spreadsheet_id)
    ws = ss.worksheet(worksheet_name) if worksheet_name else ss.sheet1
    all_values = ws.get_all_values()

    month_range = find_month_range(all_values, month)
    if month_range is None:
        create_month_rows(ws, all_values, expense_date)
        all_values = ws.get_all_values()
        month_range = find_month_range(all_values, month)
        if month_range is None:
            msg = f"Failed to create month block for {expense_date.year}-{month:02d}"
            raise RuntimeError(msg)

    rate_str = get_month_rate(all_values, month)
    if not rate_str and rate:
        ws.update_cell(month_range[0], COL_RATE_EUR, str(rate))

    row = find_category_row(all_values, month, sheet_category, sheet_group)
    if row is None:
        msg = (
            f"Sheet has no row for ({sheet_category!r}, {sheet_group!r}) "
            f"in month {month}; latest sheet must contain every category that "
            "forward projection might pick. Add the row to the sheet template."
        )
        raise RuntimeError(msg)

    append_to_rsd_formula(ws, row, amount)

    if comment:
        row_data = all_values[row - 1]
        append_comment(ws, row, row_data, comment)

    logger.info(
        "Appended +%s for %s/%s in %d-%02d",
        amount,
        sheet_category,
        sheet_group,
        expense_date.year,
        month,
    )


# ---------------------------------------------------------------------------
# `inv sync` driver: sweep every yearly DB and drain its queue
# ---------------------------------------------------------------------------


def sync_pending() -> dict:
    """Drain every `sheet_sync_jobs` row across all `budget_*.duckdb` files.

    Returns a summary dict with cross-year totals:

      * `years`: number of `budget_*.duckdb` files we visited.
      * `attempted`: number of queue rows we tried to drain (NOT
        "successfully claimed" — an unclaimable row still bumps this).
      * `appended`: clean Sheets append + queue clear.
      * `failed`: nothing was written (or write errored); queue row
        stays `pending` for the next sweep. Operator action: typically
        none — let the next sweep retry. Also covers the unclaimable
        and forward-projection-miss cases (see `_drain_one_job`).
      * `recovered_with_duplicate`: Sheets append succeeded but our
        claim was stolen before we could clear; we force-deleted the
        queue row to prevent a *third* append. Operator action: audit
        the sheet for the affected expense_id and dedupe the row the
        thief wrote.
      * `noop_orphan`: queue row pointed at a non-existent expense
        (orphan from a manual DELETE or partial rebuild). No Sheets
        I/O. Operator action: none — but a non-zero count indicates
        someone is editing budget DBs out of band.
    """
    summary = {
        "years": 0,
        "attempted": 0,
        "appended": 0,
        "failed": 0,
        "recovered_with_duplicate": 0,
        "noop_orphan": 0,
    }
    data_dir = duckdb_repo.DATA_DIR
    if not data_dir.exists():
        return summary

    # Resolve the export target once for the whole sweep. If
    # `sheet_import_sources` is misconfigured the sweep aborts here with a
    # single, useful exception instead of logging the same error per
    # pending row across every yearly DB. Force a refresh because a sweep
    # via `inv sync` is the natural place to pick up an out-of-process
    # `inv rebuild-catalog` change without waiting for the TTL.
    target = get_export_target(force_refresh=True)

    for db_path in sorted(data_dir.glob("budget_*.duckdb")):
        stem = db_path.stem
        try:
            year = int(stem.replace("budget_", ""))
        except ValueError:
            continue
        summary["years"] += 1

        con = duckdb_repo.get_budget_connection(year)
        try:
            expense_ids = duckdb_repo.list_sync_jobs(con)
        finally:
            con.close()

        for expense_id in expense_ids:
            # `attempted` (was `claimed` pre-K8): increments before
            # `_drain_one_job` even tries to acquire the queue claim.
            # An unclaimable row (peer worker holds it) still bumps this
            # — the right semantic is "rows we walked", not "claims we
            # acquired", and the name now reflects that.
            summary["attempted"] += 1
            try:
                outcome = _drain_one_job(expense_id, year, target=target)
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
            elif outcome is DrainResult.RECOVERED_WITH_DUPLICATE:
                summary["recovered_with_duplicate"] += 1
            elif outcome is DrainResult.NOOP_ORPHAN:
                summary["noop_orphan"] += 1
            else:
                summary["failed"] += 1

    return summary

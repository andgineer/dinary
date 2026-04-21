"""Optional sheet logging: append expense rows to Google Sheets.

Enabled when ``DINARY_SHEET_LOGGING_SPREADSHEET`` is set. Disabled
(silent no-op) when empty. Single writer: periodic ``drain_pending``.

Circuit breaker: transient Sheets errors halt the sweep with
exponential backoff (60s -> 30min cap). Permanent errors mark individual
queue rows as ``poisoned`` and continue.
"""

import asyncio
import enum
import logging
import time
from datetime import datetime, timedelta

import gspread

from dinary.config import settings, spreadsheet_id_from_setting
from dinary.services import duckdb_repo, sheet_mapping
from dinary.services.nbs import get_rate
from dinary.services.sheets import (
    append_expense_atomic,
    ensure_category_row,
    fetch_row_years,
    get_sheet,
)

logger = logging.getLogger(__name__)

_backoff_until: datetime | None = None
_current_backoff_sec: float = 0.0
_BACKOFF_INITIAL_SEC = 60.0
_BACKOFF_MAX_SEC = 1800.0

_HTTP_CLIENT_ERROR_MIN = 400
_HTTP_CLIENT_ERROR_MAX = 499

# Wake-up channel: the lifespan drain loop registers an asyncio.Event
# and its loop reference at startup; producers (e.g. POST /api/expenses)
# call `notify_new_work` after committing a fresh ledger row so the
# drain runs immediately instead of waiting for the next periodic tick.
# The periodic timer stays as the canonical fallback for process
# restarts and for crash-recovery of claims left by a previous worker.
_wake_event: asyncio.Event | None = None
_wake_loop: asyncio.AbstractEventLoop | None = None


def get_logging_spreadsheet_id() -> str | None:
    return spreadsheet_id_from_setting(settings.sheet_logging_spreadsheet)


def is_sheet_logging_enabled() -> bool:
    return get_logging_spreadsheet_id() is not None


def register_wake_channel(
    event: asyncio.Event,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Register the drain loop's wake-up event and its owning event loop.

    Called exactly once per process by the lifespan drain loop. The
    reference stays alive for the lifetime of the loop; on shutdown
    the drain loop calls `clear_wake_channel` so stale `notify_new_work`
    calls from a parallel test cannot touch a closed loop.
    """
    global _wake_event, _wake_loop  # noqa: PLW0603
    _wake_event = event
    _wake_loop = loop


def clear_wake_channel() -> None:
    """Detach the wake-up channel (lifespan shutdown / tests teardown)."""
    global _wake_event, _wake_loop  # noqa: PLW0603
    _wake_event = None
    _wake_loop = None


def notify_new_work() -> None:
    """Signal the drain loop to start its next sweep immediately.

    Thread-safe: safe to call from the event loop thread, from an
    `asyncio.to_thread` worker, or from a regular sync context. If no
    drain loop has registered (logging disabled, tests, shutdown),
    this is a silent no-op — the periodic timer remains the canonical
    wakeup source, so a missed notify never silently loses work.
    """
    ev = _wake_event
    loop = _wake_loop
    if ev is None or loop is None or loop.is_closed():
        return
    try:
        loop.call_soon_threadsafe(ev.set)
    except RuntimeError:
        # Loop finished between the `is_closed` check and the schedule
        # call. Dropping the notify is safe: the next lifespan startup
        # will sweep any enqueued jobs on its first iteration.
        return


class DrainResult(enum.Enum):
    APPENDED = "appended"
    ALREADY_LOGGED = "already_logged"
    FAILED = "failed"
    RECOVERED_WITH_DUPLICATE = "recovered_with_duplicate"
    NOOP_ORPHAN = "noop_orphan"
    POISONED = "poisoned"


def _is_transient(exc: Exception) -> bool:
    """Return True for errors that should trigger the circuit breaker backoff."""
    if isinstance(exc, gspread.exceptions.APIError):
        code = getattr(exc, "code", None) or getattr(
            getattr(exc, "response", None),
            "status_code",
            500,
        )
        return not (_HTTP_CLIENT_ERROR_MIN <= int(code) <= _HTTP_CLIENT_ERROR_MAX)
    return isinstance(exc, ConnectionError | TimeoutError | OSError)


def _activate_backoff() -> None:
    global _backoff_until, _current_backoff_sec  # noqa: PLW0603
    if _current_backoff_sec == 0:
        _current_backoff_sec = _BACKOFF_INITIAL_SEC
    else:
        _current_backoff_sec = min(_current_backoff_sec * 2, _BACKOFF_MAX_SEC)
    _backoff_until = datetime.now() + timedelta(seconds=_current_backoff_sec)
    logger.warning("Circuit breaker: backoff for %.0fs", _current_backoff_sec)


def _reset_backoff() -> None:
    global _backoff_until, _current_backoff_sec  # noqa: PLW0603
    _backoff_until = None
    _current_backoff_sec = 0.0


# ---------------------------------------------------------------------------
# Single-job drain
# ---------------------------------------------------------------------------


def _drain_one_job(  # noqa: C901, PLR0911, PLR0912, PLR0915
    expense_pk: int,
    *,
    spreadsheet_id: str,
) -> DrainResult:
    """Atomically claim, append, and clear one queue row."""
    con = duckdb_repo.get_connection()
    claim_token: str | None = None
    try:
        try:
            claim_token = duckdb_repo.claim_logging_job(con, expense_pk)
            if claim_token is None:
                return DrainResult.FAILED

            expense = duckdb_repo.get_expense_by_id(con, expense_pk)
            if expense is None:
                logger.warning("Queue row for missing expense pk=%d; clearing", expense_pk)
                duckdb_repo.clear_logging_job(con, expense_pk, claim_token)
                return DrainResult.NOOP_ORPHAN

            # The J-marker contract is "UUID of the last expense appended
            # to this row". Only the bootstrap importer inserts rows with
            # ``client_expense_id=NULL``, and it explicitly passes
            # ``enqueue_logging=False``, so reaching the drain with a
            # NULL UUID means a runtime row was created with a broken
            # producer. Poison the queue row rather than silently writing
            # a synthetic non-UUID marker that would poison future
            # duplicate detection on the sheet. Checked before the
            # logging-projection lookup so we don't waste two SELECTs
            # resolving a row we're about to poison.
            if expense.client_expense_id is None:
                logger.error(
                    "Queue row for pk=%d has no client_expense_id; "
                    "poisoning (runtime rows must carry a UUID)",
                    expense_pk,
                )
                duckdb_repo.poison_logging_job(
                    con,
                    expense_pk,
                    f"Runtime expense pk={expense_pk} has no client_expense_id",
                )
                return DrainResult.POISONED

            marker_key = expense.client_expense_id

            tag_ids = duckdb_repo.get_expense_tags(con, expense_pk)

            projection = duckdb_repo.logging_projection(
                con,
                category_id=expense.category_id,
                event_id=expense.event_id,
                tag_ids=tag_ids,
            )
            if projection is None:
                # ``logging_projection`` only returns ``None`` when the
                # expense points at an unknown ``category_id`` — it
                # otherwise applies the category-name / empty-group
                # fallback per column itself. An unknown category is
                # unrecoverable at drain time (we cannot invent a
                # landing cell), so poison the job and let the operator
                # fix the catalog upstream.
                logger.error(
                    "Logging projection: unknown category_id=%d for expense pk=%d",
                    expense.category_id,
                    expense_pk,
                )
                duckdb_repo.poison_logging_job(
                    con,
                    expense_pk,
                    f"No sheet_mapping fallback possible for category_id={expense.category_id}",
                )
                return DrainResult.POISONED

            sheet_category, sheet_group = projection
        except Exception:
            if claim_token is not None:
                try:
                    duckdb_repo.release_logging_claim(con, expense_pk, claim_token)
                except Exception:
                    logger.exception("Failed to release claim for pk=%d", expense_pk)
            raise

        # Fetch EUR/RSD exchange rate for column H. Column B stores the
        # app-currency (RSD) amount, column C is the EUR projection via
        # =B/H, so H must be the RSD-per-1-EUR rate for the expense date
        # regardless of what currency the expense was originally in.
        # Reuse the already-open cursor ``con`` — the singleton engine
        # allows unlimited cursors on the same connection, so opening a
        # second one here was just noise.
        expense_date = expense.datetime.date()
        rate_str: str | None = None
        try:
            rate = get_rate(con, expense_date, "EUR")
            rate_str = str(rate)
        except (ValueError, OSError):
            rate_str = None

        try:
            wrote_new_row = _append_row_to_sheet(
                spreadsheet_id=spreadsheet_id,
                expense_pk=expense_pk,
                marker_key=marker_key,
                month=expense.datetime.month,
                sheet_category=sheet_category,
                sheet_group=sheet_group,
                amount=float(expense.amount),
                comment=expense.comment or "",
                expense_date=expense_date,
                rate=rate_str,
            )
            cleared = duckdb_repo.clear_logging_job(con, expense_pk, claim_token)
            if not cleared:
                deleted = duckdb_repo.force_clear_logging_job(con, expense_pk)
                if deleted:
                    logger.error(
                        "Append succeeded for pk=%d but claim stolen; force-deleted",
                        expense_pk,
                    )
                return DrainResult.RECOVERED_WITH_DUPLICATE
            return DrainResult.APPENDED if wrote_new_row else DrainResult.ALREADY_LOGGED
        except Exception:
            logger.exception("Append to sheet failed for expense pk=%d", expense_pk)
            try:
                duckdb_repo.release_logging_claim(con, expense_pk, claim_token)
            except Exception:
                logger.exception("Failed to release claim for pk=%d", expense_pk)
            raise
    finally:
        con.close()


def _append_row_to_sheet(  # noqa: PLR0913
    *,
    spreadsheet_id: str,
    expense_pk: int,
    marker_key: str,
    month: int,
    sheet_category: str,
    sheet_group: str,
    amount: float,
    comment: str,
    expense_date,
    rate: str | None,
) -> bool:
    """Project one expense onto Google Sheets.

    ``marker_key`` is written verbatim into column J (last-key-only
    idempotency). Callers pass the expense's ``client_expense_id`` so
    the sheet J cell matches the PWA-generated UUID on every runtime
    row.
    """
    ss = get_sheet(spreadsheet_id)
    ws = ss.sheet1
    all_values = ws.get_all_values()
    years_by_row = fetch_row_years(ws, len(all_values))
    target_year = expense_date.year

    row, all_values = ensure_category_row(
        ws,
        all_values,
        month,
        sheet_category,
        sheet_group,
        expense_date,
        years_by_row=years_by_row,
        rate=rate,
    )

    if len(all_values) > len(years_by_row):
        years_by_row = years_by_row[: row - 1] + [target_year] + years_by_row[row - 1 :]

    written = append_expense_atomic(
        ws,
        row,
        marker_key=marker_key,
        amount_rsd=amount,
        comment=comment,
        rate=rate,
    )

    if written:
        logger.info(
            "Appended +%s for %s/%s in %d-%02d (pk=%d)",
            amount,
            sheet_category,
            sheet_group,
            expense_date.year,
            month,
            expense_pk,
        )
    else:
        logger.info(
            "Skipped duplicate for %s/%s in %d-%02d (pk=%d, marker present)",
            sheet_category,
            sheet_group,
            expense_date.year,
            month,
            expense_pk,
        )
    return written


# ---------------------------------------------------------------------------
# Periodic drain
# ---------------------------------------------------------------------------


def drain_pending() -> dict:  # noqa: C901, PLR0912, PLR0915
    """Drain ``sheet_logging_jobs`` from the single dinary.duckdb."""
    spreadsheet_id = get_logging_spreadsheet_id()
    if spreadsheet_id is None:
        return {"disabled": True}

    now = datetime.now()
    if _backoff_until is not None and now < _backoff_until:
        return {"backoff_active": True}

    summary: dict = {
        "attempted": 0,
        "appended": 0,
        "already_logged": 0,
        "failed": 0,
        "recovered_with_duplicate": 0,
        "noop_orphan": 0,
        "poisoned": 0,
    }

    con = duckdb_repo.get_connection()
    try:
        expense_pks = duckdb_repo.list_logging_jobs(con)
    finally:
        con.close()

    if not expense_pks:
        _reset_backoff()
        return summary

    # Lazy sheet-mapping refresh: cheap modifiedTime check via Drive API;
    # only reparses the map tab when it actually changed. Failures here
    # downgrade to a warning and we drain with the cached mapping.
    sheet_mapping.ensure_fresh()

    attempts = 0
    cap_reached = False
    max_attempts = settings.sheet_logging_drain_max_attempts_per_iteration
    delay = settings.sheet_logging_drain_inter_row_delay_sec

    for expense_pk in expense_pks:
        if attempts >= max_attempts:
            cap_reached = True
            break
        if delay > 0 and attempts > 0:
            time.sleep(delay)
        summary["attempted"] += 1
        try:
            outcome = _drain_one_job(expense_pk, spreadsheet_id=spreadsheet_id)
        except Exception as exc:
            logger.exception("Error draining expense pk=%d", expense_pk)
            if _is_transient(exc):
                _activate_backoff()
                summary["failed"] += 1
                summary["cap_reached"] = cap_reached
                return summary
            con2 = duckdb_repo.get_connection()
            try:
                duckdb_repo.poison_logging_job(
                    con2,
                    expense_pk,
                    f"{type(exc).__name__}: {exc}",
                )
            finally:
                con2.close()
            summary["poisoned"] += 1
            attempts += 1
            continue

        if outcome is DrainResult.APPENDED:
            summary["appended"] += 1
        elif outcome is DrainResult.ALREADY_LOGGED:
            summary["already_logged"] += 1
        elif outcome is DrainResult.RECOVERED_WITH_DUPLICATE:
            summary["recovered_with_duplicate"] += 1
        elif outcome is DrainResult.NOOP_ORPHAN:
            summary["noop_orphan"] += 1
        elif outcome is DrainResult.POISONED:
            summary["poisoned"] += 1
        else:
            summary["failed"] += 1
        attempts += 1

    if not cap_reached:
        _reset_backoff()

    summary["cap_reached"] = cap_reached
    return summary

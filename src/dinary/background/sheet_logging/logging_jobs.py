"""Sheet-logging job queue helpers.

All functions accept an open ``sqlite3.Connection`` (from ``db.get_connection()``).
"""

import logging
import sqlite3
import uuid
from datetime import datetime

from dinary.db.storage import best_effort_rollback, default_claim_stale_timeout

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# sheet_logging_jobs queue helpers (integer PK based)
# ---------------------------------------------------------------------------


def list_logging_jobs(
    con: sqlite3.Connection,
    *,
    now: datetime | None = None,
    stale_before: datetime | None = None,
) -> list[int]:
    """Pending rows plus stale ``in_progress`` orphans (crashed worker). Fresh
    ``in_progress`` rows are excluded — ``claim_logging_job`` would just reject
    them, burning a BEGIN/COMMIT round-trip for nothing."""
    if now is None:
        now = datetime.now()
    if stale_before is None:
        stale_before = now - default_claim_stale_timeout()
    rows = con.execute(
        "SELECT expense_id FROM sheet_logging_jobs"
        " WHERE status = 'pending'"
        "    OR (status = 'in_progress' AND claimed_at < ?)"
        " ORDER BY expense_id",
        [stale_before],
    ).fetchall()
    return [int(r[0]) for r in rows]


def count_logging_jobs(con: sqlite3.Connection) -> int:
    row = con.execute("SELECT count(*) FROM sheet_logging_jobs").fetchone()
    return int(row[0]) if row else 0


def claim_logging_job(
    con: sqlite3.Connection,
    expense_id: int,
    *,
    claim_token: str | None = None,
    now: datetime | None = None,
    stale_before: datetime | None = None,
) -> str | None:
    """Atomically claim a queue row. Returns the claim_token on success, None otherwise."""
    if claim_token is None:
        claim_token = uuid.uuid4().hex
    if now is None:
        now = datetime.now()
    if stale_before is None:
        stale_before = now - default_claim_stale_timeout()

    # Serializes on the write lock so two workers can't both win the
    # SELECT-then-UPDATE race; a losing worker's OperationalError means
    # "another worker won".
    try:
        con.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError:
        logger.debug("BEGIN IMMEDIATE lost the write-lock race claiming %s", expense_id)
        return None
    try:
        row = con.execute(
            "SELECT status, claim_token, claimed_at FROM sheet_logging_jobs WHERE expense_id = ?",
            [expense_id],
        ).fetchone()
        if row is None:
            con.execute("COMMIT")
            return None
        status, _existing_token, claimed_at = row

        is_pending = status == "pending"
        is_stale = status == "in_progress" and claimed_at is not None and claimed_at < stale_before
        if not (is_pending or is_stale):
            con.execute("COMMIT")
            return None

        con.execute(
            "UPDATE sheet_logging_jobs SET status = 'in_progress',"
            " claim_token = ?, claimed_at = ? WHERE expense_id = ?",
            [claim_token, now, expense_id],
        )
        con.execute("COMMIT")
        return claim_token
    except sqlite3.OperationalError:
        best_effort_rollback(con, context=f"claim_logging_job({expense_id}) lock conflict")
        logger.debug("Lock conflict claiming %s; another worker won", expense_id)
        return None
    except Exception:
        best_effort_rollback(con, context=f"claim_logging_job({expense_id}) generic error")
        raise


def release_logging_claim(
    con: sqlite3.Connection,
    expense_id: int,
    claim_token: str,
) -> bool:
    rows = con.execute(
        "UPDATE sheet_logging_jobs SET status = 'pending', claim_token = NULL, claimed_at = NULL"
        " WHERE expense_id = ? AND claim_token = ? RETURNING expense_id",
        [expense_id, claim_token],
    ).fetchall()
    return len(rows) > 0


def poison_logging_job(
    con: sqlite3.Connection,
    expense_id: int,
    error: str,
) -> None:
    """Mark a queue row as poisoned with an error reason."""
    con.execute(
        "UPDATE sheet_logging_jobs SET status = 'poisoned', last_error = ? WHERE expense_id = ?",
        [error, expense_id],
    )


def _delete_logging_job(
    con: sqlite3.Connection,
    expense_id: int,
    *,
    claim_token: str | None,
) -> bool:
    if claim_token is None:
        rows = con.execute(
            "DELETE FROM sheet_logging_jobs WHERE expense_id = ? RETURNING expense_id",
            [expense_id],
        ).fetchall()
    else:
        rows = con.execute(
            "DELETE FROM sheet_logging_jobs WHERE expense_id = ? AND claim_token = ?"
            " RETURNING expense_id",
            [expense_id, claim_token],
        ).fetchall()
    return len(rows) > 0


def clear_logging_job(
    con: sqlite3.Connection,
    expense_id: int,
    claim_token: str,
) -> bool:
    return _delete_logging_job(con, expense_id, claim_token=claim_token)


def force_clear_logging_job(con: sqlite3.Connection, expense_id: int) -> bool:
    return _delete_logging_job(con, expense_id, claim_token=None)

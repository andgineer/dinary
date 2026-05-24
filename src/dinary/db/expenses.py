"""Expense insert, lookup, and query operations.

All functions accept an open ``sqlite3.Connection`` (from
``db.get_connection()``) and leave it open.  Callers are
responsible for opening and closing the connection.
"""

import dataclasses
import logging
import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Literal

from dinary.db.sql_loader import fetchall_as, fetchone_as, load_sql
from dinary.db.storage import best_effort_rollback, connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal SQLite race helpers
# ---------------------------------------------------------------------------

# Exception classes SQLite may raise when an ``ON CONFLICT DO NOTHING``
# can't silently absorb a duplicate.
_RACE_EXCS: tuple[type[sqlite3.Error], ...] = (sqlite3.IntegrityError,)


def _is_unique_violation_of_client_expense_id(exc: BaseException) -> bool:
    """True iff ``exc`` is a UNIQUE violation on ``expenses.client_expense_id``."""
    message = str(exc).lower()
    if "foreign key" in message:
        return False
    return "unique constraint failed" in message and "expenses.client_expense_id" in message


# ---------------------------------------------------------------------------
# Row types
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class ExpenseRow:
    id: int
    client_expense_id: str | None
    datetime: datetime
    amount: Decimal
    amount_original: Decimal
    currency_original: str
    category_id: int
    event_id: int | None
    comment: str | None
    sheet_category: str | None
    sheet_group: str | None


@dataclasses.dataclass(slots=True)
class ExistingExpenseRow:
    id: int
    amount: Decimal
    amount_original: Decimal
    currency_original: str
    category_id: int
    event_id: int | None
    comment: str | None
    datetime: datetime
    sheet_category: str | None
    sheet_group: str | None


# ---------------------------------------------------------------------------
# Expense payload and result types
# ---------------------------------------------------------------------------

InsertExpenseResult = Literal["created", "duplicate", "conflict"]


@dataclasses.dataclass(frozen=True)
class ExpensePayload:
    """All fields needed to insert or compare one expense row."""

    client_expense_id: str | None
    expense_datetime: datetime
    amount: float
    amount_original: float
    currency_original: str
    category_id: int
    event_id: int | None = None
    comment: str = ""
    sheet_category: str | None = None
    sheet_group: str | None = None
    tag_ids: list[int] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_EXPENSE_DIFF_FIELDS: tuple[str, ...] = (
    "amount",
    "amount_original",
    "currency_original",
    "category_id",
    "event_id",
    "comment",
    "datetime",
    "sheet_category",
    "sheet_group",
    "tag_ids",
)


def _format_expense_diff(stored: tuple, incoming: tuple) -> str:
    """Return a compact list of the columns that differ between stored + incoming."""
    diffs: list[str] = []
    for field, a, b in zip(_EXPENSE_DIFF_FIELDS, stored, incoming, strict=True):
        if a != b:
            diffs.append(f"{field}: stored={a!r} incoming={b!r}")
    return "; ".join(diffs) if diffs else "(no field difference observed)"


def _to_decimal(value: float | Decimal) -> Decimal:
    """Coerce amount-shaped inputs to Decimal for consistent storage."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _validate_expense_refs(
    con: sqlite3.Connection,
    category_id: int,
    event_id: int | None,
    tag_ids: list[int],
) -> None:
    """Validate FK refs inside an open transaction; raises ValueError if any ID is missing."""
    if not con.execute("SELECT 1 FROM categories WHERE id = ?", [category_id]).fetchone():
        raise ValueError(f"category_id {category_id} not found in categories")
    if (
        event_id is not None
        and not con.execute(
            "SELECT 1 FROM events WHERE id = ?",
            [event_id],
        ).fetchone()
    ):
        raise ValueError(f"event_id {event_id} not found in events")
    for tid in tag_ids:
        if not con.execute("SELECT 1 FROM tags WHERE id = ?", [tid]).fetchone():
            raise ValueError(f"tag_id {tid} not found in tags")


def _try_insert_expense_row(
    con: sqlite3.Connection,
    sql_params: list,
    client_expense_id: str | None,
) -> tuple[tuple | None, bool]:
    """Run INSERT ... ON CONFLICT DO NOTHING RETURNING, handling unique-key races.

    Returns ``(row, tx_rolled_back)``:
    - ``(row_tuple, False)`` — success; tx still open.
    - ``(None, False)``      — ON CONFLICT fired; tx still open, caller must ROLLBACK.
    - ``(None, True)``       — race exception absorbed; tx was rolled back by this function.
    """
    try:
        return con.execute(load_sql("insert_expense.sql"), sql_params).fetchone(), False
    except _RACE_EXCS as exc:
        if not _is_unique_violation_of_client_expense_id(exc):
            raise
        best_effort_rollback(
            con,
            context=(
                f"insert_expense race-recovery at INSERT (client_expense_id={client_expense_id!r})"
            ),
        )
        return None, True


def _commit_expense_row(
    con: sqlite3.Connection,
    expense_pk: int,
    tag_ids: list[int],
    enqueue_logging: bool,
    client_expense_id: str | None,
) -> bool:
    """Insert tags, optionally enqueue a logging job, and COMMIT.

    Returns True on success. Returns False if a race on COMMIT was absorbed
    (transaction has already been rolled back by this function).
    """
    for tid in tag_ids:
        con.execute(
            "INSERT INTO expense_tags (expense_id, tag_id) VALUES (?, ?)"
            " ON CONFLICT (expense_id, tag_id) DO NOTHING",
            [expense_pk, tid],
        )
    if enqueue_logging:
        enqueue_for_logging(con, expense_pk)
    try:
        con.execute("COMMIT")
        return True
    except _RACE_EXCS as exc:
        if not _is_unique_violation_of_client_expense_id(exc):
            raise
        best_effort_rollback(
            con,
            context=f"insert_expense race-recovery (client_expense_id={client_expense_id!r})",
        )
        return False


def _compare_with_stored(
    con: sqlite3.Connection,
    client_expense_id: str | None,
    incoming: tuple,
) -> InsertExpenseResult:
    """Load the committed winner row and compare to incoming payload.

    Returns 'duplicate' if all fields match, 'conflict' otherwise.
    """
    existing = fetchone_as(
        ExistingExpenseRow,
        con,
        load_sql("get_existing_expense.sql"),
        [client_expense_id],
    )
    if existing is None:
        msg = (
            f"insert_expense: client_expense_id={client_expense_id!r} "
            "disappeared between ON CONFLICT/race recovery and the "
            "compare SELECT — concurrent writer rolled back after "
            "its commit? DB state is inconsistent with our assumptions."
        )
        raise RuntimeError(msg)
    existing_tag_ids = sorted(
        int(r[0])
        for r in con.execute(
            "SELECT tag_id FROM expense_tags WHERE expense_id = ?",
            [existing.id],
        ).fetchall()
    )
    stored = (
        existing.amount,
        existing.amount_original,
        existing.currency_original,
        existing.category_id,
        existing.event_id,
        existing.comment,
        existing.datetime,
        existing.sheet_category,
        existing.sheet_group,
        existing_tag_ids,
    )
    if stored == incoming:
        return "duplicate"
    logger.info(
        "insert_expense conflict for client_expense_id=%r: diff=%s",
        client_expense_id,
        _format_expense_diff(stored, incoming),
    )
    return "conflict"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enqueue_for_logging(con: sqlite3.Connection, expense_id: int) -> None:
    """Enqueue an expense for sheet logging.

    All expense-creating paths (PWA API, receipt pipeline, future sources)
    call this after committing the expense row.  Correction paths that only
    UPDATE existing expenses must NOT call this.
    """
    con.execute(
        "INSERT INTO sheet_logging_jobs (expense_id, status)"
        " VALUES (?, 'pending') ON CONFLICT (expense_id) DO NOTHING",
        [expense_id],
    )


def lookup_existing_expense(
    client_expense_id: str,
    *,
    con: sqlite3.Connection | None = None,
) -> ExistingExpenseRow | None:
    """Look up a stored expense by client_expense_id."""
    if con is not None:
        return fetchone_as(
            ExistingExpenseRow,
            con,
            load_sql("get_existing_expense.sql"),
            [client_expense_id],
        )
    with connection() as own_con:
        return fetchone_as(
            ExistingExpenseRow,
            own_con,
            load_sql("get_existing_expense.sql"),
            [client_expense_id],
        )


def describe_expense_conflict(
    con: sqlite3.Connection,
    payload: ExpensePayload,
) -> str | None:
    """Re-run the stored-vs-incoming compare and return a human-readable diff."""
    existing = fetchone_as(
        ExistingExpenseRow,
        con,
        load_sql("get_existing_expense.sql"),
        [payload.client_expense_id],
    )
    if existing is None:
        return None
    existing_tag_ids = sorted(
        int(r[0])
        for r in con.execute(
            "SELECT tag_id FROM expense_tags WHERE expense_id = ?",
            [existing.id],
        ).fetchall()
    )
    stored = (
        existing.amount,
        existing.amount_original,
        existing.currency_original,
        existing.category_id,
        existing.event_id,
        existing.comment,
        existing.datetime,
        existing.sheet_category,
        existing.sheet_group,
        existing_tag_ids,
    )
    incoming = (
        _to_decimal(payload.amount),
        _to_decimal(payload.amount_original),
        payload.currency_original,
        payload.category_id,
        payload.event_id,
        payload.comment,
        payload.expense_datetime,
        payload.sheet_category,
        payload.sheet_group,
        sorted(int(t) for t in payload.tag_ids),
    )
    return _format_expense_diff(stored, incoming)


def insert_expense(
    con: sqlite3.Connection,
    payload: ExpensePayload,
    *,
    enqueue_logging: bool = True,
) -> InsertExpenseResult:
    """Insert an expense + tags + queue row in one transaction.

    Returns 'created', 'duplicate', or 'conflict'.
    """
    if (payload.sheet_category is None) != (payload.sheet_group is None):
        msg = (
            "sheet_category and sheet_group must be both NULL (runtime row) "
            "or both non-NULL (bootstrap-imported provenance row)"
        )
        raise ValueError(msg)

    tag_ids = list(payload.tag_ids) if payload.tag_ids else []
    incoming_tag_ids = sorted(int(t) for t in tag_ids)
    amount_dec = _to_decimal(payload.amount)
    amount_original_dec = _to_decimal(payload.amount_original)
    incoming = (
        amount_dec,
        amount_original_dec,
        payload.currency_original,
        payload.category_id,
        payload.event_id,
        payload.comment,
        payload.expense_datetime,
        payload.sheet_category,
        payload.sheet_group,
        incoming_tag_ids,
    )
    sql_params = [
        payload.client_expense_id,
        payload.expense_datetime,
        amount_dec,
        amount_original_dec,
        payload.currency_original,
        payload.category_id,
        payload.event_id,
        payload.comment,
        payload.sheet_category,
        payload.sheet_group,
    ]

    con.execute("BEGIN IMMEDIATE")
    tx_active = True
    try:
        _validate_expense_refs(con, payload.category_id, payload.event_id, tag_ids)
        inserted, tx_rolled_back = _try_insert_expense_row(
            con,
            sql_params,
            payload.client_expense_id,
        )

        if tx_rolled_back:
            tx_active = False
        elif inserted is not None:
            expense_pk = int(inserted[0])
            committed = _commit_expense_row(
                con,
                expense_pk,
                tag_ids,
                enqueue_logging,
                payload.client_expense_id,
            )
            if committed:
                return "created"
            tx_active = False
        else:
            con.execute("ROLLBACK")
            tx_active = False

        return _compare_with_stored(con, payload.client_expense_id, incoming)
    except Exception:
        if tx_active:
            best_effort_rollback(
                con,
                context=f"insert_expense(client_expense_id={payload.client_expense_id!r})",
            )
        raise


def get_expense_tags(con: sqlite3.Connection, expense_id: int) -> list[int]:
    """Return the tag_ids attached to an expense, sorted ascending."""
    rows = con.execute(
        "SELECT tag_id FROM expense_tags WHERE expense_id = ? ORDER BY tag_id",
        [expense_id],
    ).fetchall()
    return [int(r[0]) for r in rows]


def get_expense_by_id(con: sqlite3.Connection, expense_id: int) -> ExpenseRow | None:
    """Read a stored expense row by integer PK."""
    return fetchone_as(
        ExpenseRow,
        con,
        "SELECT id, client_expense_id, datetime, amount, amount_original,"
        " currency_original, category_id, event_id, comment,"
        " sheet_category, sheet_group"
        " FROM expenses WHERE id = ?",
        [expense_id],
    )


def get_month_expenses(
    con: sqlite3.Connection,
    year: int,
    month: int,
) -> list[ExpenseRow]:
    return fetchall_as(ExpenseRow, con, load_sql("get_month_expenses.sql"), [year, month])

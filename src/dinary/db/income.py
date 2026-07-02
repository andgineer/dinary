"""Income insert, update, delete, and query operations."""

import dataclasses
import sqlite3
from datetime import date
from decimal import Decimal

from dinary.db.sql_loader import fetchall_as, fetchone_as, load_sql


@dataclasses.dataclass(slots=True)
class IncomeRow:
    id: int
    year: int
    month: int
    income_date: date
    amount: Decimal
    amount_original: Decimal
    currency_original: str
    comment: str | None


@dataclasses.dataclass(slots=True)
class IncomeData:
    """Fields for a new or updated income record (excludes DB-assigned id)."""

    year: int
    month: int
    income_date: date
    amount: float
    amount_original: float
    currency_original: str
    comment: str | None = None


def _enqueue_logging(con: sqlite3.Connection, year: int, month: int) -> None:
    con.execute(
        "INSERT INTO income_logging_jobs (year, month, status)"
        " VALUES (?, ?, 'pending')"
        " ON CONFLICT (year, month) DO UPDATE"
        " SET status = 'pending', claimed_at = NULL, last_error = NULL",
        [year, month],
    )


def insert_income(
    con: sqlite3.Connection,
    data: IncomeData,
    *,
    enqueue_logging: bool = True,
) -> IncomeRow:
    """Insert a new income row and return it."""
    row = fetchone_as(
        IncomeRow,
        con,
        load_sql("insert_income.sql"),
        [
            data.year,
            data.month,
            data.income_date,
            data.amount,
            data.amount_original,
            data.currency_original,
            data.comment,
        ],
    )
    if enqueue_logging:
        _enqueue_logging(con, data.year, data.month)
    return row  # type: ignore[return-value]


def update_income(
    con: sqlite3.Connection,
    income_id: int,
    data: IncomeData,
    *,
    enqueue_logging: bool = True,
) -> IncomeRow:
    """Update income row by id. Returns updated row. Raises ValueError if not found."""
    old = con.execute("SELECT year, month FROM income WHERE id=?", [income_id]).fetchone()
    if old is None:
        raise ValueError(f"Income {income_id} not found")
    old_year, old_month = int(old[0]), int(old[1])
    result = con.execute(
        "UPDATE income"
        " SET year=?, month=?, amount=?, amount_original=?, currency_original=?,"
        " income_date=?, comment=?"
        " WHERE id=?"
        " RETURNING id, year, month, income_date, amount,"
        " amount_original, currency_original, comment",
        [
            data.year,
            data.month,
            data.amount,
            data.amount_original,
            data.currency_original,
            data.income_date,
            data.comment,
            income_id,
        ],
    ).fetchone()
    if result is None:
        raise ValueError(f"Income {income_id} not found")
    row = IncomeRow(
        id=int(result[0]),
        year=int(result[1]),
        month=int(result[2]),
        income_date=result[3],
        amount=Decimal(str(result[4])),
        amount_original=Decimal(str(result[5])),
        currency_original=result[6],
        comment=result[7],
    )
    if enqueue_logging:
        _enqueue_logging(con, row.year, row.month)
        if (old_year, old_month) != (row.year, row.month):
            remaining = con.execute(
                "SELECT COUNT(*) FROM income WHERE year=? AND month=?",
                [old_year, old_month],
            ).fetchone()[0]
            if remaining > 0:
                _enqueue_logging(con, old_year, old_month)
            else:
                con.execute(
                    "DELETE FROM income_logging_jobs WHERE year=? AND month=?",
                    [old_year, old_month],
                )
    return row


def delete_income(con: sqlite3.Connection, income_id: int) -> None:
    """Also deletes the month's logging job when its last income row is removed
    (no FK cascade — year/month is no longer a unique key)."""
    result = con.execute(
        "DELETE FROM income WHERE id = ? RETURNING year, month",
        [income_id],
    ).fetchone()
    if result is None:
        raise ValueError(f"Income {income_id} not found")
    year, month = int(result[0]), int(result[1])
    remaining = con.execute(
        "SELECT COUNT(*) FROM income WHERE year = ? AND month = ?",
        [year, month],
    ).fetchone()[0]
    if remaining == 0:
        con.execute(
            "DELETE FROM income_logging_jobs WHERE year = ? AND month = ?",
            [year, month],
        )


def list_incomes(
    con: sqlite3.Connection,
    page: int,
    page_size: int,
) -> tuple[list[IncomeRow], bool]:
    """Return (items, has_more) for the given page, ordered newest first."""
    offset = (page - 1) * page_size
    total = con.execute("SELECT COUNT(*) FROM income").fetchone()[0]
    rows = fetchall_as(IncomeRow, con, load_sql("list_incomes.sql"), [page_size, offset])
    has_more = offset + page_size < total
    return rows, has_more


def get_income_by_id(
    con: sqlite3.Connection,
    income_id: int,
) -> IncomeRow | None:
    """Return the income row for the given id, or None."""
    return fetchone_as(
        IncomeRow,
        con,
        "SELECT id, year, month, income_date, amount, amount_original, currency_original, comment"
        " FROM income WHERE id = ?",
        [income_id],
    )


def get_income_total_for_month(
    con: sqlite3.Connection,
    year: int,
    month: int,
) -> Decimal | None:
    """Return the sum of all income amounts for the given year/month, or None if no records."""
    row = con.execute(
        "SELECT SUM(amount) FROM income WHERE year = ? AND month = ?",
        [year, month],
    ).fetchone()
    if row[0] is None:
        return None
    return Decimal(str(row[0]))

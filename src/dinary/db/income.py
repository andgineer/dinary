"""Income insert, update, delete, and query operations."""

import dataclasses
import sqlite3
from decimal import Decimal

from dinary.db.sql_loader import fetchall_as, load_sql


@dataclasses.dataclass(slots=True)
class IncomeRow:
    year: int
    month: int
    amount: Decimal


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
    year: int,
    month: int,
    amount: float,
    *,
    enqueue_logging: bool = True,
) -> None:
    """Insert a new income row. Raises sqlite3.IntegrityError on duplicate (year, month)."""
    con.execute(load_sql("insert_income.sql"), [year, month, amount])
    if enqueue_logging:
        _enqueue_logging(con, year, month)


def update_income(
    con: sqlite3.Connection,
    year: int,
    month: int,
    amount: float,
    *,
    enqueue_logging: bool = True,
) -> IncomeRow:
    """Update income amount. Returns updated row. Raises ValueError if not found."""
    result = con.execute(
        "UPDATE income SET amount = ? WHERE year = ? AND month = ? RETURNING year, month, amount",
        [amount, year, month],
    ).fetchone()
    if result is None:
        raise ValueError(f"Income ({year}, {month}) not found")
    if enqueue_logging:
        _enqueue_logging(con, year, month)
    return IncomeRow(year=int(result[0]), month=int(result[1]), amount=Decimal(str(result[2])))


def delete_income(con: sqlite3.Connection, year: int, month: int) -> None:
    """Delete an income row. Raises ValueError if not found."""
    rows = con.execute(
        "DELETE FROM income WHERE year = ? AND month = ? RETURNING year",
        [year, month],
    ).fetchall()
    if not rows:
        raise ValueError(f"Income ({year}, {month}) not found")


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


def get_income_by_year_month(
    con: sqlite3.Connection,
    year: int,
    month: int,
) -> IncomeRow | None:
    """Return the income row for the given year/month, or None."""
    row = con.execute(
        "SELECT year, month, amount FROM income WHERE year = ? AND month = ?",
        [year, month],
    ).fetchone()
    if row is None:
        return None
    return IncomeRow(year=int(row[0]), month=int(row[1]), amount=Decimal(str(row[2])))

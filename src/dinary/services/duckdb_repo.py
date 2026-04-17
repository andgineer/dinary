"""DuckDB repository: config.duckdb (reference data) and budget_YYYY.duckdb (transactions).

Uses ATTACH for cross-DB referential integrity validation.
"""

import dataclasses
import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb

from dinary.services import db_migrations
from dinary.services.sql_loader import fetchall_as, fetchone_as, load_sql

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")

CONFIG_DB = DATA_DIR / "config.duckdb"

SYNTHETIC_EVENT_PREFIX = "отпуск-"
TRAVEL_ENVELOPE = "путешествия"


# ---------------------------------------------------------------------------
# Row types for typed query results
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class MappingRow:
    category_id: int
    beneficiary_id: int | None
    event_id: int | None
    sphere_of_life_id: int | None


@dataclasses.dataclass(slots=True)
class ExpenseRow:
    id: str
    datetime: datetime
    amount: Decimal
    amount_original: Decimal
    currency_original: str
    category_id: int
    beneficiary_id: int | None
    event_id: int | None
    sphere_of_life_id: int | None
    comment: str | None
    source_type: str
    source_envelope: str


@dataclasses.dataclass(slots=True)
class SourceMappingRow:
    source_type: str
    source_envelope: str


@dataclasses.dataclass(slots=True)
class ReverseMappingRow:
    source_type: str
    source_envelope: str
    sphere_of_life_id: int | None


@dataclasses.dataclass(slots=True)
class ImportSourceRow:
    year: int
    spreadsheet_id: str
    worksheet_name: str
    layout_key: str
    notes: str | None


@dataclasses.dataclass(slots=True)
class EventIdRow:
    event_id: int


@dataclasses.dataclass(slots=True)
class ExistingExpenseRow:
    amount: Decimal
    amount_original: Decimal
    currency_original: str
    category_id: int
    beneficiary_id: int | None
    event_id: int | None
    sphere_of_life_id: int | None
    comment: str | None
    datetime: datetime
    source_type: str
    source_envelope: str


@dataclasses.dataclass(slots=True)
class IdNameRow:
    id: int
    name: str


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------


def _budget_path(year: int) -> Path:
    return DATA_DIR / f"budget_{year}.duckdb"


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def init_config_db() -> None:
    """Create or migrate config.duckdb to the latest schema."""
    ensure_data_dir()
    db_migrations.migrate_config_db(CONFIG_DB)


def init_budget_db(year: int) -> Path:
    """Create or migrate a yearly budget DB and return its path."""
    ensure_data_dir()
    path = _budget_path(year)
    created = not path.exists()
    db_migrations.migrate_budget_db(path)
    if created:
        logger.info("Created %s", path)
    return path


def get_budget_connection(year: int) -> duckdb.DuckDBPyConnection:
    """Open a connection to budget_YYYY.duckdb, creating it if needed.

    The caller is responsible for closing the connection.
    The connection has config.duckdb ATTACHed as 'config' (READ_ONLY).
    """
    path = init_budget_db(year)
    con = duckdb.connect(str(path))
    try:
        con.execute(
            f"ATTACH '{CONFIG_DB}' AS config (READ_ONLY)",
        )
    except Exception:
        con.close()
        raise
    return con


def get_config_connection(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """Open a connection to config.duckdb."""
    return duckdb.connect(str(CONFIG_DB), read_only=read_only)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def resolve_mapping(
    con: duckdb.DuckDBPyConnection,
    category: str,
    group: str,
) -> MappingRow | None:
    """Look up (source_type, source_envelope) in source_type_mapping via ATTACHed config."""
    return fetchone_as(MappingRow, con, load_sql("resolve_mapping.sql"), [category, group])


def resolve_mapping_for_year(
    con: duckdb.DuckDBPyConnection,
    category: str,
    group: str,
    year: int,
) -> MappingRow | None:
    """Look up (source_type, source_envelope) with year-scoped override support.

    Prefers an exact year match; falls back to year=0 (default).
    """
    return fetchone_as(
        MappingRow,
        con,
        load_sql("resolve_mapping_for_year.sql"),
        [category, group, year],
    )


def get_import_source(year: int) -> ImportSourceRow | None:
    """Look up the import source metadata for a given year."""
    con = get_config_connection(read_only=True)
    try:
        return fetchone_as(
            ImportSourceRow,
            con,
            "SELECT year, spreadsheet_id, worksheet_name, layout_key, notes "
            "FROM sheet_import_sources WHERE year = ?",
            [year],
        )
    finally:
        con.close()


def resolve_travel_event(expense_date: date) -> int:
    """Find or create a synthetic travel event for the expense's year.

    Operates directly on config.duckdb (not ATTACHed) to avoid handle conflicts.
    """
    year = expense_date.year
    event_name = f"{SYNTHETIC_EVENT_PREFIX}{year}"

    config_con = get_config_connection(read_only=False)
    try:
        row = fetchone_as(
            EventIdRow,
            config_con,
            load_sql("find_travel_event.sql"),
            [event_name, expense_date, expense_date],
        )
        if row:
            return row.event_id

        row = config_con.execute(
            "SELECT COALESCE(MAX(id), 0) FROM events",
        ).fetchone()
        max_id = row[0] if row else 0
        new_id = max_id + 1
        config_con.execute(
            "INSERT INTO events (id, name, date_from, date_to) VALUES (?, ?, ?, ?)",
            [new_id, event_name, date(year, 1, 1), date(year, 12, 31)],
        )
        logger.info("Auto-created synthetic travel event: %s (id=%d)", event_name, new_id)
        return new_id
    finally:
        config_con.close()


def ensure_event(
    *,
    name: str,
    date_from: date,
    date_to: date,
    comment: str | None = None,
) -> int:
    """Find or create a named event in config.duckdb and return its id."""
    config_con = get_config_connection(read_only=False)
    try:
        row = config_con.execute(
            "SELECT id FROM events WHERE name = ? AND date_from = ? AND date_to = ?",
            [name, date_from, date_to],
        ).fetchone()
        if row:
            return row[0]

        max_row = config_con.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
        max_event_id = max_row[0] if max_row else 0
        new_id = max_event_id + 1
        config_con.execute(
            "INSERT INTO events (id, name, date_from, date_to, comment) VALUES (?, ?, ?, ?, ?)",
            [new_id, name, date_from, date_to, comment],
        )
        logger.info("Created event: %s (id=%d)", name, new_id)
        return new_id
    finally:
        config_con.close()


def insert_expense(  # noqa: PLR0913
    con: duckdb.DuckDBPyConnection,
    expense_id: str,
    expense_datetime: datetime,
    amount: float,
    amount_original: float,
    currency_original: str,
    category_id: int,
    beneficiary_id: int | None,
    event_id: int | None,
    sphere_of_life_id: int | None,
    comment: str,
    *,
    source_type: str = "",
    source_envelope: str = "",
) -> str:
    """Insert an expense, returning 'created', 'duplicate', or 'conflict'.

    Uses INSERT ... ON CONFLICT DO NOTHING RETURNING id for idempotent inserts.
    On PK conflict, compares stored values to detect true duplicates vs conflicts.
    Validates dimension IDs against ATTACHed config tables before inserting.
    """
    con.execute("BEGIN")
    try:
        if not con.execute(
            "SELECT 1 FROM config.categories WHERE id = ?",
            [category_id],
        ).fetchone():
            raise ValueError(f"category_id {category_id} not found in config.categories")
        if (
            beneficiary_id is not None
            and not con.execute(
                "SELECT 1 FROM config.family_members WHERE id = ?",
                [beneficiary_id],
            ).fetchone()
        ):
            raise ValueError(f"beneficiary_id {beneficiary_id} not found in config.family_members")
        if (
            event_id is not None
            and not con.execute(
                "SELECT 1 FROM config.events WHERE id = ?",
                [event_id],
            ).fetchone()
        ):
            raise ValueError(f"event_id {event_id} not found in config.events")
        if (
            sphere_of_life_id is not None
            and not con.execute(
                "SELECT 1 FROM config.spheres_of_life WHERE id = ?",
                [sphere_of_life_id],
            ).fetchone()
        ):
            raise ValueError(
                f"sphere_of_life_id {sphere_of_life_id} not found in config.spheres_of_life",
            )

        inserted = con.execute(
            load_sql("insert_expense.sql"),
            [
                expense_id,
                expense_datetime,
                amount,
                amount_original,
                currency_original,
                category_id,
                beneficiary_id,
                event_id,
                sphere_of_life_id,
                comment,
                source_type,
                source_envelope,
            ],
        ).fetchone()

        if inserted is not None:
            year = expense_datetime.year
            month = expense_datetime.month
            con.execute(
                "INSERT INTO sheet_sync_jobs (year, month) VALUES (?, ?) ON CONFLICT DO NOTHING",
                [year, month],
            )
            con.execute("COMMIT")
            return "created"

        existing = fetchone_as(
            ExistingExpenseRow,
            con,
            load_sql("get_existing_expense.sql"),
            [expense_id],
        )
        assert existing is not None, f"expense {expense_id} vanished after ON CONFLICT"

        stored = (
            existing.amount,
            existing.amount_original,
            existing.currency_original,
            existing.category_id,
            existing.beneficiary_id,
            existing.event_id,
            existing.sphere_of_life_id,
            existing.comment,
            existing.datetime,
            existing.source_type,
            existing.source_envelope,
        )
        incoming = (
            Decimal(str(amount)),
            Decimal(str(amount_original)),
            currency_original,
            category_id,
            beneficiary_id,
            event_id,
            sphere_of_life_id,
            comment,
            expense_datetime,
            source_type,
            source_envelope,
        )

        con.execute("ROLLBACK")

        if stored == incoming:
            return "duplicate"
        return "conflict"

    except Exception:
        con.execute("ROLLBACK")
        raise


def get_dirty_sync_jobs(con: duckdb.DuckDBPyConnection) -> list[tuple[int, int]]:
    """Return all (year, month) pairs pending sync."""
    return con.execute(
        "SELECT year, month FROM sheet_sync_jobs ORDER BY year, month",
    ).fetchall()


def clear_sync_job(con: duckdb.DuckDBPyConnection, year: int, month: int) -> None:
    """Remove a completed sync job."""
    con.execute("DELETE FROM sheet_sync_jobs WHERE year = ? AND month = ?", [year, month])


def get_month_expenses(
    con: duckdb.DuckDBPyConnection,
    year: int,
    month: int,
) -> list[ExpenseRow]:
    """Read all expenses for a given month."""
    return fetchall_as(ExpenseRow, con, load_sql("get_month_expenses.sql"), [year, month])


def reverse_lookup_mapping(  # noqa: PLR0913
    con: duckdb.DuckDBPyConnection,
    year: int,
    category_id: int,
    beneficiary_id: int | None,
    event_id: int | None,
    sphere_of_life_id: int | None,
) -> tuple[str, str] | None:
    """Reverse-map a 4D expense back to (source_type, source_envelope).

    Two paths:
    1. Travel expenses (event_id references a synthetic event): match by
       category_id + TRAVEL_ENVELOPE.
    2. All others: match by category_id + beneficiary + event + sphere_of_life.
    """
    if event_id is not None:
        is_travel = con.execute(
            "SELECT 1 FROM config.events WHERE id = ? AND name LIKE ?",
            [event_id, f"{SYNTHETIC_EVENT_PREFIX}%"],
        ).fetchone()
        if is_travel:
            row = fetchone_as(
                SourceMappingRow,
                con,
                load_sql("reverse_lookup_travel.sql"),
                [year, TRAVEL_ENVELOPE, category_id],
            )
            return (row.source_type, row.source_envelope) if row else None

    rows = fetchall_as(
        ReverseMappingRow,
        con,
        load_sql("reverse_lookup_5d.sql"),
        [year, category_id, beneficiary_id, event_id],
    )

    for r in rows:
        if r.sphere_of_life_id == sphere_of_life_id:
            return (r.source_type, r.source_envelope)

    return None

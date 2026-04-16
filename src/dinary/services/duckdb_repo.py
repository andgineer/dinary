"""DuckDB repository: config.duckdb (reference data) and budget_YYYY.duckdb (transactions).

Uses ATTACH for cross-DB referential integrity validation.
"""

import dataclasses
import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb

from dinary.services.sql_loader import fetchall_as, fetchone_as, load_sql

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")

CONFIG_DB = DATA_DIR / "config.duckdb"

CONFIG_SCHEMA = """
CREATE TABLE IF NOT EXISTS category_groups (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    monthly_budget_eur DECIMAL(10,2)
);

CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    group_id    INTEGER NOT NULL REFERENCES category_groups(id),
    UNIQUE(name, group_id)
);

CREATE TABLE IF NOT EXISTS family_members (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    date_from   DATE NOT NULL,
    date_to     DATE NOT NULL,
    is_active   BOOLEAN DEFAULT true,
    comment     TEXT
);

CREATE TABLE IF NOT EXISTS event_members (
    event_id    INTEGER NOT NULL REFERENCES events(id),
    member_id   INTEGER NOT NULL REFERENCES family_members(id),
    PRIMARY KEY (event_id, member_id)
);

CREATE TABLE IF NOT EXISTS tags (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS stores (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    store_type  TEXT
);

CREATE TABLE IF NOT EXISTS sheet_category_mapping (
    sheet_category  TEXT NOT NULL,
    sheet_group     TEXT NOT NULL DEFAULT '',
    category_id     INTEGER NOT NULL REFERENCES categories(id),
    beneficiary_id  INTEGER REFERENCES family_members(id),
    event_id        INTEGER REFERENCES events(id),
    store_id        INTEGER REFERENCES stores(id),
    tag_ids         INTEGER[],
    PRIMARY KEY (sheet_category, sheet_group)
);
"""

BUDGET_SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id              TEXT PRIMARY KEY,
    datetime        TIMESTAMP NOT NULL,
    name            TEXT NOT NULL DEFAULT '',
    amount          DECIMAL(10,2) NOT NULL,
    currency        TEXT DEFAULT 'RSD',
    category_id     INTEGER NOT NULL,
    beneficiary_id  INTEGER,
    event_id        INTEGER,
    store_id        INTEGER,
    comment         TEXT,
    source          TEXT NOT NULL DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS expense_tags (
    expense_id  TEXT NOT NULL REFERENCES expenses(id),
    tag_id      INTEGER NOT NULL,
    PRIMARY KEY (expense_id, tag_id)
);

CREATE TABLE IF NOT EXISTS sheet_sync_jobs (
    year    INTEGER,
    month   INTEGER,
    PRIMARY KEY (year, month)
);
"""

SYNTHETIC_EVENT_PREFIX = "отпуск-"
TRAVEL_GROUP = "путешествия"


# ---------------------------------------------------------------------------
# Row types for typed query results
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class MappingRow:
    category_id: int
    beneficiary_id: int | None
    event_id: int | None
    store_id: int | None
    tag_ids: list[int]

    def __post_init__(self) -> None:
        self.tag_ids = sorted(self.tag_ids) if self.tag_ids else []


@dataclasses.dataclass(slots=True)
class ExpenseRow:
    id: str
    datetime: datetime
    amount: Decimal
    currency: str
    category_id: int
    beneficiary_id: int | None
    event_id: int | None
    store_id: int | None
    comment: str | None
    tag_ids: list[int]

    def __post_init__(self) -> None:
        self.tag_ids = self.tag_ids if self.tag_ids else []


@dataclasses.dataclass(slots=True)
class SheetCategoryRow:
    sheet_category: str
    sheet_group: str


@dataclasses.dataclass(slots=True)
class ReverseMappingRow:
    sheet_category: str
    sheet_group: str
    tag_ids: list[int] | None


@dataclasses.dataclass(slots=True)
class EventIdRow:
    event_id: int


@dataclasses.dataclass(slots=True)
class ExistingExpenseRow:
    amount: Decimal
    currency: str
    category_id: int
    beneficiary_id: int | None
    event_id: int | None
    store_id: int | None
    comment: str | None
    datetime: datetime


@dataclasses.dataclass(slots=True)
class IdNameRow:
    id: int
    name: str


@dataclasses.dataclass(slots=True)
class CategoryRefRow:
    id: int
    name: str
    group_id: int


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------


def _budget_path(year: int) -> Path:
    return DATA_DIR / f"budget_{year}.duckdb"


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def init_config_db() -> None:
    """Create config.duckdb and all reference tables if they don't exist."""
    ensure_data_dir()
    con = duckdb.connect(str(CONFIG_DB))
    try:
        con.execute("BEGIN")
        con.execute(CONFIG_SCHEMA)
        con.execute("COMMIT")
    finally:
        con.close()


def _init_budget_db(path: Path) -> None:
    """Create a yearly budget DB with schema if it doesn't exist."""
    con = duckdb.connect(str(path))
    try:
        con.execute("BEGIN")
        con.execute(BUDGET_SCHEMA)
        con.execute("COMMIT")
    finally:
        con.close()


def get_budget_connection(year: int) -> duckdb.DuckDBPyConnection:
    """Open a connection to budget_YYYY.duckdb, creating it if needed.

    The caller is responsible for closing the connection.
    The connection has config.duckdb ATTACHed as 'config' (READ_ONLY).
    """
    path = _budget_path(year)
    if not path.exists():
        _init_budget_db(path)
        logger.info("Created %s", path)

    con = duckdb.connect(str(path))
    try:
        con.execute(BUDGET_SCHEMA)
        con.execute(
            f"ATTACH '{CONFIG_DB}' AS config (READ_ONLY)"
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
    """Look up (category, group) in sheet_category_mapping via ATTACHed config.

    Returns MappingRow or None if not found.
    """
    return fetchone_as(MappingRow, con, load_sql("resolve_mapping.sql"), [category, group])


def resolve_travel_event(expense_date: date) -> int:
    """Find or create a synthetic travel event for the expense's year.

    Looks for an event named 'отпуск-YYYY' whose date range contains
    the expense date. Auto-creates one if missing.

    Operates directly on config.duckdb (not ATTACHed) to avoid handle conflicts.
    """
    year = expense_date.year
    event_name = f"{SYNTHETIC_EVENT_PREFIX}{year}"

    config_con = get_config_connection(read_only=False)
    try:
        row = fetchone_as(
            EventIdRow, config_con,
            load_sql("find_travel_event.sql"),
            [event_name, expense_date, expense_date],
        )
        if row:
            return row.event_id

        max_id = config_con.execute(
            "SELECT COALESCE(MAX(id), 0) FROM events"
        ).fetchone()[0]
        new_id = max_id + 1
        config_con.execute(
            "INSERT INTO events (id, name, date_from, date_to) VALUES (?, ?, ?, ?)",
            [new_id, event_name, date(year, 1, 1), date(year, 12, 31)],
        )
        logger.info("Auto-created synthetic travel event: %s (id=%d)", event_name, new_id)
        return new_id
    finally:
        config_con.close()


def insert_expense(
    con: duckdb.DuckDBPyConnection,
    expense_id: str,
    expense_datetime: datetime,
    amount: float,
    currency: str,
    category_id: int,
    beneficiary_id: int | None,
    event_id: int | None,
    store_id: int | None,
    tag_ids: list[int],
    comment: str,
) -> str:
    """Insert an expense, returning 'created', 'duplicate', or 'conflict'.

    Uses INSERT ... ON CONFLICT DO NOTHING RETURNING id for idempotent inserts.
    On PK conflict, compares stored values to detect true duplicates vs conflicts.
    Validates dimension IDs against ATTACHed config tables before inserting.
    """
    con.execute("BEGIN")
    try:
        if not con.execute(
            "SELECT 1 FROM config.categories WHERE id = ?", [category_id]
        ).fetchone():
            raise ValueError(f"category_id {category_id} not found in config.categories")
        if beneficiary_id is not None and not con.execute(
            "SELECT 1 FROM config.family_members WHERE id = ?", [beneficiary_id]
        ).fetchone():
            raise ValueError(f"beneficiary_id {beneficiary_id} not found in config.family_members")
        if event_id is not None and not con.execute(
            "SELECT 1 FROM config.events WHERE id = ?", [event_id]
        ).fetchone():
            raise ValueError(f"event_id {event_id} not found in config.events")
        if store_id is not None and not con.execute(
            "SELECT 1 FROM config.stores WHERE id = ?", [store_id]
        ).fetchone():
            raise ValueError(f"store_id {store_id} not found in config.stores")
        for tid in tag_ids:
            if not con.execute(
                "SELECT 1 FROM config.tags WHERE id = ?", [tid]
            ).fetchone():
                raise ValueError(f"tag_id {tid} not found in config.tags")

        inserted = con.execute(
            load_sql("insert_expense.sql"),
            [
                expense_id, expense_datetime, amount, currency,
                category_id, beneficiary_id, event_id, store_id, comment,
            ],
        ).fetchone()

        if inserted is not None:
            for tag_id in tag_ids:
                con.execute(
                    "INSERT INTO expense_tags (expense_id, tag_id) VALUES (?, ?)",
                    [expense_id, tag_id],
                )
            year = expense_datetime.year
            month = expense_datetime.month
            con.execute(
                "INSERT INTO sheet_sync_jobs (year, month) VALUES (?, ?) ON CONFLICT DO NOTHING",
                [year, month],
            )
            con.execute("COMMIT")
            return "created"

        existing = fetchone_as(
            ExistingExpenseRow, con,
            load_sql("get_existing_expense.sql"),
            [expense_id],
        )

        existing_tags_row = con.execute(
            "SELECT list(tag_id ORDER BY tag_id) FROM expense_tags WHERE expense_id = ?",
            [expense_id],
        ).fetchone()
        existing_tags = existing_tags_row[0] if existing_tags_row and existing_tags_row[0] else []

        stored = (
            existing.amount, existing.currency, existing.category_id,
            existing.beneficiary_id, existing.event_id, existing.store_id,
            existing.comment, existing.datetime,
        )
        incoming = (
            Decimal(str(amount)), currency, category_id, beneficiary_id,
            event_id, store_id, comment, expense_datetime,
        )

        con.execute("ROLLBACK")

        if stored == incoming and sorted(existing_tags) == sorted(tag_ids):
            return "duplicate"
        return "conflict"

    except Exception:
        con.execute("ROLLBACK")
        raise


def get_dirty_sync_jobs(con: duckdb.DuckDBPyConnection) -> list[tuple[int, int]]:
    """Return all (year, month) pairs pending sync."""
    return con.execute(
        "SELECT year, month FROM sheet_sync_jobs ORDER BY year, month"
    ).fetchall()


def clear_sync_job(con: duckdb.DuckDBPyConnection, year: int, month: int) -> None:
    """Remove a completed sync job."""
    con.execute("DELETE FROM sheet_sync_jobs WHERE year = ? AND month = ?", [year, month])


def get_month_expenses(
    con: duckdb.DuckDBPyConnection,
    year: int,
    month: int,
) -> list[ExpenseRow]:
    """Read all expenses for a given month with their tags."""
    return fetchall_as(ExpenseRow, con, load_sql("get_month_expenses.sql"), [year, month])


def reverse_lookup_mapping(
    con: duckdb.DuckDBPyConnection,
    category_id: int,
    beneficiary_id: int | None,
    event_id: int | None,
    store_id: int | None,
    tag_ids: list[int],
) -> tuple[str, str] | None:
    """Reverse-map a 5D expense back to (sheet_category, sheet_group).

    Two paths:
    1. Travel expenses (event_id references a synthetic event): match by
       category_id + TRAVEL_GROUP.
    2. All others: full 5D null-safe match.
    """
    if event_id is not None:
        is_travel = con.execute(
            "SELECT 1 FROM config.events WHERE id = ? AND name LIKE ?",
            [event_id, f"{SYNTHETIC_EVENT_PREFIX}%"],
        ).fetchone()
        if is_travel:
            row = fetchone_as(
                SheetCategoryRow, con,
                load_sql("reverse_lookup_travel.sql"),
                [TRAVEL_GROUP, category_id],
            )
            return (row.sheet_category, row.sheet_group) if row else None

    sorted_tags = sorted(tag_ids) if tag_ids else []
    rows = fetchall_as(
        ReverseMappingRow, con,
        load_sql("reverse_lookup_5d.sql"),
        [category_id, beneficiary_id, event_id, store_id],
    )

    for r in rows:
        mapping_tags = sorted(r.tag_ids) if r.tag_ids else []
        if mapping_tags == sorted_tags:
            return (r.sheet_category, r.sheet_group)

    return None

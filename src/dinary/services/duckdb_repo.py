"""DuckDB repository: config.duckdb (reference data) and budget_YYYY.duckdb (transactions).

Uses ATTACH for cross-DB referential integrity validation.
"""

import logging
from datetime import date, datetime
from pathlib import Path

import duckdb

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


def resolve_mapping(
    con: duckdb.DuckDBPyConnection,
    category: str,
    group: str,
) -> dict | None:
    """Look up (category, group) in sheet_category_mapping via ATTACHed config.

    Returns dict with category_id, beneficiary_id, event_id, store_id, tag_ids
    or None if not found.
    """
    row = con.execute(
        """
        SELECT category_id, beneficiary_id, event_id, store_id, tag_ids
        FROM config.sheet_category_mapping
        WHERE sheet_category = ? AND sheet_group = ?
        """,
        [category, group],
    ).fetchone()
    if row is None:
        return None
    tag_ids = sorted(row[4]) if row[4] else []
    return {
        "category_id": row[0],
        "beneficiary_id": row[1],
        "event_id": row[2],
        "store_id": row[3],
        "tag_ids": tag_ids,
    }


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
        row = config_con.execute(
            "SELECT id FROM events WHERE name = ? AND date_from <= ? AND date_to >= ?",
            [event_name, expense_date, expense_date],
        ).fetchone()
        if row:
            return row[0]

        max_id = config_con.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
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
            """
            INSERT INTO expenses (id, datetime, amount, currency, category_id,
                                  beneficiary_id, event_id, store_id, comment)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
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
                """
                INSERT INTO sheet_sync_jobs (year, month) VALUES (?, ?)
                ON CONFLICT DO NOTHING
                """,
                [year, month],
            )
            con.execute("COMMIT")
            return "created"

        existing = con.execute(
            """
            SELECT amount, currency, category_id, beneficiary_id,
                   event_id, store_id, comment, datetime
            FROM expenses WHERE id = ?
            """,
            [expense_id],
        ).fetchone()

        existing_tags_row = con.execute(
            "SELECT list(tag_id ORDER BY tag_id) FROM expense_tags WHERE expense_id = ?",
            [expense_id],
        ).fetchone()
        existing_tags = existing_tags_row[0] if existing_tags_row and existing_tags_row[0] else []

        stored = (
            float(existing[0]), existing[1], existing[2], existing[3],
            existing[4], existing[5], existing[6], existing[7],
        )
        incoming = (
            float(amount), currency, category_id, beneficiary_id,
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
    return con.execute("SELECT year, month FROM sheet_sync_jobs ORDER BY year, month").fetchall()


def clear_sync_job(con: duckdb.DuckDBPyConnection, year: int, month: int) -> None:
    """Remove a completed sync job."""
    con.execute("DELETE FROM sheet_sync_jobs WHERE year = ? AND month = ?", [year, month])


def get_month_expenses(
    con: duckdb.DuckDBPyConnection,
    year: int,
    month: int,
) -> list[dict]:
    """Read all expenses for a given month with their tags."""
    rows = con.execute(
        """
        SELECT e.id, e.datetime, e.amount, e.currency, e.category_id,
               e.beneficiary_id, e.event_id, e.store_id, e.comment,
               (SELECT list(tag_id ORDER BY tag_id) FROM expense_tags
                WHERE expense_id = e.id) as tag_ids
        FROM expenses e
        WHERE YEAR(e.datetime) = ? AND MONTH(e.datetime) = ?
        """,
        [year, month],
    ).fetchall()
    return [
        {
            "id": r[0], "datetime": r[1], "amount": float(r[2]),
            "currency": r[3], "category_id": r[4], "beneficiary_id": r[5],
            "event_id": r[6], "store_id": r[7], "comment": r[8],
            "tag_ids": r[9] if r[9] else [],
        }
        for r in rows
    ]


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
    1. Travel expenses (event_id is a synthetic отпуск-YYYY): match by
       category_id + sheet_group='путешествия'.
    2. All others: full 5D null-safe match.
    """
    if event_id is not None:
        is_travel = con.execute(
            "SELECT 1 FROM config.events WHERE id = ? AND name LIKE 'отпуск-%'",
            [event_id],
        ).fetchone()
        if is_travel:
            row = con.execute(
                """
                SELECT sheet_category, sheet_group
                FROM config.sheet_category_mapping
                WHERE sheet_group = 'путешествия' AND category_id = ?
                """,
                [category_id],
            ).fetchone()
            return (row[0], row[1]) if row else None

    sorted_tags = sorted(tag_ids) if tag_ids else []
    rows = con.execute(
        """
        SELECT sheet_category, sheet_group, tag_ids
        FROM config.sheet_category_mapping
        WHERE category_id = ?
          AND beneficiary_id IS NOT DISTINCT FROM ?
          AND event_id IS NOT DISTINCT FROM ?
          AND store_id IS NOT DISTINCT FROM ?
        """,
        [category_id, beneficiary_id, event_id, store_id],
    ).fetchall()

    for r in rows:
        mapping_tags = sorted(r[2]) if r[2] else []
        if mapping_tags == sorted_tags:
            return (r[0], r[1])

    return None

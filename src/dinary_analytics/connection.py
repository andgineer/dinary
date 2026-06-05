"""DuckDB connection over the ledger SQLite replica and replica sync."""

import shutil
from pathlib import Path

import duckdb

from dinary.config import settings

QUERIES_DIR = Path(__file__).parent / "queries"

_DATA_DIR = Path(settings.data_path).parent
REPLICA_PATH = _DATA_DIR / "ledger-replica.db"
ANALYTICS_DB_PATH = _DATA_DIR / "analytics.db"

LEDGER_SCHEMA = """\
-- expenses: one row per expense; amount is in accounting currency (EUR)
CREATE TABLE expenses (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    datetime          TIMESTAMP NOT NULL,
    amount            DECIMAL(12,2) NOT NULL,      -- accounting currency (EUR)
    amount_original   DECIMAL(12,2) NOT NULL,      -- amount as entered by user
    currency_original TEXT NOT NULL,               -- currency the user entered
    category_id       INTEGER NOT NULL,
    event_id          INTEGER,
    comment           TEXT,
    sheet_category    TEXT,
    sheet_group       TEXT
);

-- categories: expense classification leaf nodes
CREATE TABLE categories (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    group_id  INTEGER,
    is_active BOOLEAN NOT NULL DEFAULT 1
);

-- category_groups: top-level grouping of categories
CREATE TABLE category_groups (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    is_active  BOOLEAN NOT NULL DEFAULT 1
);

-- events: named occasions (trips, projects) optionally attached to expenses
CREATE TABLE events (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    date_from DATE NOT NULL,
    date_to   DATE NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT 1
);

-- tags and expense_tags: free-form labels on expenses (many-to-many)
CREATE TABLE tags (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT 1
);
CREATE TABLE expense_tags (
    expense_id INTEGER NOT NULL,
    tag_id     INTEGER NOT NULL,
    PRIMARY KEY (expense_id, tag_id)
);

-- income: monthly income entries
CREATE TABLE income (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    year             INTEGER NOT NULL,
    month            INTEGER NOT NULL,
    income_date      DATE NOT NULL,
    amount           DECIMAL(12,2) NOT NULL,      -- accounting currency (EUR)
    amount_original  DECIMAL(12,2) NOT NULL,      -- amount as entered by user
    currency_original TEXT NOT NULL,
    comment          TEXT,
    CHECK (month BETWEEN 1 AND 12)
);

-- exchange_rates: daily rates to accounting currency (EUR)
CREATE TABLE exchange_rates (
    currency TEXT NOT NULL,
    date     DATE NOT NULL,
    rate     DECIMAL(18,6) NOT NULL,
    PRIMARY KEY (currency, date)
);
"""


def open_ledger(replica_path: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Return a DuckDB in-memory connection with the ledger SQLite replica attached as 'ledger'."""
    path = replica_path or REPLICA_PATH
    con = duckdb.connect(":memory:")
    con.execute(f"ATTACH '{path}' AS ledger (TYPE sqlite, READ_ONLY)")  # noqa: S608
    return con


def load_query(name: str) -> str:
    """Return the SQL text of a named query file from the queries directory."""
    return (QUERIES_DIR / f"{name}.sql").read_text()


def sync_replica(source_path: Path, target_path: Path | None = None) -> None:
    """Copy the dinary SQLite DB to the analytics replica location."""
    target = target_path or REPLICA_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target)

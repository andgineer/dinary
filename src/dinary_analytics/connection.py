"""DuckDB connection over the ledger SQLite replica."""

import sqlite3
from pathlib import Path

import duckdb

from dinary_analytics.paths import QUERIES_DIR, REPLICA_PATH

LEDGER_TABLES: dict[str, str] = {
    "expenses": (
        "one row per expense; amount is in the accounting currency, "
        "amount_original and currency_original are what the user entered"
    ),
    "categories": "expense classification leaf nodes",
    "category_groups": "top-level grouping of categories",
    "events": "named occasions (trips, projects) optionally attached to expenses",
    "tags": "free-form labels on expenses",
    "expense_tags": "many-to-many link between expenses and tags",
    "income": "income entries; amount is in the accounting currency",
    "exchange_rates": "daily rates to the accounting currency",
}


def _connect_replica(replica_path: Path | None) -> sqlite3.Connection:
    path = replica_path or REPLICA_PATH
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def ledger_schema(replica_path: Path | None = None) -> str:
    """Return the CREATE statements of the ledger tables as they exist in the replica."""
    con = _connect_replica(replica_path)
    try:
        ddl = dict(
            con.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'").fetchall(),
        )
    finally:
        con.close()
    return "\n".join(
        f"-- {table}: {note}\n{ddl[table]};\n"
        for table, note in LEDGER_TABLES.items()
        if table in ddl
    )


def accounting_currency(replica_path: Path | None = None) -> str:
    """Return the currency every ledger amount is denominated in."""
    con = _connect_replica(replica_path)
    try:
        row = con.execute(
            "SELECT value FROM app_metadata WHERE key = 'accounting_currency'",
        ).fetchone()
    except sqlite3.OperationalError as exc:
        raise LookupError("the ledger replica has no app_metadata table") from exc
    finally:
        con.close()
    if row is None:
        raise LookupError("the ledger replica does not record its accounting currency")
    return row[0]


def open_ledger(replica_path: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Return a DuckDB in-memory connection with the ledger SQLite replica attached as 'ledger'."""
    path = replica_path or REPLICA_PATH
    con = duckdb.connect(":memory:")
    con.execute(f"ATTACH '{path}' AS ledger (TYPE sqlite, READ_ONLY)")  # noqa: S608
    return con


def load_query(name: str) -> str:
    """Return the SQL text of a named query file from the queries directory."""
    return (QUERIES_DIR / f"{name}.sql").read_text()

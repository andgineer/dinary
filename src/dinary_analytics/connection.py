"""DuckDB connection over the ledger SQLite replica."""

import json
import os
import sqlite3
import uuid
from pathlib import Path

import duckdb

from dinary_analytics.paths import LEDGER_CACHE_FILENAME, QUERIES_DIR, REPLICA_PATH

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
    "stores": "a shop; chain_id links shops of one chain; pib is the Serbian tax ID",
    "shop_chains": 'a retail chain; group stores by it to answer "at Lidl" across branches',
    "receipts": (
        "a scanned fiscal receipt; total_amount is in the receipt's own currency (RSD), "
        "not the accounting currency"
    ),
    "receipt_items": (
        "one line of a receipt; unit_price and total_price are in the receipt's currency; "
        "expense_id links the line to the expense it was booked into, and the lines of one "
        "expense sum exactly to its amount_original, so a line's share of the expense is "
        "expenses.amount * total_price / expenses.amount_original; name_normalized is the "
        "cleaned item name, name_raw the printed one"
    ),
}

# Tables listed here reach model-written SQL and the model's schema with only these columns.
# receipts.url is the fiscal-portal link of a receipt and is never sent to a model provider.
LEDGER_COLUMNS: dict[str, tuple[str, ...]] = {
    "receipts": (
        "id",
        "store_id",
        "store_name_raw",
        "store_pib_raw",
        "total_amount",
        "purchase_datetime",
        "created_at",
    ),
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
        for table, columns in LEDGER_COLUMNS.items():
            if table in ddl:
                ddl[table] = _narrowed_ddl(con, table, columns)
    finally:
        con.close()
    return "\n".join(
        f"-- {table}: {note}\n{ddl[table]};\n"
        for table, note in LEDGER_TABLES.items()
        if table in ddl
    )


def _narrowed_ddl(con: sqlite3.Connection, table: str, columns: tuple[str, ...]) -> str:
    references = {
        row[3]: f" REFERENCES {row[2]}({row[4]})"
        for row in con.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    }
    definitions = [
        f"    {name} {col_type}"
        + (" PRIMARY KEY" if pk else "")
        + (" NOT NULL" if notnull and not pk else "")
        + references.get(name, "")
        for _cid, name, col_type, notnull, _default, pk in con.execute(
            f"PRAGMA table_info({table})",
        ).fetchall()
        if name in columns
    ]
    return f"CREATE TABLE {table} (\n" + ",\n".join(definitions) + "\n)"


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


def build_ledger_cache(replica_path: Path, cache_path: Path) -> None:
    """Atomically write a DuckDB file holding only the ledger tables of the replica."""
    # The cache carries the mtime of the replica it was built from, so a replica replaced
    # during the build, or restored with an older mtime, still reads as a mismatch.
    replica_mtime = replica_path.stat().st_mtime_ns
    tmp_path = cache_path.with_name(f"{cache_path.name}.{uuid.uuid4().hex}.tmp")
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"ATTACH '{replica_path}' AS src (TYPE sqlite, READ_ONLY)")  # noqa: S608
        con.execute(f"ATTACH '{tmp_path}' AS cache")  # noqa: S608
        con.execute("CREATE SCHEMA cache.ledger")
        con.execute("CREATE TABLE cache.main.ledger_layout (layout VARCHAR)")
        con.execute("INSERT INTO cache.main.ledger_layout VALUES (?)", [_ledger_layout()])
        for table in _present_ledger_tables(con, "src"):
            selected = _selected_columns(con, "src", table)
            con.execute(
                f"CREATE TABLE cache.ledger.{table} AS SELECT {selected} FROM src.{table}",  # noqa: S608
            )
        con.execute("DETACH cache")
    except BaseException:
        con.close()
        tmp_path.unlink(missing_ok=True)
        raise
    con.close()
    os.utime(tmp_path, ns=(replica_mtime, replica_mtime))
    os.replace(tmp_path, cache_path)


def _present_ledger_tables(con: duckdb.DuckDBPyConnection, database: str) -> list[str]:
    present = {
        row[0]
        for row in con.execute(
            "SELECT table_name FROM duckdb_tables() WHERE database_name = ?",
            [database],
        ).fetchall()
    }
    return [table for table in LEDGER_TABLES if table in present]


def _selected_columns(con: duckdb.DuckDBPyConnection, database: str, table: str) -> str:
    if table not in LEDGER_COLUMNS:
        return "*"
    present = {
        row[0]
        for row in con.execute(
            "SELECT column_name FROM duckdb_columns() WHERE database_name = ? AND table_name = ?",
            [database, table],
        ).fetchall()
    }
    return ", ".join(f'"{column}"' for column in LEDGER_COLUMNS[table] if column in present)


def _ledger_layout() -> str:
    return json.dumps({table: LEDGER_COLUMNS.get(table, "*") for table in LEDGER_TABLES})


def _cache_is_stale(replica_path: Path, cache_path: Path) -> bool:
    if not cache_path.exists():
        return True
    if cache_path.stat().st_mtime_ns != replica_path.stat().st_mtime_ns:
        return True
    # A cache built for another table or column selection could still hold a column now excluded.
    with duckdb.connect(str(cache_path), read_only=True) as cache:
        try:
            row = cache.execute("SELECT layout FROM main.ledger_layout").fetchone()
        except duckdb.CatalogException:
            return True
    return row is None or row[0] != _ledger_layout()


def open_ledger_restricted(replica_path: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Return a connection for model-written SQL that reaches the ledger tables and nothing else."""
    path = replica_path or REPLICA_PATH
    cache_path = path.with_name(LEDGER_CACHE_FILENAME)
    if _cache_is_stale(path, cache_path):
        build_ledger_cache(path, cache_path)
    # The SQLite extension reads files past enable_external_access, so it must never load here;
    # an open database file and the temp directory would stay writable, so the data is in memory.
    con = duckdb.connect(
        ":memory:",
        config={
            "autoload_known_extensions": False,
            "autoinstall_known_extensions": False,
            "temp_directory": "",
        },
    )
    try:
        con.execute(f"ATTACH '{cache_path}' AS cache (READ_ONLY)")  # noqa: S608
        con.execute("CREATE SCHEMA ledger")
        for table in _present_ledger_tables(con, "cache"):
            con.execute(f"CREATE TABLE ledger.{table} AS SELECT * FROM cache.ledger.{table}")  # noqa: S608
        con.execute("DETACH cache")
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
    except BaseException:
        con.close()
        raise
    return con


def load_query(name: str) -> str:
    """Return the SQL text of a named query file from the queries directory."""
    return (QUERIES_DIR / f"{name}.sql").read_text()

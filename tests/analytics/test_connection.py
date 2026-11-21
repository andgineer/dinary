import sqlite3

import allure
import pytest

from dinary_analytics.connection import (
    LEDGER_TABLES,
    accounting_currency,
    ledger_schema,
    open_ledger,
)


@pytest.fixture
def ledger_sqlite(tmp_path):
    db = tmp_path / "dinary.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            datetime TEXT NOT NULL,
            amount REAL NOT NULL,
            amount_original REAL NOT NULL,
            currency_original TEXT NOT NULL,
            category_id INTEGER NOT NULL,
            event_id INTEGER,
            comment TEXT,
            sheet_category TEXT,
            sheet_group TEXT
        );
        CREATE TABLE categories (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            group_id INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO categories VALUES (1, 'Groceries', 1, 1);
        INSERT INTO expenses
            VALUES (1, '2024-03-15 10:00:00', 45.0, 4950.0, 'RSD', 1, NULL, 'supermarket', NULL, NULL);
        INSERT INTO expenses
            VALUES (2, '2024-03-20 14:00:00', 10.0, 1100.0, 'RSD', 1, NULL, 'coffee', NULL, NULL);
    """)
    con.commit()
    con.close()
    return db


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_open_ledger_attaches_sqlite(ledger_sqlite):
    con = open_ledger(replica_path=ledger_sqlite)
    try:
        count = con.execute("SELECT COUNT(*) FROM ledger.expenses").fetchone()[0]
        assert count == 2
    finally:
        con.close()


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_open_ledger_is_read_only(ledger_sqlite):
    con = open_ledger(replica_path=ledger_sqlite)
    try:
        with pytest.raises(Exception):
            con.execute(
                "INSERT INTO ledger.expenses "
                "VALUES (99, '2024-01-01', 1.0, 100.0, 'RSD', 1, NULL, NULL, NULL, NULL)"
            )
    finally:
        con.close()


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_open_ledger_can_query_join(ledger_sqlite):
    con = open_ledger(replica_path=ledger_sqlite)
    try:
        rows = con.execute(
            "SELECT e.amount, c.name FROM ledger.expenses e "
            "JOIN ledger.categories c ON e.category_id = c.id"
        ).fetchall()
        assert len(rows) == 2
        assert all(r[1] == "Groceries" for r in rows)
    finally:
        con.close()


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_ledger_schema_is_read_from_the_replica(ledger_sqlite):
    con = sqlite3.connect(str(ledger_sqlite))
    con.executescript("""
        ALTER TABLE expenses ADD COLUMN store_id INTEGER;
        CREATE TABLE llmbroker_secrets (ref TEXT, value TEXT);
    """)
    con.commit()
    con.close()

    schema = ledger_schema(replica_path=ledger_sqlite)

    assert "store_id" in schema
    assert "CREATE TABLE categories" in schema
    assert f"-- expenses: {LEDGER_TABLES['expenses']}" in schema
    assert schema.index("CREATE TABLE expenses") < schema.index("CREATE TABLE categories")
    assert "llmbroker_secrets" not in schema
    assert "CREATE TABLE income" not in schema


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_ledger_schema_handles_a_path_with_spaces(ledger_sqlite, tmp_path):
    spaced = tmp_path / "Application Support"
    spaced.mkdir()
    target = spaced / "replica.db"
    target.write_bytes(ledger_sqlite.read_bytes())

    assert "CREATE TABLE expenses" in ledger_schema(replica_path=target)


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_accounting_currency_comes_from_the_replica(ledger_sqlite):
    con = sqlite3.connect(str(ledger_sqlite))
    con.executescript("""
        CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO app_metadata VALUES ('accounting_currency', 'RSD');
    """)
    con.commit()
    con.close()

    assert accounting_currency(replica_path=ledger_sqlite) == "RSD"


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_accounting_currency_missing_table_is_reported(ledger_sqlite):
    with pytest.raises(LookupError, match="app_metadata"):
        accounting_currency(replica_path=ledger_sqlite)


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_accounting_currency_missing_row_is_reported(ledger_sqlite):
    con = sqlite3.connect(str(ledger_sqlite))
    con.execute("CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    con.commit()
    con.close()

    with pytest.raises(LookupError, match="accounting currency"):
        accounting_currency(replica_path=ledger_sqlite)

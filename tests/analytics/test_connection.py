import sqlite3

import allure
import pytest

from dinary_analytics.connection import LEDGER_SCHEMA, open_ledger, sync_replica


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
def test_sync_replica_copies_file(tmp_path, ledger_sqlite):
    target = tmp_path / "replica.db"
    sync_replica(ledger_sqlite, target)
    assert target.exists()
    assert target.stat().st_size == ledger_sqlite.stat().st_size


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_sync_replica_creates_parent_dir(tmp_path, ledger_sqlite):
    target = tmp_path / "sub" / "dir" / "replica.db"
    sync_replica(ledger_sqlite, target)
    assert target.exists()


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_ledger_schema_is_non_empty_string():
    assert isinstance(LEDGER_SCHEMA, str)
    assert "expenses" in LEDGER_SCHEMA
    assert "categories" in LEDGER_SCHEMA
    assert "income" in LEDGER_SCHEMA

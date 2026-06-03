import json
import sqlite3

import allure
import pytest

import dinary_analytics.connection as conn_module
from dinary_analytics.connection import LEDGER_SCHEMA
from dinary_analytics.mcp_server import _run_query


@pytest.fixture
def patched_replica(tmp_path, monkeypatch):
    db = tmp_path / "ledger-replica.db"
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
        INSERT INTO expenses
            VALUES (1, '2024-05-01 09:00:00', 12.5, 1375.0, 'RSD', 1, NULL, 'coffee', NULL, NULL);
        INSERT INTO expenses
            VALUES (2, '2024-05-15 13:00:00', 50.0, 5500.0, 'RSD', 1, NULL, 'lunch', NULL, NULL);
    """)
    con.commit()
    con.close()
    monkeypatch.setattr(conn_module, "REPLICA_PATH", db)
    return db


@allure.epic("Analytics")
@allure.feature("MCP Server")
def test_run_query_returns_json_array(patched_replica):
    result = _run_query("SELECT id, amount FROM ledger.expenses ORDER BY id")
    data = json.loads(result)
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["id"] == 1
    assert data[0]["amount"] == pytest.approx(12.5)
    assert data[1]["amount"] == pytest.approx(50.0)


@allure.epic("Analytics")
@allure.feature("MCP Server")
def test_run_query_empty_result(patched_replica):
    result = _run_query("SELECT * FROM ledger.expenses WHERE amount > 9999")
    data = json.loads(result)
    assert data == []


@allure.epic("Analytics")
@allure.feature("MCP Server")
def test_run_query_aggregate(patched_replica):
    result = _run_query("SELECT SUM(amount) AS total FROM ledger.expenses")
    data = json.loads(result)
    assert data[0]["total"] == pytest.approx(62.5)


@allure.epic("Analytics")
@allure.feature("MCP Server")
def test_schema_contains_key_tables():
    assert "expenses" in LEDGER_SCHEMA
    assert "categories" in LEDGER_SCHEMA
    assert "income" in LEDGER_SCHEMA
    assert "exchange_rates" in LEDGER_SCHEMA
    assert "ledger" not in LEDGER_SCHEMA

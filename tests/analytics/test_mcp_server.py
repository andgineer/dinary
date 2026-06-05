import json
import sqlite3

import allure
import pytest

import dinary_analytics.connection as conn_module
import dinary_analytics.settings as settings_module
from dinary_analytics.connection import LEDGER_SCHEMA
from dinary_analytics.mcp_server import (
    _run_query,
    delete_view_tool,
    get_config_tool,
    get_view_tool,
    list_views,
    save_view_tool,
    set_config_tool,
)


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


@pytest.fixture
def patched_analytics_db(tmp_path, monkeypatch):
    db = tmp_path / "analytics.db"
    monkeypatch.setattr(settings_module, "ANALYTICS_DB_PATH", db)
    return db


@allure.epic("Analytics")
@allure.feature("MCP Server")
def test_get_config_tool_missing_returns_empty(patched_analytics_db):
    result = get_config_tool("nonexistent")
    assert result == ""


@allure.epic("Analytics")
@allure.feature("MCP Server")
def test_set_and_get_config_tool(patched_analytics_db):
    result = set_config_tool("mykey", "myvalue")
    assert result == "ok"
    assert get_config_tool("mykey") == "myvalue"


@allure.epic("Analytics")
@allure.feature("MCP Server")
def test_list_views_empty(patched_analytics_db):
    result = json.loads(list_views())
    assert result == []


@allure.epic("Analytics")
@allure.feature("MCP Server")
def test_save_view_tool_assigns_id(patched_analytics_db):
    config_json = json.dumps({"name": "Test View", "baskets": []})
    view_id = save_view_tool(config_json)
    assert view_id


@allure.epic("Analytics")
@allure.feature("MCP Server")
def test_list_views_after_save(patched_analytics_db):
    config_json = json.dumps({"name": "Listed View", "baskets": []})
    view_id = save_view_tool(config_json)
    views = json.loads(list_views())
    assert any(v["id"] == view_id for v in views)
    assert any(v["name"] == "Listed View" for v in views)


@allure.epic("Analytics")
@allure.feature("MCP Server")
def test_get_view_tool_returns_config(patched_analytics_db):
    config_json = json.dumps({"name": "GetMe", "baskets": []})
    view_id = save_view_tool(config_json)
    result = json.loads(get_view_tool(view_id))
    assert result["name"] == "GetMe"
    assert result["id"] == view_id


@allure.epic("Analytics")
@allure.feature("MCP Server")
def test_get_view_tool_missing_returns_empty(patched_analytics_db):
    result = get_view_tool("no-such-id")
    assert result == ""


@allure.epic("Analytics")
@allure.feature("MCP Server")
def test_delete_view_tool(patched_analytics_db):
    config_json = json.dumps({"name": "ToDelete", "baskets": []})
    view_id = save_view_tool(config_json)
    result = delete_view_tool(view_id)
    assert result == "ok"
    assert get_view_tool(view_id) == ""
    views = json.loads(list_views())
    assert not any(v["id"] == view_id for v in views)

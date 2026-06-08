import asyncio
import datetime
import json
import logging
import sqlite3
import time
from unittest.mock import MagicMock

import allure
import pytest
from mcp.server.fastmcp.exceptions import ToolError

import dinary_analytics.ai_service as ai_service_module
import dinary_analytics.refresh as refresh_module
import dinary_analytics.settings as settings_module
from dinary_analytics.ai_service import (
    _run_query,
    delete_view_tool,
    get_config_tool,
    get_view_tool,
    health,
    list_views,
    refresh_now,
    save_view_tool,
    set_config_tool,
)
from dinary_analytics.connection import LEDGER_SCHEMA
from dinary_analytics.refresh import get_db_path, start_refresh_daemon


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
    monkeypatch.setattr(refresh_module, "_db_path", db)
    return db


@allure.epic("Analytics")
@allure.feature("AI Service")
def test_run_query_returns_json_array(patched_replica):
    result = _run_query("SELECT id, amount FROM ledger.expenses ORDER BY id")
    data = json.loads(result)
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["id"] == 1
    assert data[0]["amount"] == pytest.approx(12.5)
    assert data[1]["amount"] == pytest.approx(50.0)


@allure.epic("Analytics")
@allure.feature("AI Service")
def test_run_query_empty_result(patched_replica):
    result = _run_query("SELECT * FROM ledger.expenses WHERE amount > 9999")
    data = json.loads(result)
    assert data == []


@allure.epic("Analytics")
@allure.feature("AI Service")
def test_run_query_aggregate(patched_replica):
    result = _run_query("SELECT SUM(amount) AS total FROM ledger.expenses")
    data = json.loads(result)
    assert data[0]["total"] == pytest.approx(62.5)


@allure.epic("Analytics")
@allure.feature("AI Service")
def test_run_query_wraps_duckdb_error(patched_replica):
    """A query DuckDB rejects (e.g. unknown table) must surface as ToolError, not leak raw."""
    with pytest.raises(ToolError, match="query failed"):
        _run_query("SELECT * FROM ledger.no_such_table")


@allure.epic("Analytics")
@allure.feature("AI Service")
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
@allure.feature("AI Service")
def test_get_config_tool_missing_returns_empty(patched_analytics_db):
    result = get_config_tool("nonexistent")
    assert result == ""


@allure.epic("Analytics")
@allure.feature("AI Service")
def test_set_and_get_config_tool(patched_analytics_db):
    result = set_config_tool("mykey", "myvalue")
    assert result == "ok"
    assert get_config_tool("mykey") == "myvalue"


@allure.epic("Analytics")
@allure.feature("AI Service")
def test_list_views_empty(patched_analytics_db):
    result = json.loads(list_views())
    assert result == []


@allure.epic("Analytics")
@allure.feature("AI Service")
def test_save_view_tool_assigns_id(patched_analytics_db):
    config_json = json.dumps({"name": "Test View", "baskets": []})
    view_id = save_view_tool(config_json)
    assert view_id


@allure.epic("Analytics")
@allure.feature("AI Service")
def test_list_views_after_save(patched_analytics_db):
    config_json = json.dumps({"name": "Listed View", "baskets": []})
    view_id = save_view_tool(config_json)
    views = json.loads(list_views())
    assert any(v["id"] == view_id for v in views)
    assert any(v["name"] == "Listed View" for v in views)


@allure.epic("Analytics")
@allure.feature("AI Service")
def test_get_view_tool_returns_config(patched_analytics_db):
    config_json = json.dumps({"name": "GetMe", "baskets": []})
    view_id = save_view_tool(config_json)
    result = json.loads(get_view_tool(view_id))
    assert result["name"] == "GetMe"
    assert result["id"] == view_id


@allure.epic("Analytics")
@allure.feature("AI Service")
def test_get_view_tool_missing_returns_empty(patched_analytics_db):
    result = get_view_tool("no-such-id")
    assert result == ""


@allure.epic("Analytics")
@allure.feature("AI Service")
def test_delete_view_tool(patched_analytics_db):
    config_json = json.dumps({"name": "ToDelete", "baskets": []})
    view_id = save_view_tool(config_json)
    result = delete_view_tool(view_id)
    assert result == "ok"
    assert get_view_tool(view_id) == ""
    views = json.loads(list_views())
    assert not any(v["id"] == view_id for v in views)


@allure.epic("Analytics")
@allure.feature("AI Service")
def test_tool_handler_returns_error_when_no_db(monkeypatch):
    monkeypatch.setattr(refresh_module, "_db_path", None)
    with pytest.raises(ToolError):
        _run_query("SELECT 1")


@allure.epic("Analytics")
@allure.feature("AI Service")
def test_startup_refresh_failure_still_starts(monkeypatch, caplog):
    def one_shot_loop():
        try:
            refresh_module.refresh_replica()
        except refresh_module.RefreshError as exc:
            refresh_module.logger.warning("ledger replica refresh failed: %s", exc)

    monkeypatch.setattr(refresh_module, "_daemon_thread", None)
    monkeypatch.setattr(refresh_module, "_db_path", None)
    monkeypatch.setattr(refresh_module, "_refresh_loop", one_shot_loop)
    monkeypatch.setattr(
        refresh_module,
        "refresh_replica",
        MagicMock(side_effect=refresh_module.RefreshError("dinary-host unreachable")),
    )

    with caplog.at_level(logging.WARNING, logger=refresh_module.logger.name):
        start_refresh_daemon()
        thread = refresh_module._daemon_thread
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert get_db_path() is None
    assert any("dinary-host unreachable" in record.message for record in caplog.records)


@allure.epic("Analytics")
@allure.feature("AI Service")
def test_health_ok(tmp_path, monkeypatch):
    db_path = tmp_path / "replica.db"
    refreshed_at = time.time()
    monkeypatch.setattr(refresh_module, "_db_path", db_path)
    monkeypatch.setattr(refresh_module, "_last_refresh", refreshed_at)
    monkeypatch.setattr(refresh_module, "_last_refresh_error", None)

    response = asyncio.run(health(MagicMock()))

    body = json.loads(response.body)
    assert body["ok"] is True
    assert body["error"] is None
    expected = datetime.datetime.fromtimestamp(refreshed_at, tz=datetime.timezone.utc).isoformat()
    assert body["last_refresh"] == expected


@allure.epic("Analytics")
@allure.feature("AI Service")
def test_health_degraded_no_db(monkeypatch):
    monkeypatch.setattr(refresh_module, "_db_path", None)
    monkeypatch.setattr(refresh_module, "_last_refresh", None)
    monkeypatch.setattr(refresh_module, "_last_refresh_error", "dinary-host unreachable")

    response = asyncio.run(health(MagicMock()))

    body = json.loads(response.body)
    assert body["ok"] is False
    assert body["last_refresh"] is None
    assert body["error"] == "dinary-host unreachable"


@allure.epic("Analytics")
@allure.feature("AI Service")
def test_health_ok_with_stale_data(tmp_path, monkeypatch):
    db_path = tmp_path / "replica.db"
    refreshed_at = time.time() - 90_000
    monkeypatch.setattr(refresh_module, "_db_path", db_path)
    monkeypatch.setattr(refresh_module, "_last_refresh", refreshed_at)
    monkeypatch.setattr(refresh_module, "_last_refresh_error", "dinary-host unreachable")

    response = asyncio.run(health(MagicMock()))

    body = json.loads(response.body)
    assert body["ok"] is True
    assert body["error"] == "dinary-host unreachable"


@allure.epic("Analytics")
@allure.feature("AI Service")
def test_refresh_now_triggers_loop(monkeypatch):
    fake_trigger = MagicMock()
    monkeypatch.setattr(ai_service_module, "trigger_refresh_now", fake_trigger)

    response = asyncio.run(refresh_now(MagicMock()))

    fake_trigger.assert_called_once()
    assert json.loads(response.body) == {"triggered": True}

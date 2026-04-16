from datetime import date
from unittest.mock import patch

import allure
import pytest

from dinary.services import duckdb_repo


@pytest.fixture(autouse=True)
def _tmp_duckdb(tmp_path, monkeypatch):
    """Isolate DuckDB to temp dir for all API tests."""
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "CONFIG_DB", tmp_path / "config.duckdb")
    duckdb_repo.init_config_db()

    con = duckdb_repo.get_config_connection(read_only=False)
    try:
        con.execute("INSERT INTO category_groups VALUES (1, '', NULL)")
        con.execute("INSERT INTO category_groups VALUES (2, 'Essentials', NULL)")
        con.execute("INSERT INTO category_groups VALUES (3, 'путешествия', NULL)")
        con.execute("INSERT INTO categories VALUES (1, 'Food', 2)")
        con.execute("INSERT INTO categories VALUES (2, 'Transport', 2)")
        con.execute("INSERT INTO categories VALUES (3, 'кафе', 3)")
        con.execute(
            "INSERT INTO sheet_category_mapping VALUES (0, 'Food', 'Essentials', 1, NULL, NULL, NULL, NULL)"
        )
        con.execute(
            "INSERT INTO sheet_category_mapping VALUES (0, 'Transport', 'Essentials', 2, NULL, NULL, NULL, NULL)"
        )
        con.execute(
            "INSERT INTO sheet_category_mapping VALUES (0, 'кафе', 'путешествия', 3, NULL, NULL, NULL, NULL)"
        )
    finally:
        con.close()


@allure.epic("API")
@allure.feature("Health")
def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


@allure.epic("API")
@allure.feature("Version")
def test_version(client):
    resp = client.get("/api/version")
    assert resp.status_code == 200
    assert "version" in resp.json()


@allure.epic("API")
@allure.feature("Categories")
def test_categories(client):
    resp = client.get("/api/categories")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2
    names = {d["name"] for d in data}
    assert "Food" in names


@allure.epic("API")
@allure.feature("Categories")
def test_categories_db_failure(client, monkeypatch):
    """If config.duckdb is unreadable, /api/categories returns 502."""

    def bad_connection(**kwargs):
        raise RuntimeError("DB corrupted")

    monkeypatch.setattr(duckdb_repo, "get_config_connection", bad_connection)
    resp = client.get("/api/categories")
    assert resp.status_code == 502
    assert "Failed to load categories" in resp.json()["detail"]


@allure.epic("API")
@allure.feature("Expenses")
@patch("dinary.api.expenses.schedule_sync")
def test_create_expense(mock_sync, client):
    resp = client.post(
        "/api/expenses",
        json={
            "expense_id": "test-uuid-1",
            "amount": 1500,
            "currency": "RSD",
            "category": "Food",
            "group": "Essentials",
            "comment": "lunch",
            "date": "2026-04-14",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "created"
    assert data["expense_id"] == "test-uuid-1"
    assert data["category"] == "Food"
    assert data["amount_rsd"] == 1500.0
    mock_sync.assert_called_once()


@allure.epic("API")
@allure.feature("Expenses")
@patch("dinary.api.expenses.schedule_sync")
def test_create_expense_unknown_category(mock_sync, client):
    resp = client.post(
        "/api/expenses",
        json={
            "expense_id": "test-uuid-2",
            "amount": 100,
            "category": "Nonexistent",
            "group": "",
            "date": "2026-04-14",
        },
    )
    assert resp.status_code == 422
    assert "Unknown category" in resp.json()["detail"]


@allure.epic("API")
@allure.feature("Expenses")
def test_create_expense_validation(client):
    resp = client.post(
        "/api/expenses",
        json={"expense_id": "x", "amount": -5, "category": "Food", "date": "2026-04-14"},
    )
    assert resp.status_code == 422

    resp = client.post(
        "/api/expenses",
        json={
            "expense_id": "x",
            "amount": 100,
            "category": "Food",
            "currency": "USD",
            "date": "2026-04-14",
        },
    )
    assert resp.status_code == 422


@allure.epic("API")
@allure.feature("Expenses")
def test_create_expense_missing_expense_id(client):
    resp = client.post(
        "/api/expenses",
        json={"amount": 100, "category": "Food", "group": "Essentials", "date": "2026-04-14"},
    )
    assert resp.status_code == 422


@allure.epic("Data Safety")
@allure.feature("Deduplication")
@patch("dinary.api.expenses.schedule_sync")
def test_identical_expense_submitted_twice_creates_one_entry(mock_sync, client):
    """POST the same expense twice with the same expense_id.
    The second should return 'duplicate', not create a second row.
    """
    payload = {
        "expense_id": "dedup-test-1",
        "amount": 1500,
        "currency": "RSD",
        "category": "Food",
        "group": "Essentials",
        "comment": "lunch",
        "date": "2026-04-14",
    }

    resp1 = client.post("/api/expenses", json=payload)
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "created"

    resp2 = client.post("/api/expenses", json=payload)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "duplicate"


@allure.epic("Data Safety")
@allure.feature("Deduplication")
@patch("dinary.api.expenses.schedule_sync")
def test_retry_after_successful_write_does_not_double_count(mock_sync, client):
    """Simulate: POST succeeds, client retries with same expense_id.
    The total in DuckDB must not double.
    """
    payload = {
        "expense_id": "dedup-test-2",
        "amount": 1500,
        "currency": "RSD",
        "category": "Food",
        "group": "Essentials",
        "comment": "lunch",
        "date": "2026-04-14",
    }

    client.post("/api/expenses", json=payload)
    client.post("/api/expenses", json=payload)

    con = duckdb_repo.get_budget_connection(2026)
    try:
        total = con.execute("SELECT SUM(amount) FROM expenses").fetchone()
        assert float(total[0]) == 1500.0
    finally:
        con.close()


@allure.epic("Data Safety")
@allure.feature("Deduplication")
@patch("dinary.api.expenses.schedule_sync")
def test_conflict_on_different_payload(mock_sync, client):
    """Same expense_id but different amount -> 409 Conflict."""
    client.post(
        "/api/expenses",
        json={
            "expense_id": "conflict-test-1",
            "amount": 1500,
            "category": "Food",
            "group": "Essentials",
            "date": "2026-04-14",
        },
    )

    resp = client.post(
        "/api/expenses",
        json={
            "expense_id": "conflict-test-1",
            "amount": 2000,
            "category": "Food",
            "group": "Essentials",
            "date": "2026-04-14",
        },
    )
    assert resp.status_code == 409


@allure.epic("API")
@allure.feature("QR Parse")
@patch("dinary.api.qr.parse_receipt_url")
def test_qr_parse(mock_parse, client):
    from dinary.services.qr_parser import ReceiptData

    mock_parse.return_value = ReceiptData(amount=3450.0, date=date(2026, 4, 10))

    resp = client.post(
        "/api/qr/parse",
        json={"url": "https://suf.purs.gov.rs/v/?vl=abc123"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["amount"] == 3450.0
    assert data["date"] == "2026-04-10"


@allure.epic("API")
@allure.feature("QR Parse")
@patch("dinary.api.qr.parse_receipt_url")
def test_qr_parse_failure(mock_parse, client):
    mock_parse.side_effect = Exception("could not fetch")

    resp = client.post(
        "/api/qr/parse",
        json={"url": "https://suf.purs.gov.rs/v/?vl=bad"},
    )
    assert resp.status_code == 502


@allure.epic("API")
@allure.feature("Expenses")
@allure.story("Travel forward lookup")
@patch("dinary.api.expenses.schedule_sync")
def test_create_travel_expense_resolves_event(mock_sync, client):
    """POST with group='путешествия' creates a synthetic event and stores it."""
    resp = client.post(
        "/api/expenses",
        json={
            "expense_id": "travel-uuid-1",
            "amount": 3000,
            "currency": "RSD",
            "category": "кафе",
            "group": "путешествия",
            "comment": "cafe abroad",
            "date": "2026-07-15",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "created"

    con = duckdb_repo.get_budget_connection(2026)
    try:
        row = con.execute("SELECT event_id FROM expenses WHERE id = 'travel-uuid-1'").fetchone()
        assert row is not None
        assert row[0] is not None

        event = con.execute("SELECT name FROM config.events WHERE id = ?", [row[0]]).fetchone()
        assert event[0] == "отпуск-2026"
    finally:
        con.close()


@allure.epic("Data Safety")
@allure.feature("Deduplication")
@patch("dinary.api.expenses.schedule_sync")
def test_sync_not_called_on_duplicate(mock_sync, client):
    """schedule_sync must not be called for duplicate submissions."""
    payload = {
        "expense_id": "sync-dedup-1",
        "amount": 500,
        "currency": "RSD",
        "category": "Food",
        "group": "Essentials",
        "date": "2026-04-14",
    }

    client.post("/api/expenses", json=payload)
    mock_sync.reset_mock()

    resp = client.post("/api/expenses", json=payload)
    assert resp.json()["status"] == "duplicate"
    mock_sync.assert_not_called()


@allure.epic("Data Safety")
@allure.feature("Referential Integrity")
@patch("dinary.api.expenses.schedule_sync")
def test_referential_integrity_error_returns_422(mock_sync, client, monkeypatch):
    """ValueError from insert_expense (bad dimension ID) surfaces as 422, not 500."""
    from dinary.services import duckdb_repo as repo

    def bad_insert(*args, **kwargs):
        raise ValueError("category_id 999 not found in config.categories")

    monkeypatch.setattr(repo, "insert_expense", bad_insert)

    resp = client.post(
        "/api/expenses",
        json={
            "expense_id": "ri-api-1",
            "amount": 100,
            "category": "Food",
            "group": "Essentials",
            "date": "2026-04-14",
        },
    )
    assert resp.status_code == 422
    assert "category_id" in resp.json()["detail"]

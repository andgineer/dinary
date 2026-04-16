from datetime import date
from unittest.mock import AsyncMock, patch

import allure

from dinary.services.category_store import Category


@allure.epic("API")
@allure.feature("Health")
def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


@allure.epic("API")
@allure.feature("Categories")
@patch("dinary.api.categories.get_categories")
def test_categories(mock_get, client):
    mock_get.return_value = [
        Category(name="Food", group="Essentials"),
        Category(name="Transport", group="Essentials"),
        Category(name="Cinema", group="Entertainment"),
    ]
    resp = client.get("/api/categories")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    assert data[0]["name"] == "Food"
    assert data[0]["group"] == "Essentials"


@allure.epic("API")
@allure.feature("Categories")
@patch("dinary.api.categories.get_categories")
def test_categories_failure(mock_get, client):
    mock_get.side_effect = Exception("sheets down")
    resp = client.get("/api/categories")
    assert resp.status_code == 502


@allure.epic("API")
@allure.feature("Expenses")
@patch("dinary.api.expenses.sheets")
def test_create_expense(mock_sheets, client):
    mock_sheets.validate_category.return_value = True
    mock_sheets.write_expense = AsyncMock(
        return_value={
            "month": "2026-04",
            "category": "Food",
            "amount_rsd": 1500.0,
            "amount_eur": 12.79,
            "new_total_rsd": 3000.0,
        }
    )

    resp = client.post(
        "/api/expenses",
        json={
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
    assert data["category"] == "Food"
    assert data["amount_rsd"] == 1500.0


@allure.epic("API")
@allure.feature("Expenses")
@patch("dinary.api.expenses.sheets")
def test_create_expense_unknown_category(mock_sheets, client):
    mock_sheets.validate_category.return_value = False

    resp = client.post(
        "/api/expenses",
        json={
            "amount": 100,
            "category": "Nonexistent",
            "group": "",
            "date": "2026-04-14",
        },
    )
    assert resp.status_code == 400
    assert "Unknown category" in resp.json()["detail"]


@allure.epic("API")
@allure.feature("Expenses")
@patch("dinary.api.expenses.sheets")
def test_create_expense_sheets_failure(mock_sheets, client):
    mock_sheets.validate_category.return_value = True
    mock_sheets.write_expense = AsyncMock(side_effect=Exception("sheets down"))

    resp = client.post(
        "/api/expenses",
        json={"amount": 100, "category": "Food", "group": "Essentials", "date": "2026-04-14"},
    )
    assert resp.status_code == 502
    assert "queued for retry" in resp.json()["detail"]


@allure.epic("API")
@allure.feature("Expenses")
def test_create_expense_validation(client):
    resp = client.post(
        "/api/expenses",
        json={"amount": -5, "category": "Food", "date": "2026-04-14"},
    )
    assert resp.status_code == 422

    resp = client.post(
        "/api/expenses",
        json={"amount": 100, "category": "Food", "currency": "USD", "date": "2026-04-14"},
    )
    assert resp.status_code == 422


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

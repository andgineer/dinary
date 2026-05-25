"""API tests for GET/POST/PATCH/DELETE /api/incomes."""

from decimal import Decimal
from unittest.mock import patch

import allure
import pytest

from dinary.config import settings

from _api_helpers import db  # noqa: F401


def _mock_get_rate(con, rate_date, source, target, *, offline=False):
    return Decimal("120")


@allure.epic("Income")
@allure.feature("API")
class TestIncomeApi:
    def test_post_creates_201(self, client):
        with patch("dinary.api.controllers.income.get_rate", side_effect=_mock_get_rate):
            resp = client.post(
                "/api/incomes",
                json={
                    "year": 2026,
                    "month": 5,
                    "amount_original": 540.0,
                    "currency_original": "EUR",
                },
            )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["year"] == 2026
        assert data["month"] == 5
        assert data["amount"] > 0

    def test_post_passthrough_accounting_currency(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        resp = client.post(
            "/api/incomes",
            json={"year": 2026, "month": 6, "amount_original": 540.0, "currency_original": "EUR"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["amount"] == pytest.approx(540.0)

    def test_post_duplicate_409(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        payload = {"year": 2026, "month": 5, "amount_original": 540.0, "currency_original": "EUR"}
        assert client.post("/api/incomes", json=payload).status_code == 201
        assert client.post("/api/incomes", json=payload).status_code == 409

    def test_get_returns_items(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        client.post(
            "/api/incomes",
            json={"year": 2026, "month": 5, "amount_original": 540.0, "currency_original": "EUR"},
        )
        resp = client.get("/api/incomes?page=1&page_size=20")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "items" in data
        assert "has_more" in data
        assert len(data["items"]) == 1

    def test_patch_updates_amount(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        client.post(
            "/api/incomes",
            json={"year": 2026, "month": 5, "amount_original": 540.0, "currency_original": "EUR"},
        )
        resp = client.patch(
            "/api/incomes/2026/5",
            json={"amount_original": 600.0, "currency_original": "EUR"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["amount"] == pytest.approx(600.0)

    def test_patch_404_if_missing(self, client):
        resp = client.patch(
            "/api/incomes/2026/5",
            json={"amount_original": 600.0, "currency_original": "EUR"},
        )
        assert resp.status_code == 404

    def test_delete_removes_income(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        client.post(
            "/api/incomes",
            json={"year": 2026, "month": 5, "amount_original": 540.0, "currency_original": "EUR"},
        )
        assert client.delete("/api/incomes/2026/5").status_code == 204
        assert len(client.get("/api/incomes?page=1&page_size=20").json()["items"]) == 0

    def test_delete_404_if_missing(self, client):
        assert client.delete("/api/incomes/2026/5").status_code == 404

    def test_patch_currency_only_returns_422(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        client.post(
            "/api/incomes",
            json={"year": 2026, "month": 5, "amount_original": 540.0, "currency_original": "EUR"},
        )
        resp = client.patch("/api/incomes/2026/5", json={"currency_original": "RSD"})
        assert resp.status_code == 422

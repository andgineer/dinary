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
    def test_post_creates_204(self, client):
        with patch("dinary.adapters.exchange_rates.get_rate", side_effect=_mock_get_rate):
            resp = client.post(
                "/api/incomes",
                json={
                    "year": 2026,
                    "month": 5,
                    "income_date": "2026-05-15",
                    "amount_original": 540.0,
                    "currency_original": "EUR",
                },
            )
        assert resp.status_code == 204, resp.text
        item = client.get("/api/incomes?page=1&page_size=20").json()["items"][0]
        assert item["year"] == 2026
        assert item["month"] == 5
        assert item["income_date"] == "2026-05-15"
        assert item["amount"] > 0
        assert "id" in item

    def test_post_passthrough_accounting_currency(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        resp = client.post(
            "/api/incomes",
            json={
                "year": 2026,
                "month": 6,
                "income_date": "2026-06-01",
                "amount_original": 540.0,
                "currency_original": "EUR",
            },
        )
        assert resp.status_code == 204, resp.text
        item = client.get("/api/incomes?page=1&page_size=20").json()["items"][0]
        assert item["amount"] == pytest.approx(540.0)

    def test_post_with_comment(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        resp = client.post(
            "/api/incomes",
            json={
                "year": 2026,
                "month": 5,
                "income_date": "2026-05-15",
                "amount_original": 540.0,
                "currency_original": "EUR",
                "comment": "salary",
            },
        )
        assert resp.status_code == 204, resp.text
        item = client.get("/api/incomes?page=1&page_size=20").json()["items"][0]
        assert item["comment"] == "salary"

    def test_post_multiple_same_month_allowed(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        payload = {
            "year": 2026,
            "month": 5,
            "income_date": "2026-05-15",
            "amount_original": 540.0,
            "currency_original": "EUR",
        }
        assert client.post("/api/incomes", json=payload).status_code == 204
        payload2 = {
            "year": 2026,
            "month": 5,
            "income_date": "2026-05-20",
            "amount_original": 300.0,
            "currency_original": "EUR",
        }
        assert client.post("/api/incomes", json=payload2).status_code == 204

    def test_get_returns_items(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        client.post(
            "/api/incomes",
            json={
                "year": 2026,
                "month": 5,
                "income_date": "2026-05-15",
                "amount_original": 540.0,
                "currency_original": "EUR",
            },
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
            json={
                "year": 2026,
                "month": 5,
                "income_date": "2026-05-15",
                "amount_original": 540.0,
                "currency_original": "EUR",
            },
        )
        created_id = client.get("/api/incomes?page=1&page_size=20").json()["items"][0]["id"]
        resp = client.patch(
            f"/api/incomes/{created_id}",
            json={"amount_original": 600.0, "currency_original": "EUR"},
        )
        assert resp.status_code == 204, resp.text
        item = client.get("/api/incomes?page=1&page_size=20").json()["items"][0]
        assert item["amount"] == pytest.approx(600.0)

    def test_patch_updates_comment(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        client.post(
            "/api/incomes",
            json={
                "year": 2026,
                "month": 5,
                "income_date": "2026-05-15",
                "amount_original": 540.0,
                "currency_original": "EUR",
            },
        )
        created_id = client.get("/api/incomes?page=1&page_size=20").json()["items"][0]["id"]
        resp = client.patch(f"/api/incomes/{created_id}", json={"comment": "bonus"})
        assert resp.status_code == 204, resp.text
        item = client.get("/api/incomes?page=1&page_size=20").json()["items"][0]
        assert item["comment"] == "bonus"

    def test_patch_404_if_missing(self, client):
        resp = client.patch(
            "/api/incomes/9999", json={"amount_original": 600.0, "currency_original": "EUR"}
        )
        assert resp.status_code == 404

    def test_delete_removes_income(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        client.post(
            "/api/incomes",
            json={
                "year": 2026,
                "month": 5,
                "income_date": "2026-05-15",
                "amount_original": 540.0,
                "currency_original": "EUR",
            },
        )
        created_id = client.get("/api/incomes?page=1&page_size=20").json()["items"][0]["id"]
        assert client.delete(f"/api/incomes/{created_id}").status_code == 204
        assert len(client.get("/api/incomes?page=1&page_size=20").json()["items"]) == 0

    def test_delete_404_if_missing(self, client):
        assert client.delete("/api/incomes/9999").status_code == 404

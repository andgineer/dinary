"""API tests for GET /api/analytics/summary."""

from decimal import Decimal
from unittest.mock import patch

import allure

from dinary.config import settings

from _api_helpers import db  # noqa: F401


def _mock_get_rate(con, rate_date, source, target, *, offline=False):
    return Decimal("1")


def _insert_expense(client, date_str, amount, category_id=1):
    with patch("dinary.api.controllers.expenses.get_rate", side_effect=_mock_get_rate):
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": f"test-{date_str}-{amount}",
                "amount": float(amount),
                "currency": "EUR",
                "category_id": category_id,
                "expense_datetime": f"{date_str}T12:00:00+00:00",
            },
        )
    assert resp.status_code == 200, resp.text


def _insert_income(client, year, month, amount):
    with patch("dinary.api.controllers.income.get_rate", side_effect=_mock_get_rate):
        resp = client.post(
            "/api/incomes",
            json={
                "year": year,
                "month": month,
                "income_date": f"{year}-{month:02d}-01",
                "amount_original": float(amount),
                "currency_original": "EUR",
            },
        )
    assert resp.status_code == 201, resp.text


@allure.epic("Analytics")
@allure.feature("API")
class TestAnalyticsSummaryEmpty:
    def test_returns_200(self, client):
        resp = client.get("/api/analytics/summary")
        assert resp.status_code == 200

    def test_response_has_required_keys(self, client):
        data = client.get("/api/analytics/summary").json()
        assert "summary" in data
        assert "events" in data
        assert "trends" in data

    def test_empty_db_zero_amounts(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        data = client.get("/api/analytics/summary").json()
        s = data["summary"]
        assert s["this_month_total"] == "0"
        assert s["last_month_total"] == "0"
        assert s["ytd_total"] == "0"
        assert s["ytd_savings"] == "0"
        assert s["savings_rate"] == "0%"

    def test_no_trends_when_insufficient_data(self, client):
        data = client.get("/api/analytics/summary").json()
        assert data["trends"] is None

    def test_currency_matches_accounting_currency(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        data = client.get("/api/analytics/summary").json()
        assert data["summary"]["currency"] == "EUR"


@allure.epic("Analytics")
@allure.feature("API")
class TestAnalyticsSummaryStats:
    def test_ytd_total_sums_current_year_expenses(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        from datetime import date

        y = date.today().year
        _insert_expense(client, f"{y}-01-15", 100)
        _insert_expense(client, f"{y}-03-10", 200)
        data = client.get("/api/analytics/summary").json()
        assert data["summary"]["ytd_total"] == "300"

    def test_ytd_savings_income_minus_expenses(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        from datetime import date

        y = date.today().year
        _insert_income(client, y, 1, 1000)
        _insert_expense(client, f"{y}-01-15", 400)
        data = client.get("/api/analytics/summary").json()
        assert data["summary"]["ytd_savings"] == "600"

    def test_savings_rate_calculation(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        from datetime import date

        y = date.today().year
        _insert_income(client, y, 1, 1000)
        _insert_expense(client, f"{y}-01-15", 250)
        data = client.get("/api/analytics/summary").json()
        assert data["summary"]["savings_rate"] == "75%"

    def test_amount_formatted_with_spaces(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        from datetime import date

        y = date.today().year
        _insert_expense(client, f"{y}-01-15", 1234567)
        data = client.get("/api/analytics/summary").json()
        assert data["summary"]["ytd_total"] == "1 234 567"


@allure.epic("Analytics")
@allure.feature("API")
class TestAnalyticsEvents:
    def test_event_in_last_12_months_appears(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        from datetime import date, timedelta
        from dinary.db import storage

        today = date.today()
        six_ago = (today - timedelta(days=180)).isoformat()
        five_ago = (today - timedelta(days=150)).isoformat()
        db_con = storage.get_connection()
        db_con.execute(
            "INSERT INTO events (id,name,date_from,date_to,auto_attach_enabled,is_active)"
            " VALUES (10,'RecentTrip',?,?,0,1)",
            (six_ago, five_ago),
        )
        db_con.close()
        data = client.get("/api/analytics/summary").json()
        assert any(e["name"] == "RecentTrip" for e in data["events"])

    def test_open_event_flagged(self, client):
        from datetime import date, timedelta
        from dinary.db import storage

        today = date.today()
        future = (today + timedelta(days=30)).isoformat()
        db_con = storage.get_connection()
        db_con.execute(
            "INSERT INTO events (id,name,date_from,date_to,auto_attach_enabled,is_active)"
            " VALUES (11,'OpenTrip',?,?,0,1)",
            (today.isoformat(), future),
        )
        db_con.close()
        data = client.get("/api/analytics/summary").json()
        ev = next(e for e in data["events"] if e["name"] == "OpenTrip")
        assert ev["open"] is True

    def test_closed_event_not_flagged(self, client):
        from datetime import date, timedelta
        from dinary.db import storage

        today = date.today()
        past_from = (today - timedelta(days=60)).isoformat()
        past_to = (today - timedelta(days=30)).isoformat()
        db_con = storage.get_connection()
        db_con.execute(
            "INSERT INTO events (id,name,date_from,date_to,auto_attach_enabled,is_active)"
            " VALUES (12,'ClosedTrip',?,?,0,1)",
            (past_from, past_to),
        )
        db_con.close()
        data = client.get("/api/analytics/summary").json()
        ev = next(e for e in data["events"] if e["name"] == "ClosedTrip")
        assert ev["open"] is False

    def test_event_older_than_12_months_excluded(self, client):
        from datetime import date, timedelta
        from dinary.db import storage

        old = (date.today() - timedelta(days=400)).isoformat()
        db_con = storage.get_connection()
        db_con.execute(
            "INSERT INTO events (id,name,date_from,date_to,auto_attach_enabled,is_active)"
            " VALUES (13,'OldTrip',?,?,0,1)",
            (old, old),
        )
        db_con.close()
        data = client.get("/api/analytics/summary").json()
        assert not any(e["name"] == "OldTrip" for e in data["events"])

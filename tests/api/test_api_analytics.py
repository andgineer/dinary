"""API tests for GET /api/analytics/summary and GET /api/analytics/db-snapshot."""

import sqlite3
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import allure

from dinary.config import settings
from dinary.db import storage

from _api_helpers import db  # noqa: F401


def _mock_get_rate(con, rate_date, source, target, *, offline=False):
    return Decimal("1")


def _insert_expense(client, date_str, amount, category_id=1):
    with patch("dinary.adapters.rates.service.get_rate", side_effect=_mock_get_rate):
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
    with patch("dinary.adapters.rates.service.get_rate", side_effect=_mock_get_rate):
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
    assert resp.status_code == 204, resp.text


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
class TestAnalyticsAutoTrendsVisibility:
    def test_inactive_category_with_recent_expenses_still_shown_in_trends(
        self,
        client,
        monkeypatch,
    ):
        """A category that fell out of the active template (``is_active=0``)
        but has fresh expenses must still surface in trends — analytics
        reads expenses directly, regardless of any visibility flag."""
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        from datetime import date, timedelta

        recent = (date.today() - timedelta(days=30)).isoformat()
        prior = (date.today() - timedelta(days=120)).isoformat()

        db_con = storage.get_connection()
        try:
            db_con.execute(
                "INSERT INTO expenses"
                " (datetime, amount, amount_original, currency_original, category_id)"
                " VALUES (?, ?, ?, 'EUR', 1)",
                (f"{recent}T12:00:00", 1000.0, 1000.0),
            )
            db_con.execute(
                "INSERT INTO expenses"
                " (datetime, amount, amount_original, currency_original, category_id)"
                " VALUES (?, ?, ?, 'EUR', 1)",
                (f"{prior}T12:00:00", 500.0, 500.0),
            )
            db_con.execute("UPDATE categories SET is_active = 0 WHERE id = 1")
        finally:
            db_con.close()

        data = client.get("/api/analytics/summary").json()
        assert data["trends"] is not None
        assert any(t["basket_name"] == "Food" for t in data["trends"])


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


def _spy_named_temporary_file(monkeypatch, created: list[Path]):
    original = tempfile.NamedTemporaryFile

    def spy(*args, **kwargs):
        f = original(*args, **kwargs)
        created.append(Path(f.name))
        return f

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", spy)


@allure.epic("Analytics")
@allure.feature("DB Snapshot")
class TestDbSnapshot:
    def test_db_snapshot_returns_valid_sqlite_file(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        from datetime import date

        y = date.today().year
        _insert_expense(client, f"{y}-01-15", 100)
        _insert_expense(client, f"{y}-03-10", 200)

        resp = client.get("/api/analytics/db-snapshot")
        assert resp.status_code == 200

        snapshot_path = tmp_path / "snapshot.db"
        snapshot_path.write_bytes(resp.content)
        snap_con = sqlite3.connect(snapshot_path)
        try:
            snap_count = snap_con.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
        finally:
            snap_con.close()

        live_con = storage.get_connection()
        try:
            live_count = live_con.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
        finally:
            live_con.close()

        assert snap_count == live_count

    def test_db_snapshot_cleans_up_temp_file(self, client, monkeypatch):
        created: list[Path] = []
        _spy_named_temporary_file(monkeypatch, created)

        resp = client.get("/api/analytics/db-snapshot")
        assert resp.status_code == 200
        assert resp.content  # fully consume the body so the BackgroundTask runs

        assert created
        assert not created[0].exists()

    def test_db_snapshot_cleans_up_temp_file_on_backup_failure(self, client, monkeypatch):
        """``sqlite3.Connection.backup`` can't be monkeypatched directly (C-level
        immutable type), so ``sqlite3.connect`` is wrapped to return a non-Connection
        stand-in for the route's single-positional-arg ``target`` call, forcing
        ``source.backup(target)`` to raise."""
        created: list[Path] = []
        _spy_named_temporary_file(monkeypatch, created)

        real_connect = sqlite3.connect

        class _UnbackupableConnection:
            def __init__(self, real: sqlite3.Connection):
                self._real = real

            def close(self) -> None:
                self._real.close()

        def spy_connect(database, *args, **kwargs):
            con = real_connect(database, *args, **kwargs)
            if not args and not kwargs:
                return _UnbackupableConnection(con)
            return con

        monkeypatch.setattr(sqlite3, "connect", spy_connect)

        resp = client.get("/api/analytics/db-snapshot")
        assert resp.status_code >= 500

        assert created
        assert not created[0].exists()

    def test_db_snapshot_consistent_under_concurrent_write(self, client, monkeypatch, tmp_path):
        """Wraps ``get_connection`` in a proxy whose ``backup`` commits an extra
        expense via a second connection right before delegating to the real Online
        Backup API. Either outcome is fine; a torn read (count between the two
        valid values) is not."""
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        from datetime import date

        from dinary.api import analytics as analytics_module

        y = date.today().year
        _insert_expense(client, f"{y}-01-15", 100)

        before_con = storage.get_connection()
        try:
            before_count = before_con.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
        finally:
            before_con.close()

        real_get_connection = analytics_module.get_connection

        class _ConcurrentWriteSource:
            def __init__(self, real: sqlite3.Connection):
                self._real = real

            def backup(self, target, *args, **kwargs):
                writer = storage.get_connection()
                try:
                    writer.execute(
                        "INSERT INTO expenses"
                        " (datetime, amount, amount_original, currency_original, category_id)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (f"{y}-06-01 12:00:00", 1.0, 1.0, "EUR", 1),
                    )
                    writer.commit()
                finally:
                    writer.close()
                return self._real.backup(target, *args, **kwargs)

            def close(self) -> None:
                self._real.close()

        monkeypatch.setattr(
            analytics_module,
            "get_connection",
            lambda: _ConcurrentWriteSource(real_get_connection()),
        )

        resp = client.get("/api/analytics/db-snapshot")
        assert resp.status_code == 200

        snapshot_path = tmp_path / "concurrent-snapshot.db"
        snapshot_path.write_bytes(resp.content)
        snap_con = sqlite3.connect(snapshot_path)
        try:
            snap_count = snap_con.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
        finally:
            snap_con.close()

        assert snap_count in (before_count, before_count + 1)

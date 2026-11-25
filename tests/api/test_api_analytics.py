"""API tests for the /api/analytics endpoints."""

import sqlite3
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import allure

from dinary.api import analytics as analytics_api
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


_DETAIL_EVENT_ID = 50


def _seed_detail_event(expenses: list[tuple[str, float, int]]) -> None:
    """Seed one event plus its expenses as ``(datetime, amount, category_id)``."""
    today = date.today()
    con = storage.get_connection()
    try:
        con.execute(
            "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled, is_active)"
            " VALUES (?, 'DetailTrip', ?, ?, 0, 1)",
            (
                _DETAIL_EVENT_ID,
                (today - timedelta(days=20)).isoformat(),
                (today - timedelta(days=5)).isoformat(),
            ),
        )
        for dt, amount, category_id in expenses:
            con.execute(
                "INSERT INTO expenses"
                " (datetime, amount, amount_original, currency_original, category_id, event_id)"
                " VALUES (?, ?, ?, 'EUR', ?, ?)",
                (dt, amount, amount, category_id, _DETAIL_EVENT_ID),
            )
    finally:
        con.close()


def _day(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _event_detail(client) -> dict:
    resp = client.get(f"/api/analytics/events/{_DETAIL_EVENT_ID}")
    assert resp.status_code == 200, resp.text
    return resp.json()


@allure.epic("Analytics")
@allure.feature("API")
class TestAnalyticsEventDetail:
    def test_header_matches_event(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        _seed_detail_event([(f"{_day(10)} 12:00:00+02:00", 300, 1)])
        data = _event_detail(client)
        assert data["id"] == _DETAIL_EVENT_ID
        assert data["name"] == "DetailTrip"
        assert data["total"] == "300"
        assert data["currency"] == "EUR"
        assert data["open"] is False

    def test_categories_sorted_descending(self, client):
        _seed_detail_event(
            [
                (f"{_day(10)} 12:00:00+02:00", 100, 1),
                (f"{_day(9)} 12:00:00+02:00", 150, 1),
                (f"{_day(9)} 13:00:00+02:00", 400, 2),
            ]
        )
        cats = _event_detail(client)["categories"]
        assert [(c["category_name"], c["group_name"], c["total"]) for c in cats] == [
            ("transit", "Transport", "400"),
            ("food", "Food", "250"),
        ]

    def test_category_without_group_still_counted(self, client):
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO categories (id, name, group_id, is_active, is_retired)"
                " VALUES (9, 'retired-orphan', NULL, FALSE, TRUE)",
            )
        finally:
            con.close()
        _seed_detail_event(
            [
                (f"{_day(10)} 12:00:00+02:00", 100, 1),
                (f"{_day(10)} 13:00:00+02:00", 70, 9),
            ]
        )
        data = _event_detail(client)
        orphan = next(c for c in data["categories"] if c["category_id"] == 9)
        assert orphan["group_name"] is None
        assert sum(int(c["total"].replace(" ", "")) for c in data["categories"]) == int(
            data["total"].replace(" ", ""),
        )

    def test_share_is_fraction_of_event_total(self, client):
        _seed_detail_event(
            [
                (f"{_day(10)} 12:00:00+02:00", 300, 1),
                (f"{_day(10)} 13:00:00+02:00", 100, 2),
            ]
        )
        cats = _event_detail(client)["categories"]
        assert [c["share"] for c in cats] == [0.75, 0.25]
        assert sum(c["share"] for c in cats) == 1.0

    def test_days_sum_per_day_and_are_capped(self, client):
        expenses = [(f"{_day(20 - i)} 12:00:00+02:00", 10 * (i + 1), 1) for i in range(9)]
        expenses.append((f"{_day(12)} 18:00:00+02:00", 5, 2))
        _seed_detail_event(expenses)
        days = _event_detail(client)["days"]
        assert len(days) == 7
        assert [d["date"] for d in days] == [_day(n) for n in range(12, 19)]
        assert days[0]["total"] == "95"
        assert days[1]["total"] == "80"
        assert days[-1]["total"] == "30"

    def test_day_label_is_short(self, client):
        day = date.today() - timedelta(days=10)
        _seed_detail_event([(f"{day.isoformat()} 12:00:00+02:00", 1, 1)])
        label = _event_detail(client)["days"][0]["date_label"]
        assert label == f"{day.day} {day.strftime('%b')}"

    def test_spend_after_local_midnight_keeps_local_date(self, client):
        _seed_detail_event([(f"{_day(10)} 00:30:00+02:00", 50, 1)])
        days = _event_detail(client)["days"]
        assert [d["date"] for d in days] == [_day(10)]

    def test_event_without_expenses_is_empty(self, client):
        _seed_detail_event([])
        data = _event_detail(client)
        assert data["total"] == "0"
        assert data["categories"] == []
        assert data["days"] == []

    def test_unknown_event_is_404(self, client):
        assert client.get("/api/analytics/events/9999").status_code == 404

    def test_amounts_formatted_with_spaces(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "RSD")
        _seed_detail_event([(f"{_day(10)} 12:00:00+02:00", 1234567, 1)])
        data = _event_detail(client)
        assert data["total"] == "1 234 567"
        assert data["categories"][0]["total"] == "1 234 567"
        assert data["categories"][0]["currency"] == "RSD"
        assert data["days"][0]["total"] == "1 234 567"
        assert data["days"][0]["currency"] == "RSD"


def _seed_classification_jobs(statuses: list[tuple[str, str | None]]) -> None:
    conn = storage.get_connection()
    try:
        for i, (status, retry_after) in enumerate(statuses, start=1):
            conn.execute(
                "INSERT INTO receipts (id, client_receipt_id, url) VALUES (?, ?, 'https://x')",
                [i, f"r{i}"],
            )
            conn.execute(
                "INSERT INTO receipt_classification_jobs (receipt_id, status, retry_after)"
                " VALUES (?, ?, ?)",
                [i, status, retry_after],
            )
    finally:
        conn.close()


@allure.epic("Analytics")
@allure.feature("API")
class TestAnalyticsReceiptsQueue:
    def test_zeroed_when_no_jobs(self, client):
        q = client.get("/api/analytics/summary").json()["receipts_queue"]
        assert q == {"pending": 0, "in_progress": 0, "sleeping": 0, "poisoned": 0}

    def test_reflects_queued_jobs(self, client):
        _seed_classification_jobs(
            [
                ("pending", None),
                ("pending", None),
                ("pending", "2999-01-01 00:00:00"),
                ("in_progress", None),
                ("poisoned", None),
            ]
        )
        q = client.get("/api/analytics/summary").json()["receipts_queue"]
        assert q == {"pending": 2, "in_progress": 1, "sleeping": 1, "poisoned": 1}


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


_RECENT = 30
_PRIOR = 120
_CHAIN_ID = 1
_STORE_ID = 1


def _seed_trend_expense(
    days_ago: int,
    amount: float,
    category_id: int = 1,
    *,
    event_id: int | None = None,
    tag_ids: tuple[int, ...] = (),
    item: str | None = None,
) -> None:
    """Seed one expense; with ``item`` it is receipt-backed, bought in the one seeded store."""
    con = storage.get_connection()
    try:
        receipt_id = store_id = None
        if item is not None:
            con.execute(
                "INSERT OR IGNORE INTO shop_chains (id, name) VALUES (?, 'Lidl')",
                (_CHAIN_ID,),
            )
            con.execute(
                "INSERT OR IGNORE INTO stores (id, name, chain_id) VALUES (?, 'Lidl 1', ?)",
                (_STORE_ID, _CHAIN_ID),
            )
            receipt_id = con.execute(
                "INSERT INTO receipts (client_receipt_id, url, store_id) VALUES (?, 'https://x', ?)",
                (f"r-{days_ago}-{amount}-{item}", _STORE_ID),
            ).lastrowid
            store_id = _STORE_ID
        expense_id = con.execute(
            "INSERT INTO expenses (datetime, amount, amount_original, currency_original,"
            " category_id, event_id, receipt_id, store_id)"
            " VALUES (?, ?, ?, 'EUR', ?, ?, ?, ?)",
            (
                f"{_day(days_ago)} 12:00:00",
                amount,
                amount,
                category_id,
                event_id,
                receipt_id,
                store_id,
            ),
        ).lastrowid
        for tag_id in tag_ids:
            con.execute(
                "INSERT INTO expense_tags (expense_id, tag_id) VALUES (?, ?)",
                (expense_id, tag_id),
            )
        if item is not None:
            con.execute(
                "INSERT INTO receipt_items (receipt_id, name_raw, name_normalized, expense_id)"
                " VALUES (?, ?, ?, ?)",
                (receipt_id, item, item, expense_id),
            )
    finally:
        con.close()


def _seed_trend_event(event_id: int, name: str) -> None:
    con = storage.get_connection()
    try:
        con.execute(
            "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled, is_active)"
            " VALUES (?, ?, ?, ?, 0, 1)",
            (event_id, name, _day(_PRIOR + 10), _day(_RECENT - 10)),
        )
    finally:
        con.close()


def _seed_food_categories(count: int) -> list[int]:
    """Add ``count`` categories to the Food group; returns their ids, named ``cat-<id>``."""
    ids = list(range(10, 10 + count))
    con = storage.get_connection()
    try:
        for category_id in ids:
            con.execute(
                "INSERT INTO categories (id, name, group_id, is_active) VALUES (?, ?, 1, TRUE)",
                (category_id, f"cat-{category_id}"),
            )
    finally:
        con.close()
    return ids


def _trend(client, kind: str, basket_id: int) -> dict:
    trends = client.get("/api/analytics/summary").json()["trends"] or []
    return next(t for t in trends if t["kind"] == kind and t["id"] == basket_id)


def _trend_detail(client, kind: str, basket_id: int) -> dict:
    resp = client.get(f"/api/analytics/trends/{kind}/{basket_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _block(detail: dict, dimension: str) -> dict:
    return next(b for b in detail["blocks"] if b["dimension"] == dimension)


def _signed(value: str) -> int:
    return int(value.replace(" ", "").replace("−", "-"))


def _rows_delta(block: dict) -> int:
    return sum(_signed(r["delta"]) for r in block["rows"])


@allure.epic("Analytics")
@allure.feature("API")
class TestAnalyticsTrendIdentity:
    def test_trends_carry_kind_and_id(self, client):
        _seed_trend_expense(_PRIOR, 100)
        _seed_trend_expense(_RECENT, 300)
        trend = _trend(client, "group", 1)
        assert trend["basket_name"] == "Food"
        assert trend["direction"] == "up"
        assert trend["pct"] == "200%"

    def test_tag_and_group_with_same_name_stay_distinct(self, client):
        con = storage.get_connection()
        try:
            con.execute("INSERT INTO tags (id, name, is_active) VALUES (10, 'Food', TRUE)")
        finally:
            con.close()
        _seed_trend_expense(_PRIOR, 100, tag_ids=(10,))
        _seed_trend_expense(_RECENT, 300, tag_ids=(10,))
        trends = client.get("/api/analytics/summary").json()["trends"]
        food = {(t["kind"], t["id"]) for t in trends if t["basket_name"] == "Food"}
        assert food == {("group", 1), ("tag", 10)}


@allure.epic("Analytics")
@allure.feature("API")
class TestAnalyticsTrendReason:
    def test_event_explaining_half_is_named(self, client):
        _seed_food_categories(1)
        _seed_trend_event(100, "Trip")
        _seed_trend_expense(_PRIOR, 100)
        _seed_trend_expense(_RECENT, 100)
        _seed_trend_expense(_RECENT, 300, 10, event_id=100)
        assert _trend(client, "group", 1)["reason"] == {"kind": "event", "names": ["Trip"]}

    def test_no_event_is_never_named(self, client):
        _seed_food_categories(1)
        _seed_trend_event(100, "Trip")
        _seed_trend_expense(_PRIOR, 100)
        _seed_trend_expense(_RECENT, 400)
        _seed_trend_expense(_RECENT, 50, 10, event_id=100)
        assert _trend(client, "group", 1)["reason"] == {"kind": "category", "names": ["food"]}

    def test_top_two_categories_when_none_explains_half(self, client):
        _seed_food_categories(3)
        _seed_trend_expense(_PRIOR, 100)
        _seed_trend_expense(_RECENT, 200)
        _seed_trend_expense(_RECENT, 200, 10)
        _seed_trend_expense(_RECENT, 150, 11)
        _seed_trend_expense(_RECENT, 120, 12)
        assert _trend(client, "group", 1)["reason"] == {
            "kind": "category",
            "names": ["cat-10", "cat-11"],
        }

    def test_flat_basket_has_no_reason(self, client):
        _seed_food_categories(1)
        _seed_trend_expense(_PRIOR, 100)
        _seed_trend_expense(_RECENT, 100, 10)
        assert _trend(client, "group", 1)["reason"] is None

    def test_only_same_sign_contributors_are_candidates(self):
        fell = analytics_api._Driver(1, "fell", "Food", 0, 300)
        rose = analytics_api._Driver(2, "rose", "Food", 10, 0)
        assert analytics_api._trend_reason(False, 100, [fell, rose], []) == {
            "kind": "category",
            "names": ["rose"],
        }
        assert analytics_api._trend_reason(False, 100, [fell], [fell]) is None

    def test_single_category_group_never_names_its_category(self, client):
        _seed_trend_expense(_PRIOR, 100, 2)
        _seed_trend_expense(_RECENT, 300, 2)
        assert _trend(client, "group", 2)["reason"] is None

    def test_group_with_one_spending_category_names_it(self, client):
        _seed_food_categories(1)
        _seed_trend_expense(_PRIOR, 100)
        _seed_trend_expense(_RECENT, 300)
        assert _trend(client, "group", 1)["reason"] == {"kind": "category", "names": ["food"]}

    def test_single_category_group_still_names_an_event(self, client):
        _seed_trend_event(100, "Trip")
        _seed_trend_expense(_PRIOR, 100, 2)
        _seed_trend_expense(_RECENT, 300, 2, event_id=100)
        assert _trend(client, "group", 2)["reason"] == {"kind": "event", "names": ["Trip"]}


@allure.epic("Analytics")
@allure.feature("API")
class TestAnalyticsTrendDetail:
    def test_header_matches_summary(self, client, monkeypatch):
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        _seed_trend_expense(_PRIOR, 300)
        _seed_trend_expense(_RECENT, 410)
        trend = _trend(client, "group", 1)
        detail = _trend_detail(client, "group", 1)
        assert (detail["pct"], detail["direction"]) == (trend["pct"], trend["direction"])
        assert detail["name"] == "Food"
        assert detail["currency"] == "EUR"
        assert (detail["recent"], detail["prior"], detail["delta"]) == ("410", "300", "+110")

    def test_six_categories_fold_into_other(self, client):
        ids = [1, *_seed_food_categories(5)]
        for n, category_id in enumerate(ids):
            _seed_trend_expense(_PRIOR, 10, category_id)
            _seed_trend_expense(_RECENT, 10 + 100 * (n + 1), category_id)
        detail = _trend_detail(client, "group", 1)
        block = _block(detail, "category")
        assert len(block["rows"]) == 5
        other = block["rows"][-1]
        assert (other["key"], other["label"], other["count"]) == (None, None, 2)
        assert _rows_delta(block) == _signed(detail["delta"])
        assert block["delta"] == detail["delta"]

    def test_five_categories_are_all_shown(self, client):
        ids = [1, *_seed_food_categories(4)]
        for n, category_id in enumerate(ids):
            _seed_trend_expense(_RECENT, 10 * (n + 1), category_id)
        block = _block(_trend_detail(client, "group", 1), "category")
        assert [r["label"] for r in block["rows"]] == [
            "cat-13",
            "cat-12",
            "cat-11",
            "cat-10",
            "food",
        ]
        assert all(r["count"] is None for r in block["rows"])
        assert block["rows"][0]["share"] == 1.0
        assert block["rows"][-1]["share"] == 0.2

    def test_event_block_adds_up_with_no_event_row(self, client):
        _seed_trend_event(100, "Trip")
        _seed_trend_event(101, "Party")
        _seed_trend_expense(_PRIOR, 100)
        _seed_trend_expense(_PRIOR, 50, event_id=101)
        _seed_trend_expense(_RECENT, 300, event_id=100)
        _seed_trend_expense(_RECENT, 70)
        detail = _trend_detail(client, "group", 1)
        block = _block(detail, "event")
        assert [(r["label"], r["delta"]) for r in block["rows"]] == [
            ("Trip", "+300"),
            ("Party", "−50"),
            (None, "−30"),
        ]
        no_event = block["rows"][-1]
        assert (no_event["key"], no_event["count"]) == (None, None)
        assert _rows_delta(block) == _signed(detail["delta"]) == 220

    def test_no_event_row_is_never_folded(self, client):
        for n in range(6):
            _seed_trend_event(100 + n, f"Trip-{n}")
            _seed_trend_expense(_RECENT, 100 * (n + 1), event_id=100 + n)
        _seed_trend_expense(_PRIOR, 20)
        _seed_trend_expense(_RECENT, 25)
        detail = _trend_detail(client, "group", 1)
        rows = _block(detail, "event")["rows"]
        assert [r["label"] for r in rows] == ["Trip-5", "Trip-4", "Trip-3", "Trip-2", None, None]
        no_event, other = rows[-2], rows[-1]
        assert (no_event["key"], no_event["count"], no_event["delta"]) == (None, None, "+5")
        assert (other["key"], other["count"], other["delta"]) == (None, 2, "+300")
        assert _rows_delta(_block(detail, "event")) == _signed(detail["delta"]) == 2105

    def test_event_block_omitted_without_events(self, client):
        _seed_trend_expense(_PRIOR, 100)
        _seed_trend_expense(_RECENT, 300)
        dims = [b["dimension"] for b in _trend_detail(client, "group", 1)["blocks"]]
        assert dims == ["category"]

    def test_receipt_blocks_omitted_with_receipts_in_one_window(self, client):
        _seed_trend_expense(_PRIOR, 100)
        _seed_trend_expense(_RECENT, 300, item="milk")
        dims = [b["dimension"] for b in _trend_detail(client, "group", 1)["blocks"]]
        assert dims == ["category"]

    def test_receipt_blocks_present_with_receipts_in_both_windows(self, client):
        _seed_trend_expense(_PRIOR, 100)
        _seed_trend_expense(_PRIOR, 40, item="milk")
        _seed_trend_expense(_RECENT, 300)
        _seed_trend_expense(_RECENT, 60, item="milk")
        _seed_trend_expense(_RECENT, 25, item="bread")
        detail = _trend_detail(client, "group", 1)
        assert [b["dimension"] for b in detail["blocks"]] == ["category", "chain", "item"]
        assert _block(detail, "category")["receipt_backed_only"] is False
        for dimension in ("chain", "item"):
            block = _block(detail, dimension)
            assert block["receipt_backed_only"] is True
            assert (block["recent"], block["prior"], block["delta"]) == ("85", "40", "+45")
        assert [r["label"] for r in _block(detail, "chain")["rows"]] == ["Lidl"]
        items = _block(detail, "item")["rows"]
        assert [(r["label"], r["delta"]) for r in items] == [("bread", "+25"), ("milk", "+20")]

    def test_item_without_normalised_name_uses_raw_name(self, client):
        _seed_trend_expense(_PRIOR, 40, item="milk")
        _seed_trend_expense(_RECENT, 60, item="milk")
        _seed_trend_expense(_RECENT, 25, item="Hleb 500g")
        con = storage.get_connection()
        try:
            con.execute(
                "UPDATE receipt_items SET name_normalized = NULL WHERE name_raw = 'Hleb 500g'",
            )
            con.execute("UPDATE receipt_items SET name_normalized = '' WHERE name_raw = 'milk'")
        finally:
            con.close()
        block = _block(_trend_detail(client, "group", 1), "item")
        assert [(r["key"], r["label"], r["delta"]) for r in block["rows"]] == [
            ("Hleb 500g", "Hleb 500g", "+25"),
            ("milk", "milk", "+20"),
        ]
        assert block["delta"] == "+45"

    def test_expense_with_two_tags_counts_once(self, client):
        _seed_trend_expense(_PRIOR, 100, tag_ids=(1, 2))
        _seed_trend_expense(_RECENT, 250, tag_ids=(1, 2))
        detail = _trend_detail(client, "tag", 1)
        assert (detail["recent"], detail["prior"], detail["delta"]) == ("250", "100", "+150")
        block = _block(detail, "category")
        assert [(r["label"], r["recent"], r["prior"]) for r in block["rows"]] == [
            ("food", "250", "100"),
        ]

    def test_tag_basket_lists_categories_with_their_group(self, client):
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO categories (id, name, group_id, is_active, is_retired)"
                " VALUES (9, 'retired-orphan', NULL, FALSE, TRUE)",
            )
        finally:
            con.close()
        _seed_trend_expense(_PRIOR, 100, 1, tag_ids=(1,))
        _seed_trend_expense(_RECENT, 200, 1, tag_ids=(1,))
        _seed_trend_expense(_RECENT, 50, 2, tag_ids=(1,))
        _seed_trend_expense(_RECENT, 30, 9, tag_ids=(1,))
        _seed_trend_expense(_RECENT, 999, 1)
        detail = _trend_detail(client, "tag", 1)
        assert detail["name"] == "dog"
        block = _block(detail, "category")
        assert [(r["label"], r["sublabel"]) for r in block["rows"]] == [
            ("food", "Food"),
            ("transit", "Transport"),
            ("retired-orphan", None),
        ]
        assert _rows_delta(block) == _signed(detail["delta"]) == 180

    def test_falling_basket(self, client):
        _seed_trend_expense(_PRIOR, 500)
        _seed_trend_expense(_RECENT, 200)
        detail = _trend_detail(client, "group", 1)
        assert detail["direction"] == "down"
        assert detail["pct"] == "60%"
        assert detail["delta"] == "−300"
        row = _block(detail, "category")["rows"][0]
        assert (row["delta"], row["direction"]) == ("−300", "down")

    def test_zero_prior_has_no_pct(self, client):
        _seed_trend_expense(_RECENT, 100)
        detail = _trend_detail(client, "group", 1)
        assert detail["pct"] is None
        assert detail["direction"] == "up"

    def test_unknown_basket_is_404(self, client):
        assert client.get("/api/analytics/trends/group/999").status_code == 404
        assert client.get("/api/analytics/trends/tag/999").status_code == 404

    def test_inactive_tag_is_404(self, client):
        con = storage.get_connection()
        try:
            con.execute("UPDATE tags SET is_active = 0 WHERE id = 1")
        finally:
            con.close()
        assert client.get("/api/analytics/trends/tag/1").status_code == 404

    def test_bad_kind_is_422(self, client):
        assert client.get("/api/analytics/trends/event/1").status_code == 422


@allure.epic("Analytics")
@allure.feature("API")
class TestAnalyticsTrendFormatting:
    def test_signed_amounts(self):
        assert analytics_api._fmt_signed(1533.4) == "+1 533"
        assert analytics_api._fmt_signed(-260) == "−260"
        assert analytics_api._fmt_signed(0.2) == "0"

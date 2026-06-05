"""Tests for spending_summary.sql and view_data.sql against a test SQLite DB."""

import json
import sqlite3

import allure
import pytest

from dinary_analytics.connection import load_query, open_ledger


@pytest.fixture
def full_ledger_db(tmp_path):
    """SQLite DB with events, tags, category_groups, and recent expenses."""
    db = tmp_path / "ledger.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE category_groups (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_active  INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE categories (
            id        INTEGER PRIMARY KEY,
            name      TEXT NOT NULL,
            group_id  INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE events (
            id        INTEGER PRIMARY KEY,
            name      TEXT NOT NULL,
            date_from DATE NOT NULL,
            date_to   DATE NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE tags (
            id        INTEGER PRIMARY KEY,
            name      TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE expenses (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            datetime          TEXT NOT NULL,
            amount            REAL NOT NULL,
            amount_original   REAL NOT NULL,
            currency_original TEXT NOT NULL,
            category_id       INTEGER NOT NULL,
            event_id          INTEGER,
            comment           TEXT,
            sheet_category    TEXT,
            sheet_group       TEXT
        );
        CREATE TABLE expense_tags (
            expense_id INTEGER NOT NULL,
            tag_id     INTEGER NOT NULL,
            PRIMARY KEY (expense_id, tag_id)
        );
        CREATE TABLE income (
            year  INTEGER NOT NULL,
            month INTEGER NOT NULL,
            amount REAL NOT NULL,
            PRIMARY KEY (year, month)
        );
        CREATE TABLE exchange_rates (
            currency TEXT NOT NULL,
            date     DATE NOT NULL,
            rate     REAL NOT NULL,
            PRIMARY KEY (currency, date)
        );

        INSERT INTO category_groups VALUES (1, 'Питание', 0, 1);
        INSERT INTO category_groups VALUES (2, 'Жильё', 1, 1);
        INSERT INTO categories VALUES (1, 'еда', 1, 1);
        INSERT INTO categories VALUES (2, 'аренда', 2, 1);
        INSERT INTO events VALUES (1, 'отпуск', '2025-06-01', '2025-07-31', 1);
        INSERT INTO tags VALUES (1, 'путешествия', 1);

        -- Recent expenses (within last 12 months, use a far-future date to be safe)
        INSERT INTO expenses VALUES (1, '2026-01-10 10:00:00', 500.0, 55000.0, 'RSD', 1, 1, NULL, NULL, NULL);
        INSERT INTO expenses VALUES (2, '2026-01-15 12:00:00', 300.0, 33000.0, 'RSD', 2, 1, NULL, NULL, NULL);
        INSERT INTO expenses VALUES (3, '2026-02-05 09:00:00', 200.0, 22000.0, 'RSD', 1, NULL, NULL, NULL, NULL);

        INSERT INTO expense_tags VALUES (1, 1);
        INSERT INTO expense_tags VALUES (3, 1);
    """)
    con.commit()
    con.close()
    return db


@allure.epic("Analytics")
@allure.feature("Queries")
def test_spending_summary_returns_single_row(full_ledger_db):
    sql = load_query("spending_summary")
    con = open_ledger(replica_path=full_ledger_db)
    try:
        rows = con.execute(sql).fetchall()
    finally:
        con.close()
    assert len(rows) == 1
    assert rows[0][0] is not None


@allure.epic("Analytics")
@allure.feature("Queries")
def test_spending_summary_has_three_keys(full_ledger_db):
    sql = load_query("spending_summary")
    con = open_ledger(replica_path=full_ledger_db)
    try:
        row = con.execute(sql).fetchone()
    finally:
        con.close()
    summary = json.loads(row[0])
    assert "events" in summary
    assert "tags" in summary
    assert "category_groups" in summary


@allure.epic("Analytics")
@allure.feature("Queries")
def test_spending_summary_events_structure(full_ledger_db):
    sql = load_query("spending_summary")
    con = open_ledger(replica_path=full_ledger_db)
    try:
        row = con.execute(sql).fetchone()
    finally:
        con.close()
    events = json.loads(row[0])["events"]
    assert len(events) >= 1
    ev = events[0]
    assert "id" in ev
    assert "name" in ev
    assert "total_amount" in ev
    assert "date_from" in ev
    assert "date_to" in ev


@allure.epic("Analytics")
@allure.feature("Queries")
def test_spending_summary_tags_structure(full_ledger_db):
    sql = load_query("spending_summary")
    con = open_ledger(replica_path=full_ledger_db)
    try:
        row = con.execute(sql).fetchone()
    finally:
        con.close()
    tags = json.loads(row[0])["tags"]
    assert len(tags) >= 1
    t = tags[0]
    assert "id" in t
    assert "name" in t
    assert "expense_count" in t
    assert "total_amount" in t


@allure.epic("Analytics")
@allure.feature("Queries")
def test_spending_summary_category_groups_structure(full_ledger_db):
    sql = load_query("spending_summary")
    con = open_ledger(replica_path=full_ledger_db)
    try:
        row = con.execute(sql).fetchone()
    finally:
        con.close()
    groups = json.loads(row[0])["category_groups"]
    assert len(groups) >= 1
    g = groups[0]
    assert "id" in g
    assert "name" in g
    assert "total_amount" in g


@allure.epic("Analytics")
@allure.feature("Queries")
def test_spending_summary_event_total_correct(full_ledger_db):
    sql = load_query("spending_summary")
    con = open_ledger(replica_path=full_ledger_db)
    try:
        row = con.execute(sql).fetchone()
    finally:
        con.close()
    events = json.loads(row[0])["events"]
    отпуск = next(e for e in events if e["name"] == "отпуск")
    # expenses 1 and 2 have event_id=1: 500+300=800
    assert abs(отпуск["total_amount"] - 800.0) < 0.01


@allure.epic("Analytics")
@allure.feature("Queries")
def test_view_data_returns_expected_columns(full_ledger_db):
    sql = load_query("view_data")
    basket_cfg = json.dumps(
        {
            "baskets": [{"name": "Trip", "triggers": {"events": [1], "tags": []}}],
            "default_basket": "Other",
        }
    )
    con = open_ledger(replica_path=full_ledger_db)
    try:
        result = con.execute(sql, [basket_cfg, "2026-01-01"]).fetchall()
        cols = [d[0] for d in con.execute(sql, [basket_cfg, "2026-01-01"]).description]
    finally:
        con.close()
    assert cols == ["basket_name", "year_month", "group_name", "total_amount"]


@allure.epic("Analytics")
@allure.feature("Queries")
def test_view_data_assigns_event_basket(full_ledger_db):
    sql = load_query("view_data")
    basket_cfg = json.dumps(
        {
            "baskets": [{"name": "Trip", "triggers": {"events": [1], "tags": []}}],
            "default_basket": "Other",
        }
    )
    con = open_ledger(replica_path=full_ledger_db)
    try:
        rows = con.execute(sql, [basket_cfg, "2026-01-01"]).fetchall()
    finally:
        con.close()
    basket_names = {r[0] for r in rows}
    # expenses 1 and 2 have event_id=1 → basket "Trip"; expense 3 → "Other"
    assert "Trip" in basket_names
    assert "Other" in basket_names


@allure.epic("Analytics")
@allure.feature("Queries")
def test_view_data_assigns_tag_basket(full_ledger_db):
    sql = load_query("view_data")
    # tag_id=1 (путешествия) is on expenses 1 and 3
    basket_cfg = json.dumps(
        {
            "baskets": [{"name": "Tagged", "triggers": {"events": [], "tags": [1]}}],
            "default_basket": "Other",
        }
    )
    con = open_ledger(replica_path=full_ledger_db)
    try:
        rows = con.execute(sql, [basket_cfg, "2026-01-01"]).fetchall()
    finally:
        con.close()
    basket_names = {r[0] for r in rows}
    assert "Tagged" in basket_names


@allure.epic("Analytics")
@allure.feature("Queries")
def test_view_data_default_basket_for_unmatched(full_ledger_db):
    sql = load_query("view_data")
    # no triggers match → all fall into default
    basket_cfg = json.dumps(
        {
            "baskets": [{"name": "Nothing", "triggers": {"events": [], "tags": []}}],
            "default_basket": "Misc",
        }
    )
    con = open_ledger(replica_path=full_ledger_db)
    try:
        rows = con.execute(sql, [basket_cfg, "2026-01-01"]).fetchall()
    finally:
        con.close()
    # Since triggers are empty, no expense matches "Nothing" → all go to "Misc"
    basket_names = {r[0] for r in rows}
    assert "Misc" in basket_names


@allure.epic("Analytics")
@allure.feature("Queries")
def test_view_data_aggregates_by_month(full_ledger_db):
    sql = load_query("view_data")
    basket_cfg = json.dumps(
        {
            "baskets": [{"name": "Trip", "triggers": {"events": [1], "tags": []}}],
            "default_basket": "Other",
        }
    )
    con = open_ledger(replica_path=full_ledger_db)
    try:
        rows = con.execute(sql, [basket_cfg, "2026-01-01"]).fetchall()
    finally:
        con.close()
    year_months = {r[1] for r in rows}
    assert "2026-01" in year_months
    assert "2026-02" in year_months


@allure.epic("Analytics")
@allure.feature("Queries")
def test_view_data_empty_baskets_no_underflow(full_ledger_db):
    """An empty baskets array must not trigger a UINT64 0-1 underflow in generate_series."""
    sql = load_query("view_data")
    basket_cfg = json.dumps({"baskets": [], "default_basket": "Misc"})
    con = open_ledger(replica_path=full_ledger_db)
    try:
        rows = con.execute(sql, [basket_cfg, "2026-01-01"]).fetchall()
    finally:
        con.close()
    assert rows  # every expense routed to the default basket
    assert {r[0] for r in rows} == {"Misc"}

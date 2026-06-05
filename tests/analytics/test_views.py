"""Tests for analytics view-data loading (dinary_analytics.views)."""

import sqlite3

import allure
import pytest

from dinary_analytics.settings import save_view
from dinary_analytics.views import empty_view_frame, load_pinned_view_frames, load_view_frame


@pytest.fixture
def ledger_db(tmp_path):
    db = tmp_path / "ledger.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            datetime TIMESTAMP NOT NULL,
            amount REAL NOT NULL,
            amount_original REAL NOT NULL,
            currency_original TEXT NOT NULL,
            category_id INTEGER NOT NULL,
            event_id INTEGER,
            comment TEXT,
            sheet_category TEXT,
            sheet_group TEXT
        );
        CREATE TABLE categories (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            group_id INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE category_groups (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            date_from DATE NOT NULL,
            date_to DATE NOT NULL,
            auto_attach_enabled INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE tags (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE expense_tags (
            expense_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY (expense_id, tag_id)
        );
        INSERT INTO category_groups VALUES (1, 'Питание', 0, 1);
        INSERT INTO categories VALUES (1, 'еда', 1, 1);
        INSERT INTO categories VALUES (2, 'аренда', 1, 1);
        INSERT INTO events VALUES (1, 'отпуск', '2025-06-01', '2025-07-31', 1, 1);
        INSERT INTO expenses VALUES (1, '2025-06-10 10:00:00', 500.0, 55000.0, 'RSD', 1, 1, NULL, NULL, NULL);
        INSERT INTO expenses VALUES (2, '2025-06-15 12:00:00', 300.0, 33000.0, 'RSD', 2, 1, NULL, NULL, NULL);
    """)
    con.commit()
    con.close()
    return db


@allure.epic("Analytics")
@allure.feature("Views")
def test_empty_view_frame_schema():
    df = empty_view_frame()
    assert df.is_empty()
    assert set(df.columns) == {"basket_name", "year_month", "group_name", "total_amount"}


@allure.epic("Analytics")
@allure.feature("Views")
def test_load_view_frame_assigns_baskets(ledger_db):
    cfg = {
        "baskets": [{"name": "Отпуск", "triggers": {"events": [1], "tags": []}}],
        "default_basket": "Прочее",
    }
    df = load_view_frame(cfg, "2020-01-01", replica_path=ledger_db)
    assert not df.is_empty()
    assert "Отпуск" in set(df["basket_name"].to_list())


@allure.epic("Analytics")
@allure.feature("Views")
def test_load_pinned_view_frames_empty(tmp_path, ledger_db):
    frames = load_pinned_view_frames(
        "2020-01-01", replica_path=ledger_db, db_path=tmp_path / "settings.db"
    )
    assert frames == []


@allure.epic("Analytics")
@allure.feature("Views")
def test_load_pinned_view_frames_returns_saved(tmp_path, ledger_db):
    settings_db = tmp_path / "settings.db"
    save_view(
        {
            "name": "Отпуск",
            "baskets": [{"name": "Отпуск", "triggers": {"events": [1], "tags": []}}],
            "default_basket": "Прочее",
        },
        db_path=settings_db,
    )

    frames = load_pinned_view_frames("2020-01-01", replica_path=ledger_db, db_path=settings_db)

    assert len(frames) == 1
    view_id, cfg, df = frames[0]
    assert view_id
    assert cfg["name"] == "Отпуск"
    assert "Отпуск" in set(df["basket_name"].to_list())

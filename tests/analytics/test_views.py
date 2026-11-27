"""Tests for analytics view-data loading (dinary_analytics.views)."""

import sqlite3

import allure
import pytest

from dinary_analytics.settings import save_view
from dinary_analytics.views import (
    drill_down_view,
    duplicate_basket_name,
    empty_view_frame,
    load_pinned_view_frames,
    load_view_frame,
)


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
        INSERT INTO category_groups VALUES (1, 'Nutrition', 0, 1);
        INSERT INTO categories VALUES (1, 'food', 1, 1);
        INSERT INTO categories VALUES (2, 'rent', 1, 1);
        INSERT INTO events VALUES (1, 'vacation', '2025-06-01', '2025-07-31', 1, 1);
        INSERT INTO tags VALUES (1, 'travel', 1);
        INSERT INTO expenses VALUES (1, '2025-06-10 10:00:00', 500.0, 55000.0, 'RSD', 1, 1, NULL, NULL, NULL);
        INSERT INTO expenses VALUES (2, '2025-06-15 12:00:00', 300.0, 33000.0, 'RSD', 2, 1, NULL, NULL, NULL);
        INSERT INTO expense_tags VALUES (1, 1);
        INSERT INTO expense_tags VALUES (2, 1);
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
        "baskets": [{"name": "Vacation", "triggers": {"events": [1], "tags": []}}],
        "default_basket": "Other",
    }
    df = load_view_frame(cfg, "2020-01-01", replica_path=ledger_db)
    assert not df.is_empty()
    assert "Vacation" in set(df["basket_name"].to_list())


@allure.epic("Analytics")
@allure.feature("Views")
def test_load_view_frame_empty_baskets_assigns_all_to_default(ledger_db):
    """A view with no baskets must not crash (UINT64 0-1) and routes all to default."""
    cfg = {"baskets": [], "default_basket": "Other"}
    df = load_view_frame(cfg, "2020-01-01", replica_path=ledger_db)
    assert not df.is_empty()
    assert set(df["basket_name"].to_list()) == {"Other"}


@allure.epic("Analytics")
@allure.feature("Views")
def test_load_view_frame_single_basket_first_match(ledger_db):
    """First-match priority: an expense matching an earlier basket goes there, not default."""
    cfg = {
        "baskets": [
            {"name": "Travel", "triggers": {"events": [], "tags": [1]}},
            {"name": "Vacation", "triggers": {"events": [1], "tags": []}},
        ],
        "default_basket": "Other",
    }
    df = load_view_frame(cfg, "2020-01-01", replica_path=ledger_db)
    assert not df.is_empty()
    # both tag-1 expenses (ids 1,2) are in event 1 too, but tag basket is listed first
    assert "Travel" in set(df["basket_name"].to_list())


@allure.epic("Analytics")
@allure.feature("Views")
def test_load_view_frame_runs_for_every_period(ledger_db):
    """view_data.sql executes for each dashboard period label without SQL errors."""
    cfg = {
        "baskets": [{"name": "Vacation", "triggers": {"events": [1], "tags": []}}],
        "default_basket": "Other",
    }
    for date_from in ("2020-01-01", "2025-01-01", "2024-01-01"):
        # must not raise a DuckDB/MarimoSQLError
        load_view_frame(cfg, date_from, replica_path=ledger_db)


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
            "name": "Vacation",
            "baskets": [{"name": "Vacation", "triggers": {"events": [1], "tags": []}}],
            "default_basket": "Other",
        },
        db_path=settings_db,
    )

    frames = load_pinned_view_frames("2020-01-01", replica_path=ledger_db, db_path=settings_db)

    assert len(frames) == 1
    view_id, cfg, df = frames[0]
    assert view_id
    assert cfg["name"] == "Vacation"
    assert "Vacation" in set(df["basket_name"].to_list())


@allure.epic("Analytics")
@allure.feature("Views")
def test_load_view_frame_for_a_scoped_config(ledger_db):
    cfg = {
        "baskets": [{"name": "Food", "triggers": {"categories": [1]}}],
        "default_basket": "Rest",
        "scope": {"name": "Vacation", "required": [{"events": [1]}], "excluded": []},
    }

    df = load_view_frame(cfg, "2020-01-01", replica_path=ledger_db)

    totals: dict[str, float] = {}
    for basket_name, amount in zip(df["basket_name"], df["total_amount"], strict=True):
        totals[basket_name] = totals.get(basket_name, 0.0) + amount
    assert totals == {"Food": 500.0, "Rest": 300.0}


PARENT = {
    "name": "Life",
    "chart_type": "stacked_bar_monthly",
    "baskets": [
        {"name": "Travel", "triggers": {"tags": [7]}},
        {"name": "Food", "triggers": {"category_groups": [2]}},
        {"name": "Home", "triggers": {"categories": [5]}},
    ],
    "default_basket": "Main",
}
BREAKDOWN = [{"name": "Groceries", "triggers": {"categories": [11]}}]


@allure.epic("Analytics")
@allure.feature("Views")
def test_drill_down_view_excludes_the_preceding_baskets():
    child = drill_down_view(PARENT, "Food", BREAKDOWN, "Other food", "by category")

    assert child == {
        "name": "by category",
        "baskets": BREAKDOWN,
        "default_basket": "Other food",
        "chart_type": "stacked_bar_monthly",
        "scope": {
            "name": "Food",
            "required": [{"category_groups": [2]}],
            "excluded": [{"tags": [7]}],
        },
    }


@allure.epic("Analytics")
@allure.feature("Views")
def test_drill_down_view_into_the_default_basket_excludes_every_basket():
    scope = drill_down_view(PARENT, "Main", BREAKDOWN, "Rest")["scope"]

    assert scope == {
        "name": "Main",
        "required": [],
        "excluded": [{"tags": [7]}, {"category_groups": [2]}, {"categories": [5]}],
    }


@allure.epic("Analytics")
@allure.feature("Views")
def test_drill_down_view_composes_with_the_parent_scope():
    food = drill_down_view(PARENT, "Food", BREAKDOWN, "Other food")

    scope = drill_down_view(food, "Other food", [], "All")["scope"]

    assert scope == {
        "name": "Food ▸ Other food",
        "required": [{"category_groups": [2]}],
        "excluded": [{"tags": [7]}, {"categories": [11]}],
    }


@allure.epic("Analytics")
@allure.feature("Views")
def test_drill_down_view_rejects_an_unknown_basket():
    with pytest.raises(ValueError, match="Nope"):
        drill_down_view(PARENT, "Nope", BREAKDOWN, "Rest")


@allure.epic("Analytics")
@allure.feature("Views")
def test_duplicate_basket_name():
    assert duplicate_basket_name(PARENT["baskets"], "Main") is None
    assert duplicate_basket_name([{"name": "Other"}], "Other") == "Other"
    assert duplicate_basket_name([{"name": "Food"}, {"name": "Food"}], "Main") == "Food"


@allure.epic("Analytics")
@allure.feature("Views")
def test_drill_down_view_rejects_a_parent_with_a_repeated_basket_name():
    parent = {"baskets": [{"name": "Other", "triggers": {"tags": [1]}}], "default_basket": "Other"}

    with pytest.raises(ValueError, match="more than once"):
        drill_down_view(parent, "Other", BREAKDOWN, "Rest")

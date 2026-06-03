"""Tests for dashboard chart construction.

Tests call make_chart_pair from dinary_analytics.charts directly —
the same function the notebook imports — so any invalid Altair params
or broken imports are caught here before runtime.
"""

import json
import sqlite3

import allure
import polars as pl
import pytest

from dinary_analytics.charts import make_chart_pair
from dinary_analytics.connection import open_ledger


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
        CREATE TABLE income (
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            amount REAL NOT NULL,
            PRIMARY KEY (year, month)
        );
        INSERT INTO categories VALUES (1, 'еда', 1, 1);
        INSERT INTO categories VALUES (2, 'аренда', 1, 1);
        INSERT INTO expenses VALUES (1, '2025-06-10 10:00:00', 500.0, 55000.0, 'RSD', 1, NULL, NULL, NULL, NULL);
        INSERT INTO expenses VALUES (2, '2025-06-15 12:00:00', 300.0, 33000.0, 'RSD', 2, NULL, NULL, NULL, NULL);
        INSERT INTO expenses VALUES (3, '2025-07-05 09:00:00', 480.0, 52800.0, 'RSD', 1, NULL, NULL, NULL, NULL);
        INSERT INTO income VALUES (2025, 6, 2000.0);
        INSERT INTO income VALUES (2025, 7, 2100.0);
    """)
    con.commit()
    con.close()
    return db


@pytest.fixture
def chart_data(ledger_db):
    top10 = ["еда", "аренда"]
    category_order = top10 + ["Other"]
    rank_df = pl.DataFrame(
        {"category": category_order, "cat_rank": list(range(len(category_order)))}
    )

    con = open_ledger(replica_path=ledger_db)
    try:
        exp_rows = con.execute(
            """
            SELECT
                strftime(e.datetime::TIMESTAMP, '%Y-%m') AS month,
                CASE WHEN list_contains($1::VARCHAR[], c.name)
                    THEN c.name ELSE 'Other' END AS category,
                CAST(SUM(e.amount) AS DOUBLE) AS total
            FROM ledger.expenses e
            JOIN ledger.categories c ON e.category_id = c.id
            WHERE e.datetime >= (CURRENT_DATE - INTERVAL '12 months')
            GROUP BY month, category
            ORDER BY month
        """,
            [top10],
        ).fetchall()
        inc_rows = con.execute("""
            SELECT
                strftime(make_date(year, month, 1), '%Y-%m') AS month,
                CAST(amount AS DOUBLE) AS income
            FROM ledger.income
            WHERE make_date(year, month, 1) >= (CURRENT_DATE - INTERVAL '12 months')::DATE
            ORDER BY month
        """).fetchall()
    finally:
        con.close()

    expense_monthly_df = pl.DataFrame(
        {
            "month": [r[0] for r in exp_rows],
            "category": [r[1] for r in exp_rows],
            "total": [float(r[2]) for r in exp_rows],
        }
    ).join(rank_df, on="category", how="left")

    income_monthly_df = pl.DataFrame(
        {
            "month": [r[0] for r in inc_rows],
            "income": [float(r[1]) for r in inc_rows],
        }
    )

    year_df = (
        expense_monthly_df.group_by("category")
        .agg(pl.sum("total"), pl.first("cat_rank"))
        .sort("total", descending=True)
    )

    return (
        expense_monthly_df,
        income_monthly_df,
        year_df,
        float(income_monthly_df["income"].sum()),
        category_order,
    )


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_chart_pair_builds_valid_spec(chart_data):
    exp_df, inc_df, yr_df, total_inc, cat_order = chart_data
    chart = make_chart_pair(exp_df, inc_df, yr_df, total_inc, cat_order)
    spec = chart.to_dict()
    assert "hconcat" in spec
    assert len(spec["hconcat"]) == 2


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_chart_pair_with_title(chart_data):
    exp_df, inc_df, yr_df, total_inc, cat_order = chart_data
    chart = make_chart_pair(exp_df, inc_df, yr_df, total_inc, cat_order, period_title="2025")
    spec = chart.to_dict()
    main = spec["hconcat"][0]
    assert main.get("title") == "2025"


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_chart_pair_main_layers(chart_data):
    exp_df, inc_df, yr_df, total_inc, cat_order = chart_data
    chart = make_chart_pair(exp_df, inc_df, yr_df, total_inc, cat_order)
    spec = chart.to_dict()
    main = spec["hconcat"][0]
    assert "layer" in main
    mark_types = set()
    for layer in main["layer"]:
        for sub in layer.get("layer", [layer]):
            mark = sub.get("mark", {})
            mark_types.add(mark.get("type") if isinstance(mark, dict) else mark)
    assert "area" in mark_types
    assert "line" in mark_types


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_chart_pair_no_invalid_mark_params(chart_data):
    exp_df, inc_df, yr_df, total_inc, cat_order = chart_data
    chart = make_chart_pair(exp_df, inc_df, yr_df, total_inc, cat_order)
    spec_str = json.dumps(chart.to_dict())
    assert "paintOrder" not in spec_str


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_dashboard_notebook_imports():
    """Verify the notebook module loads and make_chart_pair is importable from charts.py."""
    import dinary_analytics.charts as charts_module
    import dinary_analytics.notebooks.dashboard as dash_module

    assert hasattr(charts_module, "make_chart_pair")
    assert dash_module.app is not None

"""Tests for dashboard chart construction.

Tests call make_chart_pair and make_event_chart from dinary_analytics.charts directly —
the same functions the notebook imports — so any invalid Altair params
or broken imports are caught here before runtime.
"""

import json
import sqlite3

import allure
import polars as pl
import pytest

from dinary_analytics.charts import ChartSize, make_chart_pair, make_event_chart
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
        INSERT INTO categories VALUES (1, 'еда', 1, 1);
        INSERT INTO categories VALUES (2, 'аренда', 1, 1);
        INSERT INTO tags VALUES (1, 'путешествия', 1);
        INSERT INTO events VALUES (1, 'отпуск', '2025-06-01', '2025-07-31', 1, 1);
        INSERT INTO expenses VALUES (1, '2025-06-10 10:00:00', 500.0, 55000.0, 'RSD', 1, 1, NULL, NULL, NULL);
        INSERT INTO expenses VALUES (2, '2025-06-15 12:00:00', 300.0, 33000.0, 'RSD', 2, 1, NULL, NULL, NULL);
        INSERT INTO expenses VALUES (3, '2025-07-05 09:00:00', 480.0, 52800.0, 'RSD', 1, 1, NULL, NULL, NULL);
        INSERT INTO expense_tags VALUES (1, 1);
        INSERT INTO expense_tags VALUES (2, 1);
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


@pytest.fixture
def event_expense_df(ledger_db):
    con = open_ledger(replica_path=ledger_db)
    try:
        rows = con.execute(
            """
            SELECT c.name AS category, CAST(SUM(e.amount) AS DOUBLE) AS total
            FROM ledger.expenses e
            JOIN ledger.categories c ON e.category_id = c.id
            WHERE e.event_id = 1
            GROUP BY c.name
            ORDER BY total DESC
            """
        ).fetchall()
    finally:
        con.close()
    return pl.DataFrame({"category": [r[0] for r in rows], "total": [float(r[1]) for r in rows]})


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
def test_make_chart_pair_custom_size(chart_data):
    exp_df, inc_df, yr_df, total_inc, cat_order = chart_data
    chart = make_chart_pair(
        exp_df,
        inc_df,
        yr_df,
        total_inc,
        cat_order,
        size=ChartSize(width=400, height=280, year_width=120),
    )
    spec = chart.to_dict()
    assert spec["hconcat"][0]["width"] == 400
    assert spec["hconcat"][0]["height"] == 280


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_dashboard_notebook_imports():
    """Verify the notebook module loads and both chart functions are importable."""
    import dinary_analytics.charts as charts_module
    import dinary_analytics.notebooks.dashboard as dash_module

    assert hasattr(charts_module, "make_chart_pair")
    assert hasattr(charts_module, "make_event_chart")
    assert dash_module.app is not None


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_dashboard_selectors_cell_runs(ledger_db, monkeypatch):
    """Run the actual dashboard selectors cell against a test DB.

    Uses cell.run() so any regression in the real notebook code is caught here,
    not via duplicated logic.
    """
    import datetime
    import marimo as mo

    import dinary_analytics.notebooks.dashboard as dash_module
    from dinary_analytics.connection import open_ledger as _real_open_ledger

    def _mock_open_ledger():
        return _real_open_ledger(replica_path=ledger_db)

    cells = list(dash_module.app._cell_manager.cells())
    sel_cell = next(c for c in cells if "event_selector" in c.defs)

    _, defs = sel_cell.run(
        date=datetime.date,
        get_config_json=lambda _k: None,
        mo=mo,
        open_ledger=_mock_open_ledger,
    )

    assert defs["event_selector"] is not None
    assert defs["tag_selector"] is not None
    assert defs["tag_year_selector"] is not None
    # event_selector is now a single-select dropdown: .value is one ID string or None
    eid = defs["event_selector"].value
    assert eid is None or int(eid)
    # tag_selector.value returns an ID or None
    tid = defs["tag_selector"].value
    assert tid is None or int(tid)
    # tag_year_selector defaults to current year if present in years list
    tyr = defs["tag_year_selector"].value
    assert tyr is None or tyr.isdigit()


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_event_chart_builds_valid_spec(event_expense_df):
    chart = make_event_chart(event_expense_df, "отпуск")
    spec = chart.to_dict()
    # make_event_chart returns a LayerChart; the arc mark is in the first layer
    assert spec["layer"][0]["mark"]["type"] == "arc"


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_event_chart_with_subtitle(event_expense_df):
    chart = make_event_chart(event_expense_df, "отпуск")
    spec = chart.to_dict()
    title = spec.get("title", {})
    assert title.get("text") == "отпуск"
    assert "€" in title.get("subtitle", "")


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_event_chart_top9_plus_other():
    many_cats = pl.DataFrame(
        {
            "category": [f"cat{i}" for i in range(12)],
            "total": [float(100 - i * 5) for i in range(12)],
        }
    )
    chart = make_event_chart(many_cats, "big event")
    spec_str = json.dumps(chart.to_dict())
    # bottom 3 categories must be merged into Other
    assert "Other" in spec_str
    assert '"cat9"' not in spec_str
    assert '"cat10"' not in spec_str
    assert '"cat11"' not in spec_str


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_event_chart_multi_event(ledger_db):
    con = open_ledger(replica_path=ledger_db)
    try:
        rows = con.execute(
            """
            SELECT c.name AS category, CAST(SUM(e.amount) AS DOUBLE) AS total
            FROM ledger.expenses e
            JOIN ledger.categories c ON e.category_id = c.id
            WHERE list_contains($1::INT[], e.event_id)
            GROUP BY c.name
            ORDER BY total DESC
            """,
            [[1]],
        ).fetchall()
    finally:
        con.close()
    expense_df = pl.DataFrame(
        {"category": [r[0] for r in rows], "total": [float(r[1]) for r in rows]}
    )
    chart = make_event_chart(expense_df, "combined")
    spec = chart.to_dict()
    assert spec["layer"][0]["mark"]["type"] == "arc"

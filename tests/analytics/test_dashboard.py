"""Smoke tests for dashboard chart construction — catch invalid Altair mark params before runtime."""

import sqlite3

import altair as alt
import polars as pl
import pytest

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


def _build_charts(ledger_db):
    """Run the chart-cell logic and return the top-level Altair spec dict."""
    con = open_ledger(replica_path=ledger_db)
    try:
        top10_rows = con.execute("""
            SELECT c.name
            FROM ledger.expenses e
            JOIN ledger.categories c ON e.category_id = c.id
            WHERE e.datetime >= (CURRENT_DATE - INTERVAL '12 months')
            GROUP BY c.name
            ORDER BY SUM(e.amount) DESC
            LIMIT 10
        """).fetchall()
        top10 = [r[0] for r in top10_rows]
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

    category_order = top10 + ["Other"]
    rank_df = pl.DataFrame(
        {"category": category_order, "cat_rank": list(range(len(category_order)))}
    )

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
    total_annual_income = float(income_monthly_df["income"].sum())

    chart_width, chart_height, year_width = 700, 400, 160
    total_expenses = float(year_df["total"].sum())
    annual_saved = total_annual_income - total_expenses
    sign = "+" if annual_saved >= 0 else ""
    savings_color = "#2ca02c" if annual_saved >= 0 else "#d62728"

    monthly_totals = (
        expense_monthly_df.group_by("month").agg(pl.sum("total").alias("expenses")).sort("month")
    )
    savings_df = (
        income_monthly_df.join(monthly_totals, on="month", how="left")
        .fill_null(0)
        .sort("month")
        .with_columns((pl.col("income") - pl.col("expenses")).alias("saved"))
    )

    first_month = expense_monthly_df["month"].min()
    label_df = (
        expense_monthly_df.filter(pl.col("month") == first_month)
        .sort("cat_rank")
        .with_columns(pl.col("total").cum_sum().alias("y_top"))
        .with_columns((pl.col("y_top") - pl.col("total") / 2).alias("y_mid"))
    )

    color_scale = alt.Scale(domain=category_order, scheme="tableau20")

    areas = (
        alt.Chart(expense_monthly_df)
        .mark_area(opacity=0.85, interpolate="monotone")
        .encode(
            x=alt.X("month:O", title="Month"),
            y=alt.Y(
                "total:Q",
                stack=True,
                title="EUR",
                scale=alt.Scale(nice=True),
                axis=alt.Axis(tickCount=6, format="~s", grid=True),
            ),
            color=alt.Color("category:N", scale=color_scale, legend=None),
            order=alt.Order("cat_rank:Q", sort="ascending"),
            tooltip=["month:O", "category:N", alt.Tooltip("total:Q", format=".0f")],
        )
    )

    labels_enc = {"x": alt.X("month:O"), "y": alt.Y("y_mid:Q"), "text": alt.Text("category:N")}
    labels_bg = (
        alt.Chart(label_df)
        .mark_text(
            align="left",
            dx=5,
            fontSize=11,
            fontWeight="bold",
            fill="white",
            stroke="white",
            strokeWidth=5,
        )
        .encode(**labels_enc)
    )
    labels_fg = (
        alt.Chart(label_df)
        .mark_text(align="left", dx=5, fontSize=11, fontWeight="bold", fill="#333333")
        .encode(**labels_enc)
    )

    savings_line = (
        alt.Chart(savings_df)
        .mark_line(
            color="#555555",
            strokeWidth=2,
            strokeDash=[6, 3],
            point={"color": "#555555", "size": 40, "filled": True},
        )
        .encode(
            x=alt.X("month:O"),
            y=alt.Y("saved:Q"),
            tooltip=["month:O", alt.Tooltip("saved:Q", format=".0f", title="Saved")],
        )
    )
    savings_label = (
        alt.Chart(savings_df.tail(1))
        .mark_text(align="right", dx=-6, dy=-10, fontSize=10, fontWeight="bold")
        .encode(
            x=alt.X("month:O"),
            y=alt.Y("saved:Q"),
            text=alt.value("saved"),
            color=alt.value("#555555"),
        )
    )

    year_order = list(reversed(category_order))
    year_df_pos = year_df.with_columns(pl.lit(0.0).alias("label_x"))

    year_bars = (
        alt.Chart(year_df_pos)
        .mark_bar()
        .encode(
            y=alt.Y("category:N", sort=year_order, title=None, axis=None),
            x=alt.X("total:Q", title="Year total", axis=alt.Axis(tickCount=3, format="~s")),
            color=alt.Color("category:N", scale=color_scale, legend=None),
            tooltip=["category:N", alt.Tooltip("total:Q", format=".0f")],
        )
    )
    year_enc = {
        "y": alt.Y("category:N", sort=year_order, axis=None),
        "x": alt.X("label_x:Q"),
        "text": alt.Text("category:N"),
    }
    year_text_bg = (
        alt.Chart(year_df_pos)
        .mark_text(align="left", dx=5, fontSize=10, fill="white", stroke="white", strokeWidth=4)
        .encode(**year_enc)
    )
    year_text_fg = (
        alt.Chart(year_df_pos)
        .mark_text(align="left", dx=5, fontSize=10, fill="#333333")
        .encode(**year_enc)
    )

    saved_text = (
        alt.Chart(pl.DataFrame({"t": [f"Saved {sign}{annual_saved:,.0f}€"]}))
        .mark_text(fontSize=13, fontWeight="bold", align="center", baseline="middle")
        .encode(
            x=alt.value(year_width / 2), y=alt.value(15), text="t:N", color=alt.value(savings_color)
        )
        .properties(width=year_width, height=30)
    )

    return alt.hconcat(
        alt.layer(areas, labels_bg, labels_fg, savings_line, savings_label)
        .properties(width=chart_width, height=chart_height, title="Monthly Expenses by Category")
        .interactive(),
        alt.vconcat(
            alt.layer(year_bars, year_text_bg, year_text_fg).properties(
                width=year_width, height=chart_height
            ),
            saved_text,
            spacing=4,
        ),
        spacing=10,
    )


def test_dashboard_chart_builds_valid_spec(ledger_db):
    chart = _build_charts(ledger_db)
    spec = chart.to_dict()
    assert "hconcat" in spec
    assert len(spec["hconcat"]) == 2


def test_dashboard_main_chart_has_correct_layers(ledger_db):
    chart = _build_charts(ledger_db)
    spec = chart.to_dict()
    main = spec["hconcat"][0]
    assert "layer" in main
    layer_types = [
        lay["mark"]["type"] if isinstance(lay["mark"], dict) else lay["mark"]
        for lay in main["layer"]
    ]
    assert "area" in layer_types
    assert layer_types.count("text") >= 2


def test_dashboard_no_invalid_mark_params(ledger_db):
    chart = _build_charts(ledger_db)
    import json

    spec_str = json.dumps(chart.to_dict())
    assert "paintOrder" not in spec_str

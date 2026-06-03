import marimo

__generated_with = "0.10.0"
app = marimo.App(width="wide", title="Dinary Analytics")


@app.cell
def _():
    import json
    import os

    import altair as alt
    import marimo as mo
    import polars as pl
    from google import genai
    from google.genai import types

    from dinary_analytics.connection import LEDGER_SCHEMA, open_ledger

    return LEDGER_SCHEMA, alt, genai, json, mo, open_ledger, os, pl, types


@app.cell
def _(mo):
    mo.md("# Dinary Analytics Dashboard")


@app.cell
def _(open_ledger, pl):
    _con = open_ledger()
    try:
        _top10_rows = _con.execute("""
            SELECT c.name
            FROM ledger.expenses e
            JOIN ledger.categories c ON e.category_id = c.id
            WHERE e.datetime >= (CURRENT_DATE - INTERVAL '12 months')
            GROUP BY c.name
            ORDER BY SUM(e.amount) DESC
            LIMIT 10
        """).fetchall()

        _top10 = [r[0] for r in _top10_rows]

        _exp_rows = _con.execute(
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
            [_top10],
        ).fetchall()

        _inc_rows = _con.execute("""
            SELECT
                strftime(make_date(year, month, 1), '%Y-%m') AS month,
                CAST(amount AS DOUBLE) AS income
            FROM ledger.income
            WHERE make_date(year, month, 1) >= (CURRENT_DATE - INTERVAL '12 months')::DATE
            ORDER BY month
        """).fetchall()
    finally:
        _con.close()

    category_order = _top10 + ["Other"]
    _rank_df = pl.DataFrame(
        {"category": category_order, "cat_rank": list(range(len(category_order)))},
    )

    expense_monthly_df = pl.DataFrame(
        {
            "month": [r[0] for r in _exp_rows],
            "category": [r[1] for r in _exp_rows],
            "total": [float(r[2]) for r in _exp_rows],
        },
    ).join(_rank_df, on="category", how="left")

    income_monthly_df = pl.DataFrame(
        {
            "month": [r[0] for r in _inc_rows],
            "income": [float(r[1]) for r in _inc_rows],
        },
    )

    year_df = (
        expense_monthly_df.group_by("category")
        .agg(pl.sum("total"), pl.first("cat_rank"))
        .sort("total", descending=True)
    )

    total_annual_income = float(income_monthly_df["income"].sum())

    return category_order, expense_monthly_df, income_monthly_df, total_annual_income, year_df


@app.cell
def _(
    alt,
    category_order,
    expense_monthly_df,
    income_monthly_df,
    pl,
    total_annual_income,
    year_df,
):
    _CHART_WIDTH = 700
    _CHART_HEIGHT = 400
    _YEAR_WIDTH = 160

    # Annual savings
    _total_expenses = float(year_df["total"].sum())
    _annual_saved = total_annual_income - _total_expenses
    _sign = "+" if _annual_saved >= 0 else ""
    _savings_color = "#2ca02c" if _annual_saved >= 0 else "#d62728"

    # Monthly savings for the overlay line
    _monthly_totals = (
        expense_monthly_df.group_by("month").agg(pl.sum("total").alias("expenses")).sort("month")
    )
    _savings_df = (
        income_monthly_df.join(_monthly_totals, on="month", how="left")
        .fill_null(0)
        .sort("month")
        .with_columns((pl.col("income") - pl.col("expenses")).alias("saved"))
    )

    # Midpoints for left-edge labels at the first month
    _first_month = expense_monthly_df["month"].min()
    _label_df = (
        expense_monthly_df.filter(pl.col("month") == _first_month)
        .sort("cat_rank")
        .with_columns(pl.col("total").cum_sum().alias("y_top"))
        .with_columns((pl.col("y_top") - pl.col("total") / 2).alias("y_mid"))
    )

    _color_scale = alt.Scale(domain=category_order, scheme="tableau20")

    _areas = (
        alt.Chart(expense_monthly_df)
        .mark_area(opacity=0.85, interpolate="monotone")
        .encode(
            x=alt.X("month:O", title="Month"),
            y=alt.Y("total:Q", stack=True, title="EUR"),
            color=alt.Color("category:N", scale=_color_scale, legend=None),
            order=alt.Order("cat_rank:Q", sort="ascending"),
            tooltip=["month:O", "category:N", alt.Tooltip("total:Q", format=".0f")],
        )
    )
    # White text, thin dark outline — readable on all tableau20 colours
    _labels = (
        alt.Chart(_label_df)
        .mark_text(align="left", dx=5, fontSize=11, fontWeight="bold")
        .encode(
            x=alt.X("month:O"),
            y=alt.Y("y_mid:Q"),
            text=alt.Text("category:N"),
            color=alt.value("#333333"),
        )
    )
    # Savings line on a right-side independent y axis
    _savings_line = (
        alt.Chart(_savings_df)
        .mark_line(
            color="#555555",
            strokeWidth=2,
            strokeDash=[6, 3],
            point={"color": "#555555", "size": 40, "filled": True},
        )
        .encode(
            x=alt.X("month:O"),
            y=alt.Y("saved:Q", title="saved / month (EUR)", axis=alt.Axis(orient="right")),
            tooltip=["month:O", alt.Tooltip("saved:Q", format=".0f", title="Saved")],
        )
    )
    # Label pinned to the last point of the savings line
    _savings_label = (
        alt.Chart(_savings_df.tail(1))
        .mark_text(align="right", dx=-6, dy=-10, fontSize=10, fontWeight="bold")
        .encode(
            x=alt.X("month:O"),
            y=alt.Y("saved:Q"),
            text=alt.value("saved"),
            color=alt.value("#555555"),
        )
    )

    # Right panel: saved text + year totals
    # Year bars reversed so top-of-bars matches top-of-stack (Other at top, еда at bottom)
    _year_order = list(reversed(category_order))
    _year_df_pos = year_df.with_columns(pl.lit(0.0).alias("label_x"))
    _year_bars = (
        alt.Chart(_year_df_pos)
        .mark_bar()
        .encode(
            y=alt.Y("category:N", sort=_year_order, title=None, axis=None),
            x=alt.X("total:Q", title="Year total", axis=alt.Axis(tickCount=3, format="~s")),
            color=alt.Color("category:N", scale=_color_scale, legend=None),
            tooltip=["category:N", alt.Tooltip("total:Q", format=".0f")],
        )
    )
    _year_text = (
        alt.Chart(_year_df_pos)
        .mark_text(align="left", dx=5, fontSize=10)
        .encode(
            y=alt.Y("category:N", sort=_year_order, axis=None),
            x=alt.X("label_x:Q"),
            text="category:N",
            color=alt.value("#333333"),
        )
    )
    _saved_text = (
        alt.Chart(pl.DataFrame({"t": [f"Saved {_sign}{_annual_saved:,.0f}€"]}))
        .mark_text(fontSize=13, fontWeight="bold", align="center", baseline="middle")
        .encode(
            x=alt.value(_YEAR_WIDTH / 2),
            y=alt.value(15),
            text="t:N",
            color=alt.value(_savings_color),
        )
        .properties(width=_YEAR_WIDTH, height=30)
    )

    alt.hconcat(
        alt.layer(alt.layer(_areas, _labels), _savings_line, _savings_label)
        .resolve_scale(y="independent")
        .properties(width=_CHART_WIDTH, height=_CHART_HEIGHT, title="Monthly Expenses by Category")
        .interactive(),
        alt.vconcat(
            alt.layer(_year_bars, _year_text).properties(width=_YEAR_WIDTH, height=_CHART_HEIGHT),
            _saved_text,
            spacing=4,
        ),
        spacing=10,
    )


@app.cell
def _(LEDGER_SCHEMA, genai, json, mo, open_ledger, os, types):
    _api_key = os.getenv("GOOGLE_AI_STUDIO_API_KEY", "")

    if not _api_key:
        _status = mo.callout(
            mo.md("**Set `GOOGLE_AI_STUDIO_API_KEY` to enable AI chat.**"),
            kind="warn",
        )
    else:
        _status = mo.md("")

    _system_prompt = (
        "You are a financial analytics assistant for the dinary expense tracker. "
        "Use the query tool to answer questions about the user's expenses. "
        "Tables are in the 'ledger' schema — prefix all table names: "
        "ledger.expenses, ledger.categories, ledger.events, etc. "
        "The amount column in ledger.expenses is in EUR (accounting currency). "
        "Schema:\n" + LEDGER_SCHEMA
    )

    def _query_ledger(sql: str) -> str:
        """Execute read-only SQL against the expense ledger. Use 'ledger.' prefix for all tables."""
        _con = open_ledger()
        try:
            _res = _con.execute(sql)
            _cols = [d[0] for d in _res.description]
            _rows = _res.fetchall()
            return json.dumps(
                [dict(zip(_cols, row, strict=True)) for row in _rows],
                default=str,
            )
        finally:
            _con.close()

    def _chat_model(messages, _config):
        if not _api_key:
            return "Set GOOGLE_AI_STUDIO_API_KEY to enable chat."

        _client = genai.Client(api_key=_api_key)
        _history = [
            types.Content(
                role="user" if m.role == "user" else "model",
                parts=[
                    types.Part(
                        text=m.content if isinstance(m.content, str) else str(m.content),
                    ),
                ],
            )
            for m in messages
        ]
        _response = _client.models.generate_content(
            model="gemini-2.0-flash",
            contents=_history,
            config=types.GenerateContentConfig(
                system_instruction=_system_prompt,
                tools=[_query_ledger],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=False,
                ),
            ),
        )
        return _response.text or ""

    _chat = mo.ui.chat(
        _chat_model,
        prompts=[
            "What did I spend most on last month?",
            "Show my top 5 expense categories this year.",
            "How does my spending compare month over month?",
            "What is my total income vs expenses this year?",
        ],
    )
    mo.vstack([_status, _chat])


if __name__ == "__main__":
    app.run()

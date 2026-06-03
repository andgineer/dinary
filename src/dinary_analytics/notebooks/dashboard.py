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

    from dinary_analytics.charts import make_chart_pair
    from dinary_analytics.connection import LEDGER_SCHEMA, open_ledger

    return LEDGER_SCHEMA, alt, genai, json, make_chart_pair, mo, open_ledger, os, pl, types


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
    category_order,
    expense_monthly_df,
    income_monthly_df,
    make_chart_pair,
    total_annual_income,
    year_df,
):
    make_chart_pair(
        expense_monthly_df,
        income_monthly_df,
        year_df,
        total_annual_income,
        category_order,
    )


@app.cell
def _(mo, open_ledger):
    _con = open_ledger()
    try:
        _year_rows = _con.execute("""
            SELECT DISTINCT EXTRACT(YEAR FROM datetime::TIMESTAMP)::INT AS yr
            FROM ledger.expenses
            ORDER BY yr DESC
        """).fetchall()
    finally:
        _con.close()

    _all_years = [str(r[0]) for r in _year_rows]

    year_selector = mo.ui.multiselect(
        options=_all_years,
        value=[],
        label="Compare years:",
    )
    year_selector
    return (year_selector,)


@app.cell
def _(category_order, open_ledger, pl, year_selector):
    _selected = [int(y) for y in year_selector.value]
    _rank_df = pl.DataFrame(
        {"category": category_order, "cat_rank": list(range(len(category_order)))},
    )
    _top10 = category_order[:-1]

    if not _selected:
        comp_expense_monthly_df = pl.DataFrame(
            {"month": [], "category": [], "total": [], "cat_rank": []},
            schema={
                "month": pl.String,
                "category": pl.String,
                "total": pl.Float64,
                "cat_rank": pl.Int32,
            },
        )
        comp_income_monthly_df = pl.DataFrame(
            {"month": [], "income": []},
            schema={"month": pl.String, "income": pl.Float64},
        )
    else:
        _con = open_ledger()
        try:
            _exp_rows = _con.execute(
                """
                SELECT
                    strftime(e.datetime::TIMESTAMP, '%Y-%m') AS month,
                    CASE WHEN list_contains($1::VARCHAR[], c.name)
                        THEN c.name ELSE 'Other' END AS category,
                    CAST(SUM(e.amount) AS DOUBLE) AS total
                FROM ledger.expenses e
                JOIN ledger.categories c ON e.category_id = c.id
                WHERE list_contains($2::INT[], EXTRACT(YEAR FROM e.datetime::TIMESTAMP)::INT)
                GROUP BY month, category
                ORDER BY month
            """,
                [_top10, _selected],
            ).fetchall()
            _inc_rows = _con.execute(
                """
                SELECT
                    strftime(make_date(year, month, 1), '%Y-%m') AS month,
                    CAST(amount AS DOUBLE) AS income
                FROM ledger.income
                WHERE list_contains($1::INT[], year)
                ORDER BY month
            """,
                [_selected],
            ).fetchall()
        finally:
            _con.close()
        comp_expense_monthly_df = pl.DataFrame(
            {
                "month": [r[0] for r in _exp_rows],
                "category": [r[1] for r in _exp_rows],
                "total": [float(r[2]) for r in _exp_rows],
            },
        ).join(_rank_df, on="category", how="left")
        comp_income_monthly_df = pl.DataFrame(
            {"month": [r[0] for r in _inc_rows], "income": [float(r[1]) for r in _inc_rows]},
        )

    comp_year_df = (
        comp_expense_monthly_df.group_by("category")
        .agg(pl.sum("total"), pl.first("cat_rank"))
        .sort("total", descending=True)
    )
    total_comp_income = float(comp_income_monthly_df["income"].sum())

    return comp_expense_monthly_df, comp_income_monthly_df, comp_year_df, total_comp_income


@app.cell
def _(
    category_order,
    comp_expense_monthly_df,
    comp_income_monthly_df,
    comp_year_df,
    make_chart_pair,
    total_comp_income,
    year_selector,
):
    _chart = None
    if year_selector.value and not comp_expense_monthly_df.is_empty():
        _title = ", ".join(sorted(year_selector.value))
        _chart = make_chart_pair(
            comp_expense_monthly_df,
            comp_income_monthly_df,
            comp_year_df,
            total_comp_income,
            category_order,
            period_title=_title,
        )
    _chart


@app.cell
def _(LEDGER_SCHEMA, category_order, genai, json, mo, open_ledger, os, types):
    _api_key = os.getenv("GOOGLE_AI_STUDIO_API_KEY", "")

    if not _api_key:
        _status = mo.callout(
            mo.md("**Set `GOOGLE_AI_STUDIO_API_KEY` to enable AI chat.**"),
            kind="warn",
        )
    else:
        _status = mo.md("")

    _top10 = category_order[:-1]
    _top10_sql = ", ".join(f"'{c}'" for c in _top10)

    _system_prompt = (
        "You are a financial analytics assistant for the dinary expense tracker. "
        "Use the query tool to answer questions about the user's expenses. "
        "Tables are in the 'ledger' schema — prefix all table names: "
        "ledger.expenses, ledger.categories, ledger.events, etc. "
        "The amount column in ledger.expenses is in EUR (accounting currency). "
        "\n\nDashboard context: the stacked area chart shows the top-10 expense categories "
        "by spending over the last 12 months, plus 'Other'. "
        f"The 10 categories shown individually are: {', '.join(_top10)}. "
        "'Other' on the chart means ALL expenses whose category is NOT in that list — "
        "there is no category literally named 'Other' in the DB. "
        f"To query 'Other' expenses use: WHERE c.name NOT IN ({_top10_sql}).\n\n"
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
            model="gemini-2.5-flash",
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

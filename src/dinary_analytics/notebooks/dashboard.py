import marimo

__generated_with = "0.10.0"
app = marimo.App(width="wide", app_title="Dinary Analytics")


@app.cell
def _():
    import json
    import os
    from datetime import date

    import altair as alt
    import marimo as mo
    import polars as pl
    from google import genai
    from google.genai import types

    from dinary_analytics.charts import make_chart_pair, make_event_chart
    from dinary_analytics.connection import LEDGER_SCHEMA, open_ledger
    from dinary_analytics.settings import get_config_json, set_config_json

    return (
        LEDGER_SCHEMA,
        alt,
        date,
        genai,
        get_config_json,
        json,
        make_chart_pair,
        make_event_chart,
        mo,
        open_ledger,
        os,
        pl,
        set_config_json,
        types,
    )


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
def _(date, get_config_json, mo, open_ledger):
    _con = open_ledger()
    try:
        _year_rows = _con.execute("""
            SELECT DISTINCT EXTRACT(YEAR FROM datetime::TIMESTAMP)::INT AS yr
            FROM ledger.expenses
            ORDER BY yr DESC
        """).fetchall()
        _event_rows = _con.execute("""
            SELECT id, name,
                STRFTIME(date_from::DATE, '%Y-%m-%d'),
                STRFTIME(date_to::DATE, '%Y-%m-%d'),
                auto_attach_enabled
            FROM ledger.events
            ORDER BY date_to DESC
        """).fetchall()
        _tag_rows = _con.execute("""
            SELECT id, name FROM ledger.tags
            WHERE is_active = TRUE
            ORDER BY name
        """).fetchall()
    finally:
        _con.close()

    _today = str(date.today())

    _all_years = [str(r[0]) for r in _year_rows]
    _saved_years = get_config_json("dashboard.compare_years") or []
    _valid_years = [y for y in _saved_years if y in _all_years]
    year_selector = mo.ui.multiselect(
        options=_all_years,
        value=_valid_years,
        label="Compare years:",
    )

    all_events_info = {str(r[0]): r for r in _event_rows}
    # marimo multiselect {label: value}: value= accepts labels (keys), .value returns IDs (values)
    _event_select_opts = {f"{r[1]} ({r[2][:10]})": str(r[0]) for r in _event_rows if r[2]}
    _last_completed_label = next(
        (f"{r[1]} ({r[2][:10]})" for r in _event_rows if r[3] and r[3] < _today),
        None,
    )
    event_selector = mo.ui.dropdown(
        options=_event_select_opts,
        value=_last_completed_label,
        label="Event:",
    )

    tag_name_map = {str(r[0]): r[1] for r in _tag_rows}
    _tag_opts = {r[1]: str(r[0]) for r in _tag_rows}
    _saved_tag_id = get_config_json("dashboard.tag_id")
    _tag_default_label = (
        tag_name_map.get(_saved_tag_id)
        if _saved_tag_id and _saved_tag_id in tag_name_map
        else (_tag_rows[0][1] if _tag_rows else None)
    )
    tag_selector = mo.ui.dropdown(
        options=_tag_opts,
        value=_tag_default_label,
        label="Tag:",
    )

    _current_year = _today[:4]
    _saved_tag_year = get_config_json("dashboard.tag_year")
    _tag_year_default = (
        _saved_tag_year
        if _saved_tag_year and _saved_tag_year in _all_years
        else (
            _current_year
            if _current_year in _all_years
            else (_all_years[0] if _all_years else None)
        )
    )
    tag_year_selector = mo.ui.dropdown(
        options=_all_years,
        value=_tag_year_default,
        label="Year:",
    )

    year_selector
    return (
        all_events_info,
        event_selector,
        tag_name_map,
        tag_selector,
        tag_year_selector,
        year_selector,
    )


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
def _(all_events_info, event_selector, make_event_chart, mo, open_ledger, pl):
    _eid = event_selector.value
    if not _eid:
        event_chart = mo.md("*Select an event above.*")
    else:
        _con = open_ledger()
        try:
            _rows = _con.execute(
                """
                SELECT c.name AS category, CAST(SUM(e.amount) AS DOUBLE) AS total
                FROM ledger.expenses e
                JOIN ledger.categories c ON e.category_id = c.id
                WHERE e.event_id = $1
                GROUP BY c.name
                ORDER BY total DESC
                """,
                [int(_eid)],
            ).fetchall()
        finally:
            _con.close()
        _expense_df = pl.DataFrame(
            {"category": [r[0] for r in _rows], "total": [float(r[1]) for r in _rows]},
        )
        _ev = all_events_info.get(_eid)
        _ev_title = _ev[1] if _ev else _eid
        if _expense_df.is_empty():
            event_chart = mo.md(f"*No expenses for {_ev_title}.*")
        else:
            event_chart = make_event_chart(_expense_df, _ev_title)

    return (event_chart,)


@app.cell
def _(make_event_chart, mo, open_ledger, pl, tag_name_map, tag_selector, tag_year_selector):
    _tid = tag_selector.value
    _yr = tag_year_selector.value
    if not _tid or not _yr:
        tag_chart = mo.md("*No tags available.*")
    else:
        _con = open_ledger()
        try:
            _rows = _con.execute(
                """
                SELECT c.name AS category, CAST(SUM(e.amount) AS DOUBLE) AS total
                FROM ledger.expenses e
                JOIN ledger.categories c ON e.category_id = c.id
                JOIN ledger.expense_tags et ON et.expense_id = e.id
                WHERE et.tag_id = $1
                  AND EXTRACT(YEAR FROM e.datetime::TIMESTAMP)::INT = $2
                GROUP BY c.name
                ORDER BY total DESC
                """,
                [int(_tid), int(_yr)],
            ).fetchall()
        finally:
            _con.close()
        _expense_df = pl.DataFrame(
            {"category": [r[0] for r in _rows], "total": [float(r[1]) for r in _rows]},
        )
        if _expense_df.is_empty():
            tag_chart = mo.md(f"*No expenses for this tag in {_yr}.*")
        else:
            tag_chart = make_event_chart(_expense_df, f"{tag_name_map.get(_tid, _tid)} {_yr}")

    return (tag_chart,)


@app.cell
def _(
    event_chart,
    event_selector,
    mo,
    tag_chart,
    tag_selector,
    tag_year_selector,
):
    _card_style = {
        "border": "1px solid rgba(255,255,255,0.1)",
        "border-radius": "10px",
        "padding": "1rem",
    }
    _event_card = mo.style(
        mo.vstack([event_selector, event_chart]),
        style=_card_style,
    )
    _tag_card = mo.style(
        mo.vstack([mo.hstack([tag_selector, tag_year_selector], justify="start"), tag_chart]),
        style=_card_style,
    )
    mo.hstack([_event_card, _tag_card], justify="start")


@app.cell
def _(set_config_json, tag_selector, tag_year_selector, year_selector):
    set_config_json("dashboard.compare_years", year_selector.value)
    set_config_json("dashboard.tag_id", tag_selector.value)
    set_config_json("dashboard.tag_year", tag_year_selector.value)


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

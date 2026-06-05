import marimo

__generated_with = "0.10.0"
app = marimo.App(width="wide", app_title="Dinary Analytics")


@app.cell
def _():
    import json
    from datetime import date, timedelta

    import altair as alt
    import marimo as mo
    import polars as pl

    from dinary_analytics.charts import make_basket_chart, make_chart_pair, make_event_chart
    from dinary_analytics.connection import LEDGER_SCHEMA, load_query, open_ledger
    from dinary_analytics.llm import providers_available, run_chat_turn
    from dinary_analytics.settings import (
        delete_view,
        get_config_json,
        save_view,
        set_config_json,
    )
    from dinary_analytics.views import empty_view_frame, load_pinned_view_frames, load_view_frame

    return (
        LEDGER_SCHEMA,
        alt,
        date,
        delete_view,
        empty_view_frame,
        get_config_json,
        json,
        load_pinned_view_frames,
        load_query,
        load_view_frame,
        make_basket_chart,
        make_chart_pair,
        make_event_chart,
        mo,
        open_ledger,
        pl,
        providers_available,
        run_chat_turn,
        save_view,
        set_config_json,
        timedelta,
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
def _(mo):
    draft_view, set_draft_view = mo.state(None)
    view_list_ver, bump_view_list = mo.state(0)
    return bump_view_list, draft_view, set_draft_view, view_list_ver


@app.cell
def _(date, timedelta):
    def period_date_from(period: str) -> str:
        """Return the ISO start date for a dashboard period label."""
        if period == "This year":
            return f"{date.today().year}-01-01"
        if period == "Last year":
            return f"{date.today().year - 1}-01-01"
        return (date.today() - timedelta(days=365)).isoformat()

    return (period_date_from,)


@app.cell
def _(mo):
    period_selector = mo.ui.dropdown(
        options=["Last 12 months", "This year", "Last year"],
        value="Last 12 months",
        label="Period:",
    )
    period_selector
    return (period_selector,)


@app.cell
def _(mo):
    chat_history, set_chat_history = mo.state([])
    pending, set_pending = mo.state(None)  # text awaiting a reply, or None
    suggestions, set_suggestions = mo.state([])  # clickable follow-up questions
    input_ver, bump_input = mo.state(0)
    return (
        bump_input,
        chat_history,
        input_ver,
        pending,
        set_chat_history,
        set_pending,
        set_suggestions,
        suggestions,
    )


@app.cell
def _(chat_history, set_chat_history, set_pending, set_suggestions):
    def send_message(text: str) -> None:
        """Queue a user message for the analyst (the reply runs reactively)."""
        _text = (text or "").strip()
        if _text:
            set_suggestions([])  # old follow-ups no longer apply
            set_pending({"text": _text, "base": chat_history()})

    def retry_last() -> None:
        """Re-run the most recent user message, dropping the failed turn first."""
        _history = chat_history()
        _last_user = next((m["content"] for m in reversed(_history) if m["role"] == "user"), None)
        if _last_user is None:
            return
        _trimmed = list(_history)
        while _trimmed and _trimmed[-1]["role"] == "model":
            _trimmed.pop()
        if _trimmed and _trimmed[-1]["role"] == "user":
            _trimmed.pop()
        set_suggestions([])
        set_chat_history(_trimmed)
        set_pending({"text": _last_user, "base": _trimmed})

    def clear_chat() -> None:
        """Reset the conversation."""
        set_chat_history([])
        set_pending(None)
        set_suggestions([])

    return clear_chat, retry_last, send_message


@app.cell
def _(mo, send_message):
    _STARTERS = [
        "Rebuild my spending into 5–10 meaningful baskets and show the chart",
        "Which unusual expenses are worth pulling into their own basket?",
        "Merge all my trips into one basket",
        "Break the largest basket down in more detail",
    ]
    _chips = mo.hstack(
        [mo.ui.button(label=_p, on_click=lambda _v, _q=_p: send_message(_q)) for _p in _STARTERS],
        justify="start",
        wrap=True,
    )
    mo.vstack(
        [
            mo.md("### Talk to the analyst"),
            mo.md(
                "Click a suggestion or type a question. The analyst proposes a chart you "
                "can pin — its explanation and next steps appear in the reply.",
            ),
            _chips,
        ],
    )


@app.cell
def _(draft_view, empty_view_frame, load_view_frame, period_date_from, period_selector):
    _draft = draft_view()
    if _draft is None:
        view_data_df = empty_view_frame()
    else:
        view_data_df = load_view_frame(_draft, period_date_from(period_selector.value))
    return (view_data_df,)


@app.cell
def _(
    chat_history,
    clear_chat,
    draft_view,
    make_basket_chart,
    mo,
    pending,
    retry_last,
    view_data_df,
):
    _msgs = chat_history()
    _job = pending()
    _blocks: list = []
    for _m in _msgs:
        if _m["role"] == "user":
            _blocks.append(mo.callout(mo.md(f"**You:** {_m['content']}"), kind="neutral"))
        else:
            _blocks.append(mo.md(f"**Analyst:** {_m['content']}"))

    # Inline draft chart, right under the latest reply, with a caption tying it to the chat.
    if _job is None and draft_view() is not None and not view_data_df.is_empty():
        _blocks.append(mo.md("**Proposed view** — pin it below to keep it:"))
        _blocks.append(make_basket_chart(view_data_df, draft_view().get("name", "Draft view")))

    if _job is not None:
        _blocks.append(mo.callout(mo.md(f"**You:** {_job['text']}"), kind="neutral"))
        _blocks.append(mo.callout(mo.md("⏳ *Analyzing your spending…*"), kind="info"))

    if _msgs:
        _blocks.append(
            mo.hstack(
                [
                    mo.ui.button(label="🔁 Retry", on_click=lambda _v: retry_last()),
                    mo.ui.button(label="🗑 Clear", on_click=lambda _v: clear_chat()),
                ],
                justify="end",
            ),
        )

    if not _blocks:
        _blocks = [mo.md("*The conversation will appear here.*")]
    mo.vstack(_blocks)


@app.cell
def _(mo, pending, send_message, suggestions):
    _next = suggestions() if pending() is None else []
    if _next:
        _chips = mo.hstack(
            [mo.ui.button(label=_q, on_click=lambda _v, _t=_q: send_message(_t)) for _q in _next],
            justify="start",
            wrap=True,
        )
        _out = mo.vstack([mo.md("**Next steps to explore:**"), _chips])
    else:
        _out = mo.md("")
    _out


@app.cell
def _(
    ai_system_prompt,
    ai_tools,
    bump_input,
    input_ver,
    pending,
    run_chat_turn,
    set_chat_history,
    set_pending,
):
    _job = pending()
    if _job is not None:
        _reply = run_chat_turn(ai_system_prompt, ai_tools, _job["base"], _job["text"])
        set_chat_history(
            [
                *_job["base"],
                {"role": "user", "content": _job["text"]},
                {"role": "model", "content": _reply},
            ],
        )
        set_pending(None)
        bump_input(input_ver() + 1)


@app.cell
def _(ai_status, input_ver, mo, send_message):
    _ = input_ver()  # recreate the input after each send to clear it
    msg_input = mo.ui.text_area(placeholder="Ask the analyst…", full_width=True, rows=2)
    _send_btn = mo.ui.button(
        label="Send",
        kind="success",
        on_click=lambda _v: send_message(msg_input.value),
    )
    mo.vstack([ai_status, mo.hstack([msg_input, _send_btn], widths=[8, 1], align="end")])
    return (msg_input,)


@app.cell
def _(bump_view_list, draft_view, mo, save_view, view_list_ver):
    _draft = draft_view()
    if _draft is None:
        pin_controls = mo.md("")
    else:
        pin_name = mo.ui.text(
            value=_draft.get("name", ""),
            placeholder="View name",
            label="Pin draft as:",
        )

        def _pin(_value, _name_field=pin_name, _cfg=_draft) -> None:
            _name = _name_field.value.strip() or "Untitled view"
            save_view({**_cfg, "name": _name})
            bump_view_list(view_list_ver() + 1)

        pin_button = mo.ui.button(label="📌 Pin view", on_click=_pin)
        pin_controls = mo.hstack([pin_name, pin_button], justify="start")
    pin_controls


@app.cell
def _(load_pinned_view_frames, period_date_from, period_selector, view_list_ver):
    _ = view_list_ver()  # re-render the gallery whenever a view is pinned or deleted
    pinned_frames = load_pinned_view_frames(period_date_from(period_selector.value))
    return (pinned_frames,)


@app.cell
def _(
    bump_view_list,
    delete_view,
    make_basket_chart,
    mo,
    pinned_frames,
    set_draft_view,
    view_list_ver,
):
    if not pinned_frames:
        saved_gallery = mo.md(
            "*No pinned views yet — ask the analyst to propose a view and press 📌 Pin view.*",
        )
    else:
        _card_style = {
            "border": "1px solid rgba(255,255,255,0.1)",
            "border-radius": "10px",
            "padding": "1rem",
        }
        _cards = []
        for _vid, _cfg, _df in pinned_frames:
            _name = _cfg.get("name", _vid)
            _chart = (
                make_basket_chart(_df, _name)
                if not _df.is_empty()
                else mo.md(f"**{_name}**\n\n*No data for this period.*")
            )
            _open_btn = mo.ui.button(
                label="Open in draft",
                on_click=lambda _value, _c=_cfg: set_draft_view(_c),
            )
            _del_btn = mo.ui.button(
                label="Delete",
                on_click=lambda _value, _i=_vid: (
                    delete_view(_i),
                    bump_view_list(view_list_ver() + 1),
                ),
            )
            _cards.append(
                mo.style(
                    mo.vstack([_chart, mo.hstack([_open_btn, _del_btn], justify="start")]),
                    style=_card_style,
                ),
            )
        saved_gallery = mo.vstack(
            [mo.md("### Pinned views"), mo.hstack(_cards, justify="start", wrap=True)],
        )
    saved_gallery


@app.cell
def _(LEDGER_SCHEMA, category_order, mo, providers_available):
    if not providers_available():
        ai_status = mo.callout(
            mo.md("**No LLM providers configured** in `.deploy/llm_providers.toml`."),
            kind="warn",
        )
    else:
        ai_status = mo.md("")
    _top10 = category_order[:-1]
    _top10_sql = ", ".join(f"'{c}'" for c in _top10)
    ai_system_prompt = (
        "You are a financial analytics assistant for the dinary expense tracker. "
        "Use the query_ledger tool to answer questions about the user's expenses. "
        "Tables are in the 'ledger' schema — prefix all table names: "
        "ledger.expenses, ledger.categories, ledger.events, ledger.tags, etc. "
        "The amount column in ledger.expenses is in EUR (accounting currency). "
        "\n\nDashboard context: the stacked area chart shows the top-10 expense categories "
        "by spending over the last 12 months, plus 'Other'. "
        f"The 10 categories shown individually are: {', '.join(_top10)}. "
        "'Other' on the chart means ALL expenses whose category is NOT in that list — "
        "there is no category literally named 'Other' in the DB. "
        f"To query 'Other' expenses use: WHERE c.name NOT IN ({_top10_sql}).\n\n"
        "Analytics Views: you are the analyst, the user is the client who reacts. "
        "A view groups expenses into named 'baskets' by event or tag membership. "
        "When asked to build or rebuild a view: first call query_summary to "
        "examine actual events, tags and category groups; then call propose_view with "
        "5–10 baskets that reveal non-obvious, actionable patterns — avoid obvious dominant "
        "items like rent, use events for trips/projects and tags for themes, merge "
        "negligible items into the default basket. Justify each basket with concrete numbers "
        "(amount and share of total). Use set_chart_type/propose_view again to refine when "
        "the user reacts. End every turn by calling suggest_next with 3–5 short follow-up "
        "questions — do NOT list them in your text, they render as clickable buttons. "
        "The draft chart renders live below the chat. To pin the current "
        "draft permanently call save_current_view(name) — or the user can press the Pin "
        "button. Never ask the user to name categories, events or tags; do it from data.\n\n"
        "Schema:\n" + LEDGER_SCHEMA
    )
    return ai_status, ai_system_prompt


@app.cell
def _(json, load_query, open_ledger):
    def _query_ledger_fn(sql: str) -> str:
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

    def _query_summary_fn() -> str:
        """Return a JSON summary of events, tags and category groups for the last 12 months."""
        _con = open_ledger()
        try:
            return _con.execute(load_query("spending_summary")).fetchone()[0]
        finally:
            _con.close()

    ai_ledger_tools_pair = (_query_ledger_fn, _query_summary_fn)
    return (ai_ledger_tools_pair,)


@app.cell
def _(bump_view_list, delete_view, save_view, set_draft_view, set_suggestions, view_list_ver):
    _ts: dict = {"draft": None}

    def _suggest_next(questions: list[str]) -> str:
        """Offer up to 5 follow-up questions as clickable buttons.

        Call this to propose next steps instead of listing them in your text reply.
        """
        set_suggestions([str(_q) for _q in questions][:5])
        return f"Offered {len(questions)} follow-up questions."

    def _propose_view(
        baskets: list[dict],
        default_basket: str,
        chart_type: str = "stacked_bar_monthly",
    ) -> str:
        """Propose a complete analytics view draft. Always pass the full basket list."""
        _cfg = {"baskets": baskets, "default_basket": default_basket, "chart_type": chart_type}
        _ts["draft"] = _cfg
        set_draft_view(_cfg)
        return f"View draft set with {len(baskets)} baskets, default '{default_basket}'."

    def _set_chart_type(chart_type: str) -> str:
        """Set the chart_type on the current draft view."""
        _cur = dict(_ts.get("draft") or {"baskets": [], "default_basket": "Other"})
        _cur["chart_type"] = chart_type
        _ts["draft"] = _cur
        set_draft_view(_cur)
        return f"Chart type set to '{chart_type}'."

    def _save_current_view(name: str) -> str:
        """Save the current draft view under a name. Returns the assigned ID."""
        _cur = dict(_ts.get("draft") or {"baskets": [], "default_basket": "Other"})
        _cur["name"] = name
        _vid = save_view(_cur)
        bump_view_list(view_list_ver() + 1)
        return f"View '{name}' saved with id {_vid}."

    def _delete_view_fn(view_id: str) -> str:
        """Delete a saved analytics view by ID."""
        delete_view(view_id)
        bump_view_list(view_list_ver() + 1)
        return f"View {view_id} deleted."

    ai_view_tools_tuple = (
        _propose_view,
        _set_chart_type,
        _save_current_view,
        _delete_view_fn,
        _suggest_next,
    )
    return (ai_view_tools_tuple,)


@app.cell
def _(ai_ledger_tools_pair, ai_view_tools_tuple):
    ai_tools = list(ai_ledger_tools_pair) + list(ai_view_tools_tuple)
    return (ai_tools,)


@app.cell
def _(mo):
    mo.accordion(
        {
            "Connect external AI client (Claude Desktop / Claude Code)": mo.vstack(
                [
                    mo.md(
                        "MCP server runs at **`http://localhost:8765/mcp`** while `inv analytics` is active."
                        " Only Claude Desktop and Claude Code support MCP.\n\n"
                        "### Claude Desktop\n\n"
                        "Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:",
                    ),
                    mo.plain_text(
                        "{\n"
                        '  "mcpServers": {\n'
                        '    "dinary-analytics": {\n'
                        '      "command": "npx",\n'
                        '      "args": ["mcp-remote", "http://localhost:8765/mcp"]\n'
                        "    }\n"
                        "  }\n"
                        "}",
                    ),
                    mo.md(
                        "Restart Claude Desktop. The server appears under **Search and tools**.\n\n"
                        "### Claude Code (CLI)",
                    ),
                    mo.plain_text(
                        "claude mcp add --transport http dinary-analytics http://localhost:8765/mcp",
                    ),
                    mo.md(
                        "Verify with `claude mcp list`. "
                        "Tools: `query`, `schema`, `list_views`, `get_view`, `save_view`,"
                        " `delete_view`, `get_config`, `set_config`.",
                    ),
                ],
            ),
        },
    )


if __name__ == "__main__":
    app.run()

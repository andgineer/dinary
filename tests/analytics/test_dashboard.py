"""Tests for dashboard chart construction.

Tests call make_chart_pair and make_event_chart from dinary_analytics.charts directly —
the same functions the notebook imports — so any invalid Altair params
or broken imports are caught here before runtime.

cell.run() tests execute the actual notebook cells against a real SQLite test DB so
regressions in cell logic are caught without duplicating that logic in the tests.
"""

import json
import sqlite3
from functools import partial

import allure
import polars as pl
import pytest

import dinary_analytics.notebooks.dashboard as _dash_module
from dinary_analytics.charts import ChartSize, make_basket_chart, make_chart_pair, make_event_chart
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
        CREATE TABLE category_groups (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_active  INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO category_groups VALUES (1, 'Питание', 0, 1);
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
        replica_status={"ok": True},
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


@pytest.fixture
def basket_df():
    return pl.DataFrame(
        {
            "basket_name": ["Travel", "Travel", "Food", "Food"],
            "year_month": ["2025-01", "2025-02", "2025-01", "2025-02"],
            "group_name": ["Transport", "Transport", "Groceries", "Groceries"],
            "total_amount": [200.0, 150.0, 300.0, 280.0],
        }
    )


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_basket_chart_builds_valid_spec(basket_df):
    chart = make_basket_chart(basket_df, "My View")
    spec = chart.to_dict()
    assert "vconcat" in spec
    assert len(spec["vconcat"]) == 2


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_basket_chart_top_panel_is_bar(basket_df):
    chart = make_basket_chart(basket_df)
    spec = chart.to_dict()
    top = spec["vconcat"][0]
    mark = top.get("mark", {})
    mark_type = mark.get("type") if isinstance(mark, dict) else mark
    assert mark_type == "bar"


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_basket_chart_has_selection_param(basket_df):
    chart = make_basket_chart(basket_df)
    spec = chart.to_dict()
    # Altair hoists params to the outermost vconcat level
    all_params = spec.get("params", []) + spec["vconcat"][0].get("params", [])
    assert any(p.get("select", {}).get("type") == "point" for p in all_params)


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_basket_chart_bottom_has_filter_transform(basket_df):
    chart = make_basket_chart(basket_df)
    spec = chart.to_dict()
    bottom = spec["vconcat"][1]
    transforms = bottom.get("transform", [])
    assert any("filter" in t for t in transforms)


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_basket_chart_independent_color_scales(basket_df):
    chart = make_basket_chart(basket_df)
    spec = chart.to_dict()
    resolve = spec.get("resolve", {})
    assert resolve.get("scale", {}).get("color") == "independent"


# ---------------------------------------------------------------------------
# cell.run() integration tests — execute real notebook cells against test DB
# ---------------------------------------------------------------------------


class _DropdownStub:
    """Minimal stand-in for mo.ui.dropdown inside cell.run() calls."""

    def __init__(self, value: str) -> None:
        self.value = value


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_period_selector_cell_runs():
    """Period selector cell builds the dropdown with the default period."""
    import marimo as mo

    cells = list(_dash_module.app._cell_manager.cells())
    sel_cell = next(c for c in cells if "period_selector" in c.defs)

    _, defs = sel_cell.run(mo=mo)

    assert defs["period_selector"] is not None
    assert defs["period_selector"].value == "Last 12 months"


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_fetch_replica_status_survives_refresh_post_failure(monkeypatch):
    """A failed POST /refresh/now (service restarted mid-click) must not raise."""
    import contextlib
    import urllib.error
    import urllib.request
    from unittest.mock import MagicMock

    health_response = MagicMock()
    health_response.read.return_value = json.dumps(
        {"ok": True, "last_refresh": "2024-01-01T00:00:00+00:00", "error": None},
    ).encode()
    health_response.__enter__ = lambda _self: health_response
    health_response.__exit__ = lambda *_args: None

    def fake_urlopen(url, *args, **kwargs):
        if isinstance(url, urllib.request.Request) and url.get_method() == "POST":
            raise urllib.error.URLError("refused")
        return health_response

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    cells = list(_dash_module.app._cell_manager.cells())
    cell = next(c for c in cells if "fetch_replica_status" in c.defs)

    _, defs = cell.run(contextlib=contextlib, json=json, urllib=urllib)
    fetch_replica_status = defs["fetch_replica_status"]

    cleared = []
    status = fetch_replica_status(12345, lambda: True, cleared.append)

    assert status["ok"] is True
    assert cleared == [False]


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_status_bar_cell_surfaces_refresh_error():
    """A non-null refresh error is shown even when the replica is otherwise ok."""
    import datetime
    import marimo as mo

    cells = list(_dash_module.app._cell_manager.cells())
    cell = next(
        c
        for c in cells
        if {"replica_status", "set_refresh_requested", "set_address_configured"} <= c.refs
    )

    output, _ = cell.run(
        datetime=datetime.datetime,
        mo=mo,
        replica_status={
            "ok": True,
            "last_refresh": "2024-01-01T00:00:00+00:00",
            "error": "failed to download ledger snapshot: HTTP Error 502: Bad Gateway",
        },
        set_address_configured=lambda _v: None,
        set_refresh_requested=lambda _v: None,
        timezone=datetime.timezone,
    )

    _, html = output._mime_()
    assert "Last refresh attempt failed" in html
    assert "Bad Gateway" in html


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_status_bar_cell_hides_error_callout_when_refresh_ok():
    """No error callout is shown when the last refresh succeeded."""
    import datetime
    import marimo as mo

    cells = list(_dash_module.app._cell_manager.cells())
    cell = next(
        c
        for c in cells
        if {"replica_status", "set_refresh_requested", "set_address_configured"} <= c.refs
    )

    output, _ = cell.run(
        datetime=datetime.datetime,
        mo=mo,
        replica_status={
            "ok": True,
            "last_refresh": "2024-01-01T00:00:00+00:00",
            "error": None,
        },
        set_address_configured=lambda _v: None,
        set_refresh_requested=lambda _v: None,
        timezone=datetime.timezone,
    )

    _, html = output._mime_()
    assert "Last refresh attempt failed" not in html


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_followups_cell_renders_clickable_buttons_and_hides_while_pending():
    """Follow-up suggestions render as clickable buttons; hidden while a reply is pending."""
    import marimo as mo

    cells = list(_dash_module.app._cell_manager.cells())
    cell = next(c for c in cells if c.refs == {"mo", "pending", "send_message", "suggestions"})

    shown, _ = cell.run(
        mo=mo,
        pending=lambda: None,
        send_message=lambda _t: None,
        suggestions=lambda: ["Break down rent", "Compare to last year"],
    )
    assert shown is not None

    # while a reply is pending, stale follow-ups are not shown
    hidden, _ = cell.run(
        mo=mo,
        pending=lambda: {"text": "x"},
        send_message=lambda _t: None,
        suggestions=lambda: ["Break down rent"],
    )
    assert hidden is not None


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_suggest_next_tool_sets_suggestions():
    """The suggest_next LLM tool stores up to 5 follow-up questions in state."""
    cells = list(_dash_module.app._cell_manager.cells())
    tools_cell = next(c for c in cells if "ai_view_tools_tuple" in c.defs)

    stored: dict = {}
    _, defs = tools_cell.run(
        bump_view_list=lambda _v: None,
        delete_view=lambda _i: None,
        save_view=lambda _cfg: "id",
        set_draft_view=lambda _c: None,
        set_suggestions=lambda s: stored.__setitem__("s", s),
        view_list_ver=lambda: 0,
    )

    tools = defs["ai_view_tools_tuple"]
    suggest = next(fn for fn in tools if "suggest_next" in fn.__name__)
    suggest(["a", "b", "c", "d", "e", "f"])
    assert stored["s"] == ["a", "b", "c", "d", "e"]  # capped at 5


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_chips_cell_builds_clickable_starters():
    """The starter-suggestions cell builds clickable buttons (not plain prose)."""
    import marimo as mo

    cells = list(_dash_module.app._cell_manager.cells())
    chips_cell = next(c for c in cells if c.refs == {"mo", "send_message"})

    output, _ = chips_cell.run(mo=mo, send_message=lambda _t: None)

    assert output is not None


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_chat_input_cell_builds():
    """The input cell builds a text area + send button without invalid marimo params."""
    import marimo as mo

    cells = list(_dash_module.app._cell_manager.cells())
    input_cell = next(c for c in cells if "msg_input" in c.defs)

    output, defs = input_cell.run(
        ai_status=mo.md(""),
        input_ver=lambda: 0,
        mo=mo,
        send_message=lambda _t: None,
    )

    assert output is not None
    assert defs["msg_input"] is not None


def _log_cell():
    cells = list(_dash_module.app._cell_manager.cells())
    return next(c for c in cells if "pending" in c.refs and "retry_last" in c.refs)


def _actions_cell():
    cells = list(_dash_module.app._cell_manager.cells())
    return next(c for c in cells if "send_message" in c.defs)


def _runner_cell():
    cells = list(_dash_module.app._cell_manager.cells())
    return next(c for c in cells if "run_chat_turn" in c.refs and not c.defs)


def _run_log(*, chat_history, pending, view_data_df):
    import marimo as mo

    from dinary_analytics.charts import make_basket_chart

    output, _ = _log_cell().run(
        chat_history=lambda: chat_history,
        clear_chat=lambda: None,
        draft_view=lambda: None,
        make_basket_chart=make_basket_chart,
        mo=mo,
        pending=lambda: pending,
        retry_last=lambda: None,
        view_data_df=view_data_df,
    )
    return output


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_chat_log_cell_empty_pending_and_populated():
    """Log cell: placeholder when empty, thinking bubble when pending, bubbles when populated."""
    from dinary_analytics.views import empty_view_frame

    empty_df = empty_view_frame()
    assert _run_log(chat_history=[], pending=None, view_data_df=empty_df) is not None
    assert _run_log(chat_history=[], pending={"text": "hi"}, view_data_df=empty_df) is not None
    msgs = [{"role": "user", "content": "hi"}, {"role": "model", "content": "hello"}]
    assert _run_log(chat_history=msgs, pending=None, view_data_df=empty_df) is not None


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_send_message_queues_pending_job():
    """send_message stores a pending job (text + base history); blank input is a no-op."""
    history = [{"role": "user", "content": "old"}, {"role": "model", "content": "ans"}]
    box: dict = {}

    cleared: list = []
    _, defs = _actions_cell().run(
        chat_history=lambda: history,
        set_chat_history=lambda _new: None,
        set_pending=lambda job: box.__setitem__("job", job),
        set_suggestions=lambda s: cleared.append(s),
    )

    defs["send_message"]("  what did I spend on?  ")
    assert box["job"] == {"text": "what did I spend on?", "base": history}
    assert cleared == [[]]  # stale follow-ups cleared on a new turn

    box.clear()
    defs["send_message"]("   ")
    assert box == {}


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_retry_last_trims_failed_turn_and_requeues():
    """retry_last drops the failed user+model pair and re-queues the user message."""
    history = [
        {"role": "user", "content": "Merge all my trips into one basket"},
        {"role": "model", "content": "**Rate limit reached.** Wait a moment and try again."},
    ]
    set_calls: dict = {}

    _, defs = _actions_cell().run(
        chat_history=lambda: history,
        set_chat_history=lambda new: set_calls.__setitem__("history", new),
        set_pending=lambda job: set_calls.__setitem__("pending", job),
        set_suggestions=lambda _s: None,
    )

    defs["retry_last"]()

    assert set_calls["history"] == []  # failed pair dropped
    assert set_calls["pending"] == {"text": "Merge all my trips into one basket", "base": []}


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_clear_chat_resets_history_and_pending():
    """clear_chat empties history and clears any pending job."""
    set_calls: dict = {}

    _, defs = _actions_cell().run(
        chat_history=lambda: [{"role": "user", "content": "x"}],
        set_chat_history=lambda new: set_calls.__setitem__("history", new),
        set_pending=lambda job: set_calls.__setitem__("pending", job),
        set_suggestions=lambda s: set_calls.__setitem__("suggestions", s),
    )

    defs["clear_chat"]()
    assert set_calls["history"] == []
    assert set_calls["pending"] is None
    assert set_calls["suggestions"] == []


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_llm_runner_cell_runs_pending_turn():
    """The runner cell consumes a pending job, runs the turn, appends it and clears pending."""
    base = [{"role": "user", "content": "earlier"}, {"role": "model", "content": "ok"}]
    captured: dict = {}
    set_calls: dict = {}

    def _fake_turn(system_prompt, tools, hist, user_text):
        captured["hist"] = hist
        captured["user_text"] = user_text
        return "the reply"

    _runner_cell().run(
        ai_system_prompt="system",
        ai_tools=[],
        bump_input=lambda _v: None,
        input_ver=lambda: 0,
        pending=lambda: {"text": "now", "base": base},
        run_chat_turn=_fake_turn,
        set_chat_history=lambda new: set_calls.__setitem__("history", new),
        set_pending=lambda job: set_calls.__setitem__("pending", job),
    )

    assert captured == {"hist": base, "user_text": "now"}
    assert set_calls["history"] == [
        *base,
        {"role": "user", "content": "now"},
        {"role": "model", "content": "the reply"},
    ]
    assert set_calls["pending"] is None


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_llm_runner_cell_idle_when_no_pending():
    """The runner cell does nothing when there is no pending job."""
    set_calls: dict = {}

    _runner_cell().run(
        ai_system_prompt="system",
        ai_tools=[],
        bump_input=lambda _v: None,
        input_ver=lambda: 0,
        pending=lambda: None,
        run_chat_turn=lambda *_a: pytest.fail("run_chat_turn must not be called when idle"),
        set_chat_history=lambda new: set_calls.__setitem__("history", new),
        set_pending=lambda job: set_calls.__setitem__("pending", job),
    )

    assert set_calls == {}


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_view_data_cell_no_draft():
    """View data cell returns an empty DataFrame when draft_view is None."""
    from dinary_analytics.views import empty_view_frame

    cells = list(_dash_module.app._cell_manager.cells())
    data_cell = next(c for c in cells if "view_data_df" in c.defs)

    _, defs = data_cell.run(
        draft_view=lambda: None,
        empty_view_frame=empty_view_frame,
        load_view_frame=lambda *_a, **_k: pytest.fail("load_view_frame called without draft"),
        period_date_from=lambda _p: "2025-01-01",
        period_selector=_DropdownStub("Last 12 months"),
    )

    df = defs["view_data_df"]
    assert df.is_empty()
    assert set(df.columns) == {"basket_name", "year_month", "group_name", "total_amount"}


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_view_data_cell_with_draft(ledger_db):
    """View data cell runs view_data.sql against the real test DB and returns rows."""
    from dinary_analytics.views import empty_view_frame, load_view_frame

    cells = list(_dash_module.app._cell_manager.cells())
    data_cell = next(c for c in cells if "view_data_df" in c.defs)

    draft = {
        "baskets": [{"name": "Отпуск", "triggers": {"events": [1], "tags": []}}],
        "default_basket": "Прочее",
    }

    _, defs = data_cell.run(
        draft_view=lambda: draft,
        empty_view_frame=empty_view_frame,
        load_view_frame=partial(load_view_frame, replica_path=ledger_db),
        period_date_from=lambda _p: "2020-01-01",
        period_selector=_DropdownStub("Last 12 months"),
    )

    df = defs["view_data_df"]
    assert not df.is_empty()
    basket_names = set(df["basket_name"].to_list())
    assert "Отпуск" in basket_names


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_chat_log_renders_inline_draft_chart(basket_df):
    """The log cell renders the proposed-view chart inline when a draft exists."""
    import marimo as mo

    draft = {"name": "Test View", "baskets": [], "default_basket": "Other"}
    output, _ = _log_cell().run(
        chat_history=lambda: [{"role": "model", "content": "here is a view"}],
        clear_chat=lambda: None,
        draft_view=lambda: draft,
        make_basket_chart=make_basket_chart,
        mo=mo,
        pending=lambda: None,
        retry_last=lambda: None,
        view_data_df=basket_df,
    )

    assert output is not None


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_pin_controls_cell_hidden_without_draft():
    """Pin controls cell renders nothing when there is no draft view."""
    import marimo as mo

    cells = list(_dash_module.app._cell_manager.cells())
    pin_cell = next(c for c in cells if "pin_controls" in c.defs)

    output, defs = pin_cell.run(
        bump_view_list=lambda _v: None,
        draft_view=lambda: None,
        mo=mo,
        save_view=lambda _cfg: "id",
        view_list_ver=lambda: 0,
    )

    assert defs["pin_controls"] is not None
    assert output is not None


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_pin_controls_cell_shows_for_draft():
    """Pin controls cell builds a name input + pin button when a draft view exists."""
    import marimo as mo

    cells = list(_dash_module.app._cell_manager.cells())
    pin_cell = next(c for c in cells if "pin_controls" in c.defs)

    draft = {"name": "Draft", "baskets": [], "default_basket": "Other"}
    output, defs = pin_cell.run(
        bump_view_list=lambda _v: None,
        draft_view=lambda: draft,
        mo=mo,
        save_view=lambda _cfg: "id",
        view_list_ver=lambda: 0,
    )

    assert defs["pin_controls"] is not None
    assert isinstance(output, mo.Html)


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_pinned_frames_cell_loads(ledger_db, tmp_path):
    """The pinned-frames data cell loads (id, config, frame) tuples for saved views."""
    from dinary_analytics.settings import save_view as _real_save_view
    from dinary_analytics.views import load_pinned_view_frames

    settings_db = tmp_path / "settings.db"
    _real_save_view(
        {
            "name": "Отпуск",
            "baskets": [{"name": "Отпуск", "triggers": {"events": [1], "tags": []}}],
            "default_basket": "Прочее",
        },
        db_path=settings_db,
    )

    cells = list(_dash_module.app._cell_manager.cells())
    data_cell = next(c for c in cells if "pinned_frames" in c.defs)

    _, defs = data_cell.run(
        load_pinned_view_frames=partial(
            load_pinned_view_frames, replica_path=ledger_db, db_path=settings_db
        ),
        period_date_from=lambda _p: "2020-01-01",
        period_selector=_DropdownStub("Last 12 months"),
        view_list_ver=lambda: 1,
    )

    frames = defs["pinned_frames"]
    assert len(frames) == 1
    _vid, cfg, df = frames[0]
    assert cfg["name"] == "Отпуск"
    assert "Отпуск" in set(df["basket_name"].to_list())


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_gallery_cell_empty():
    """Saved-views gallery shows a placeholder when there are no pinned frames."""
    import marimo as mo

    cells = list(_dash_module.app._cell_manager.cells())
    gallery_cell = next(c for c in cells if "saved_gallery" in c.defs)

    output, defs = gallery_cell.run(
        bump_view_list=lambda _v: None,
        delete_view=lambda _i: None,
        make_basket_chart=make_basket_chart,
        mo=mo,
        pinned_frames=[],
        set_draft_view=lambda _c: None,
        view_list_ver=lambda: 0,
    )

    assert defs["saved_gallery"] is not None
    assert output is not None


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_gallery_cell_with_saved_view(basket_df):
    """Gallery renders a card per pinned frame."""
    import marimo as mo

    cells = list(_dash_module.app._cell_manager.cells())
    gallery_cell = next(c for c in cells if "saved_gallery" in c.defs)

    frames = [("vid-1", {"name": "Travel"}, basket_df)]
    output, defs = gallery_cell.run(
        bump_view_list=lambda _v: None,
        delete_view=lambda _i: None,
        make_basket_chart=make_basket_chart,
        mo=mo,
        pinned_frames=frames,
        set_draft_view=lambda _c: None,
        view_list_ver=lambda: 1,
    )

    assert defs["saved_gallery"] is not None
    assert output is not None

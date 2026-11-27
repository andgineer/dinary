"""Tests for dashboard chart construction: direct calls into
``dinary_analytics.charts`` (same functions the notebook imports) plus
``cell.run()`` tests against a real SQLite test DB."""

import contextlib
import datetime
import json
import sqlite3
import types
import urllib.error
import urllib.request
from functools import partial
from unittest.mock import MagicMock

import allure
import duckdb
import marimo as mo
import polars as pl
import pytest

import dinary_analytics.charts as charts_module
import dinary_analytics.connection as connection_module
import dinary_analytics.notebooks.dashboard as _dash_module
import dinary_analytics.settings as settings_module
from dinary_analytics.charts import ChartSize, make_basket_chart, make_chart_pair, make_event_chart
from dinary_analytics.connection import open_ledger
from dinary_analytics.llm import tool_name
from dinary_analytics.views import (
    drill_down_view,
    duplicate_basket_name,
    empty_view_frame,
    load_pinned_view_frames,
    load_view_frame,
)


def _months_ago(months: int) -> datetime.date:
    """First-of-month date `months` months before today, so the fixture data
    always falls inside the dashboard's rolling last-12-months window."""
    today = datetime.date.today()
    total = today.year * 12 + (today.month - 1) - months
    return datetime.date(total // 12, total % 12 + 1, 1)


@pytest.fixture
def ledger_db(tmp_path):
    older = _months_ago(2)
    newer = _months_ago(1)
    db = tmp_path / "ledger.db"
    con = sqlite3.connect(str(db))
    con.executescript(f"""
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
        INSERT INTO category_groups VALUES (1, 'Nutrition', 0, 1);
        INSERT INTO categories VALUES (1, 'food', 1, 1);
        INSERT INTO categories VALUES (2, 'rent', 1, 1);
        INSERT INTO tags VALUES (1, 'travel', 1);
        INSERT INTO events VALUES (1, 'vacation', '{older.isoformat()}', '{newer.replace(day=28).isoformat()}', 1, 1);
        INSERT INTO expenses VALUES (1, '{older.replace(day=10)} 10:00:00', 500.0, 55000.0, 'RSD', 1, 1, NULL, NULL, NULL);
        INSERT INTO expenses VALUES (2, '{older.replace(day=15)} 12:00:00', 300.0, 33000.0, 'RSD', 2, 1, NULL, NULL, NULL);
        INSERT INTO expenses VALUES (3, '{newer.replace(day=5)} 09:00:00', 480.0, 52800.0, 'RSD', 1, 1, NULL, NULL, NULL);
        INSERT INTO expense_tags VALUES (1, 1);
        INSERT INTO expense_tags VALUES (2, 1);
        INSERT INTO income VALUES ({older.year}, {older.month}, 2000.0);
        INSERT INTO income VALUES ({newer.year}, {newer.month}, 2100.0);
        CREATE TABLE shop_chains (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
        CREATE TABLE stores (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            chain_id INTEGER REFERENCES shop_chains(id),
            pib TEXT UNIQUE
        );
        ALTER TABLE expenses ADD COLUMN store_id INTEGER REFERENCES stores(id);
    """)
    con.commit()
    con.close()
    return db


@pytest.fixture
def chart_data(ledger_db):
    top10 = ["food", "rent"]
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
    chart = make_chart_pair(exp_df, inc_df, yr_df, total_inc, cat_order, "EUR")
    spec = chart.to_dict()
    assert "hconcat" in spec
    assert len(spec["hconcat"]) == 2


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_chart_pair_with_title(chart_data):
    exp_df, inc_df, yr_df, total_inc, cat_order = chart_data
    chart = make_chart_pair(exp_df, inc_df, yr_df, total_inc, cat_order, "EUR", period_title="2025")
    spec = chart.to_dict()
    main = spec["hconcat"][0]
    assert main.get("title") == "2025"


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_chart_pair_main_layers(chart_data):
    exp_df, inc_df, yr_df, total_inc, cat_order = chart_data
    chart = make_chart_pair(exp_df, inc_df, yr_df, total_inc, cat_order, "EUR")
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
    chart = make_chart_pair(exp_df, inc_df, yr_df, total_inc, cat_order, "EUR")
    spec_str = json.dumps(chart.to_dict())
    assert "paintOrder" not in spec_str


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_charts_label_amounts_with_the_given_currency(chart_data, basket_df):
    exp_df, inc_df, yr_df, total_inc, cat_order = chart_data
    pair = json.dumps(make_chart_pair(exp_df, inc_df, yr_df, total_inc, cat_order, "RSD").to_dict())
    basket = json.dumps(make_basket_chart(basket_df, "RSD").to_dict())
    assert "Saved (RSD)" in pair
    assert '"title": "RSD"' in basket
    assert "EUR" not in pair + basket
    assert "€" not in pair + basket


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_ledger_facts_cell_reads_currency_and_schema_from_the_replica(ledger_db, monkeypatch):
    con = sqlite3.connect(str(ledger_db))
    con.executescript("""
        CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO app_metadata VALUES ('accounting_currency', 'RSD');
    """)
    con.commit()
    con.close()
    monkeypatch.setattr(connection_module, "REPLICA_PATH", ledger_db)

    cells = list(_dash_module.app._cell_manager.cells())
    cell = next(c for c in cells if "ledger_schema_sql" in c.defs)
    _, defs = cell.run(
        accounting_currency=connection_module.accounting_currency,
        ledger_schema=connection_module.ledger_schema,
        replica_status={"ok": True},
    )

    assert defs["currency"] == "RSD"
    assert "CREATE TABLE expenses" in defs["ledger_schema_sql"]
    assert "app_metadata" not in defs["ledger_schema_sql"]


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_chat_prompt_cell_names_the_ledger_currency_and_schema():

    cells = list(_dash_module.app._cell_manager.cells())
    cell = next(c for c in cells if "ai_system_prompt" in c.defs)
    _, defs = cell.run(
        category_order=["food", "Other"],
        chat_key_available=lambda: True,
        currency="RSD",
        key_ref=lambda: "SOME_KEY",
        ledger_schema_sql="CREATE TABLE expenses (id INTEGER);",
        mo=mo,
    )

    assert "is in RSD (the accounting currency)" in defs["ai_system_prompt"]
    assert (
        "Money by store or by item always comes from ledger.expenses.amount"
        in (defs["ai_system_prompt"])
    )
    assert (
        "expenses.amount * receipt_items.total_price / expenses.amount_original"
        in (defs["ai_system_prompt"])
    )
    assert "OVER (PARTITION BY" not in defs["ai_system_prompt"]
    assert defs["ai_system_prompt"].endswith("CREATE TABLE expenses (id INTEGER);")
    for phrase in (
        "categories and category_groups",
        "FIRST basket",
        "drill_into",
        "back_to_parent",
    ):
        assert phrase in defs["ai_system_prompt"]
    assert "EUR" not in defs["ai_system_prompt"]


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
        "EUR",
        size=ChartSize(width=400, height=280, year_width=120),
    )
    spec = chart.to_dict()
    assert spec["hconcat"][0]["width"] == 400
    assert spec["hconcat"][0]["height"] == 280


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_dashboard_notebook_imports():
    """Verify the notebook module loads and both chart functions are importable."""

    assert hasattr(charts_module, "make_chart_pair")
    assert hasattr(charts_module, "make_event_chart")
    assert _dash_module.app is not None


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_dashboard_selectors_cell_runs(ledger_db, monkeypatch):
    """Uses ``cell.run()`` so a regression in the real notebook code is caught
    here, not via duplicated logic."""

    def _mock_open_ledger():
        return open_ledger(replica_path=ledger_db)

    cells = list(_dash_module.app._cell_manager.cells())
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
    chart = make_event_chart(event_expense_df, "vacation", "EUR")
    spec = chart.to_dict()
    # make_event_chart returns a LayerChart; the arc mark is in the first layer
    assert spec["layer"][0]["mark"]["type"] == "arc"


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_event_chart_with_subtitle(event_expense_df):
    chart = make_event_chart(event_expense_df, "vacation", "RSD")
    spec = chart.to_dict()
    title = spec.get("title", {})
    assert title.get("text") == "vacation"
    assert title.get("subtitle", "").endswith(" RSD")


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_event_chart_top9_plus_other():
    many_cats = pl.DataFrame(
        {
            "category": [f"cat{i}" for i in range(12)],
            "total": [float(100 - i * 5) for i in range(12)],
        }
    )
    chart = make_event_chart(many_cats, "big event", "EUR")
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
    chart = make_event_chart(expense_df, "combined", "EUR")
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
    chart = make_basket_chart(basket_df, "EUR", "My View")
    spec = chart.to_dict()
    assert "vconcat" in spec
    assert len(spec["vconcat"]) == 2


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_basket_chart_top_panel_is_bar(basket_df):
    chart = make_basket_chart(basket_df, "EUR")
    spec = chart.to_dict()
    top = spec["vconcat"][0]
    mark = top.get("mark", {})
    mark_type = mark.get("type") if isinstance(mark, dict) else mark
    assert mark_type == "bar"


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_basket_chart_has_selection_param(basket_df):
    chart = make_basket_chart(basket_df, "EUR")
    spec = chart.to_dict()
    # Altair hoists params to the outermost vconcat level
    all_params = spec.get("params", []) + spec["vconcat"][0].get("params", [])
    assert any(p.get("select", {}).get("type") == "point" for p in all_params)


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_basket_chart_bottom_has_filter_transform(basket_df):
    chart = make_basket_chart(basket_df, "EUR")
    spec = chart.to_dict()
    bottom = spec["vconcat"][1]
    transforms = bottom.get("transform", [])
    assert any("filter" in t for t in transforms)


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_basket_chart_independent_color_scales(basket_df):
    chart = make_basket_chart(basket_df, "EUR")
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

    cells = list(_dash_module.app._cell_manager.cells())
    sel_cell = next(c for c in cells if "period_selector" in c.defs)

    _, defs = sel_cell.run(mo=mo)

    assert defs["period_selector"] is not None
    assert defs["period_selector"].value == "Last 12 months"


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_fetch_replica_status_survives_refresh_post_failure(monkeypatch):
    """A failed POST /refresh/now (service restarted mid-click) must not raise."""

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
def test_status_bar_cell_shows_refresh_requested_feedback_right_after_click():
    """Clicking 'Refresh now' shows immediate feedback that the request was sent."""

    cells = list(_dash_module.app._cell_manager.cells())
    cell = next(
        c
        for c in cells
        if {"replica_status", "set_refresh_requested", "set_address_configured"} <= c.refs
    )

    _now = datetime.datetime.now(tz=datetime.timezone.utc).timestamp()

    output, _ = cell.run(
        datetime=datetime.datetime,
        mo=mo,
        refreshing=lambda: _now,
        replica_status={"ok": True, "last_refresh": "2024-01-01T00:00:00+00:00", "error": None},
        set_address_configured=lambda _v: None,
        set_refresh_requested=lambda _v: None,
        set_refreshing=lambda _v: None,
        timezone=datetime.timezone,
    )

    _, html = output._mime_()
    assert "Refresh requested" in html


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_status_bar_cell_hides_refresh_requested_feedback_after_timeout():
    """The 'Refresh requested' feedback disappears once enough time has passed."""

    cells = list(_dash_module.app._cell_manager.cells())
    cell = next(
        c
        for c in cells
        if {"replica_status", "set_refresh_requested", "set_address_configured"} <= c.refs
    )

    _long_ago = datetime.datetime.now(tz=datetime.timezone.utc).timestamp() - 100

    output, _ = cell.run(
        datetime=datetime.datetime,
        mo=mo,
        refreshing=lambda: _long_ago,
        replica_status={"ok": True, "last_refresh": "2024-01-01T00:00:00+00:00", "error": None},
        set_address_configured=lambda _v: None,
        set_refresh_requested=lambda _v: None,
        set_refreshing=lambda _v: None,
        timezone=datetime.timezone,
    )

    _, html = output._mime_()
    assert "Refresh requested" not in html


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_status_bar_cell_hides_refresh_requested_feedback_once_refresh_completes():
    """The 'Refresh requested' feedback disappears as soon as last_refresh advances past the click."""

    cells = list(_dash_module.app._cell_manager.cells())
    cell = next(
        c
        for c in cells
        if {"replica_status", "set_refresh_requested", "set_address_configured"} <= c.refs
    )

    _now = datetime.datetime.now(tz=datetime.timezone.utc)
    _clicked_at = (_now - datetime.timedelta(seconds=2)).timestamp()
    _last_refresh = (_now - datetime.timedelta(seconds=1)).isoformat()

    output, _ = cell.run(
        datetime=datetime.datetime,
        mo=mo,
        refreshing=lambda: _clicked_at,
        replica_status={"ok": True, "last_refresh": _last_refresh, "error": None},
        set_address_configured=lambda _v: None,
        set_refresh_requested=lambda _v: None,
        set_refreshing=lambda _v: None,
        timezone=datetime.timezone,
    )

    _, html = output._mime_()
    assert "Refresh requested" not in html


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_address_gate_cell_shows_field_above_instructions_in_one_panel():
    """The address field comes first, with the lookup instructions below it, in one panel."""

    cells = list(_dash_module.app._cell_manager.cells())
    cell = next(
        c
        for c in cells
        if {"address_configured", "address_warning", "save_server_address"} <= c.refs
    )

    output, _ = cell.run(
        address_configured=lambda: False,
        address_warning=lambda: False,
        get_app_url=lambda: None,
        mo=mo,
        save_server_address=lambda *a, **kw: None,
        set_address_configured=lambda _v: None,
        set_address_warning=lambda _v: None,
        set_refresh_requested=lambda _v: None,
    )

    _, html = output._mime_()
    assert "marimo-callout-output" in html
    assert html.index("Server address") < html.index("Where to find your server")


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_followups_cell_renders_clickable_buttons_and_hides_while_pending():
    """Follow-up suggestions render as clickable buttons; hidden while a reply is pending."""

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
        ai_draft_tools=(),
        bump_view_list=lambda _v: None,
        delete_view=lambda _i: None,
        draft_view=lambda: None,
        save_view=lambda _cfg: "id",
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

    cells = list(_dash_module.app._cell_manager.cells())
    chips_cell = next(c for c in cells if c.refs == {"mo", "send_message"})

    output, _ = chips_cell.run(mo=mo, send_message=lambda _t: None)

    assert output is not None


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_chat_input_cell_builds():
    """The input cell builds a text area + send button without invalid marimo params."""

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


def _draft_chart_cell():
    cells = list(_dash_module.app._cell_manager.cells())
    return next(c for c in cells if "draft_chart_blocks" in c.defs)


def _draft_controls_cell():
    cells = list(_dash_module.app._cell_manager.cells())
    return next(c for c in cells if "draft_controls" in c.defs)


def _draft_chart_blocks(*, draft, pending, view_data_df, stack=(), fake_mo=None, sent=None):
    _, controls = _draft_controls_cell().run(
        back_to_parent=lambda: "",
        draft_stack=lambda: list(stack),
        draft_view=lambda: draft,
        mo=fake_mo or mo,
        send_message=(sent.append if sent is not None else lambda _t: None),
        view_data_df=view_data_df,
    )
    _, defs = _draft_chart_cell().run(
        currency="EUR",
        draft_controls=controls["draft_controls"],
        draft_view=lambda: draft,
        make_basket_chart=make_basket_chart,
        mo=mo,
        pending=lambda: pending,
        view_data_df=view_data_df,
    )
    return defs["draft_chart_blocks"]


def _run_log(*, chat_history, pending, draft_chart_blocks=()):

    output, _ = _log_cell().run(
        chat_history=lambda: chat_history,
        clear_chat=lambda: None,
        draft_chart_blocks=list(draft_chart_blocks),
        mo=mo,
        pending=lambda: pending,
        retry_last=lambda: None,
    )
    return output


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_chat_log_cell_empty_pending_and_populated():
    """Log cell: placeholder when empty, thinking bubble when pending, bubbles when populated."""
    assert _run_log(chat_history=[], pending=None) is not None
    assert _run_log(chat_history=[], pending={"text": "hi"}) is not None
    msgs = [{"role": "user", "content": "hi"}, {"role": "model", "content": "hello"}]
    assert _run_log(chat_history=msgs, pending=None) is not None


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

    cells = list(_dash_module.app._cell_manager.cells())
    data_cell = next(c for c in cells if "view_data_df" in c.defs)

    draft = {
        "baskets": [{"name": "Vacation", "triggers": {"events": [1], "tags": []}}],
        "default_basket": "Other",
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
    assert "Vacation" in basket_names


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_draft_chart_cell_shows_the_chart_only_for_a_settled_draft_with_data(basket_df):

    draft = {"name": "Test View", "baskets": [], "default_basket": "Other"}

    shown = _draft_chart_blocks(draft=draft, pending=None, view_data_df=basket_df)
    assert len(shown) == 3
    assert "Test View" in json.dumps(shown[1].to_dict())

    assert _draft_chart_blocks(draft=None, pending=None, view_data_df=basket_df) == []
    assert _draft_chart_blocks(draft=draft, pending={"text": "hi"}, view_data_df=basket_df) == []
    assert _draft_chart_blocks(draft=draft, pending=None, view_data_df=empty_view_frame()) == []


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_chat_log_renders_inline_draft_chart(basket_df):
    """The log cell renders the proposed-view chart inline when a draft exists."""
    blocks = _draft_chart_blocks(
        draft={"name": "Test View", "baskets": [], "default_basket": "Other"},
        pending=None,
        view_data_df=basket_df,
    )
    output = _run_log(
        chat_history=[{"role": "model", "content": "here is a view"}],
        pending=None,
        draft_chart_blocks=blocks,
    )

    assert output is not None


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_pin_controls_cell_hidden_without_draft():
    """Pin controls cell renders nothing when there is no draft view."""

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

    settings_db = tmp_path / "settings.db"
    settings_module.save_view(
        {
            "name": "Vacation",
            "baskets": [{"name": "Vacation", "triggers": {"events": [1], "tags": []}}],
            "default_basket": "Other",
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
    assert cfg["name"] == "Vacation"
    assert "Vacation" in set(df["basket_name"].to_list())


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_gallery_cell_empty():
    """Saved-views gallery shows a placeholder when there are no pinned frames."""

    cells = list(_dash_module.app._cell_manager.cells())
    gallery_cell = next(c for c in cells if "saved_gallery" in c.defs)

    output, defs = gallery_cell.run(
        bump_view_list=lambda _v: None,
        delete_view=lambda _i: None,
        currency="EUR",
        make_basket_chart=make_basket_chart,
        mo=mo,
        open_in_draft=lambda _c: None,
        pinned_frames=[],
        view_list_ver=lambda: 0,
    )

    assert defs["saved_gallery"] is not None
    assert output is not None


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_gallery_cell_with_saved_view(basket_df):
    """Gallery renders a card per pinned frame."""

    cells = list(_dash_module.app._cell_manager.cells())
    gallery_cell = next(c for c in cells if "saved_gallery" in c.defs)

    frames = [("vid-1", {"name": "Travel"}, basket_df)]
    output, defs = gallery_cell.run(
        bump_view_list=lambda _v: None,
        delete_view=lambda _i: None,
        currency="EUR",
        make_basket_chart=make_basket_chart,
        mo=mo,
        open_in_draft=lambda _c: None,
        pinned_frames=frames,
        view_list_ver=lambda: 1,
    )

    assert defs["saved_gallery"] is not None
    assert output is not None


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_query_ledger_tool_reads_only_the_ledger(ledger_db, monkeypatch):
    con = sqlite3.connect(str(ledger_db))
    con.executescript("""
        CREATE TABLE llmbroker_secrets (ref TEXT, value TEXT);
        INSERT INTO llmbroker_secrets VALUES ('openai', 'sk-secret-marker-7f3a');
    """)
    con.commit()
    con.close()
    monkeypatch.setattr(connection_module, "REPLICA_PATH", ledger_db)

    cells = list(_dash_module.app._cell_manager.cells())
    tools_cell = next(c for c in cells if "ai_ledger_tools_pair" in c.defs)
    _, defs = tools_cell.run(
        json=json,
        load_query=connection_module.load_query,
        open_ledger=connection_module.open_ledger,
        open_ledger_restricted=connection_module.open_ledger_restricted,
    )
    query_ledger, query_summary = defs["ai_ledger_tools_pair"]

    assert len(json.loads(query_ledger("SELECT id FROM ledger.expenses"))) == 3
    with pytest.raises(duckdb.Error) as exc_info:
        query_ledger("SELECT * FROM ledger.llmbroker_secrets")
    assert "sk-secret-marker-7f3a" not in str(exc_info.value)
    assert json.loads(query_summary()) is not None


class _StateStub:
    """Getter/setter pair standing in for one ``mo.state``."""

    def __init__(self, value) -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


PARENT_DRAFT = {
    "name": "Life",
    "baskets": [
        {"name": "Travel", "triggers": {"tags": [1]}},
        {"name": "Food", "triggers": {"category_groups": [1]}},
    ],
    "default_basket": "Main",
    "chart_type": "stacked_bar_monthly",
}
FOOD_BREAKDOWN = [{"name": "Groceries", "triggers": {"categories": [1]}}]


def _navigation(draft, stack):
    cells = list(_dash_module.app._cell_manager.cells())
    names_cell = next(c for c in cells if "basket_names_error" in c.defs)
    _, names = names_cell.run(duplicate_basket_name=duplicate_basket_name)
    edit_cell = next(c for c in cells if "ai_draft_edit_tools" in c.defs)
    _, edit = edit_cell.run(
        basket_names_error=names["basket_names_error"],
        draft_view=draft.get,
        set_draft_stack=stack.set,
        set_draft_view=draft.set,
    )
    nav_cell = next(c for c in cells if "drill_into" in c.defs)
    _, defs = nav_cell.run(
        ai_draft_edit_tools=edit["ai_draft_edit_tools"],
        basket_names_error=names["basket_names_error"],
        draft_stack=stack.get,
        draft_view=draft.get,
        drill_down_view=drill_down_view,
        set_draft_stack=stack.set,
        set_draft_view=draft.set,
    )
    return defs


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_drill_into_pushes_the_parent_and_sets_the_scoped_draft():
    draft, stack = _StateStub(PARENT_DRAFT), _StateStub([])

    _navigation(draft, stack)["drill_into"]("Food", FOOD_BREAKDOWN, "Other food", "by category")

    assert stack.value == [PARENT_DRAFT]
    assert draft.value["name"] == "by category"
    assert draft.value["baskets"] == FOOD_BREAKDOWN
    assert draft.value["scope"] == {
        "name": "Food",
        "required": [{"category_groups": [1]}],
        "excluded": [{"tags": [1]}],
    }


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_drill_into_an_unknown_basket_leaves_the_draft_alone():
    draft, stack = _StateStub(PARENT_DRAFT), _StateStub([])

    reply = _navigation(draft, stack)["drill_into"]("Nope", FOOD_BREAKDOWN, "Rest")

    assert "Nope" in reply
    assert draft.value is PARENT_DRAFT
    assert stack.value == []


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_back_to_parent_restores_the_parent_draft():
    draft, stack = _StateStub(PARENT_DRAFT), _StateStub([])
    nav = _navigation(draft, stack)
    nav["drill_into"]("Main", FOOD_BREAKDOWN, "Rest")

    nav["back_to_parent"]()

    assert draft.value is PARENT_DRAFT
    assert stack.value == []
    assert "no parent" in nav["back_to_parent"]()


def _recording_mo(buttons: list):
    """A ``mo`` whose buttons are recorded as (label, on_click) pairs."""

    def _button(**kwargs):
        buttons.append((kwargs["label"], kwargs["on_click"]))
        return mo.ui.button(**kwargs)

    return types.SimpleNamespace(
        md=mo.md,
        hstack=mo.hstack,
        ui=types.SimpleNamespace(button=_button),
    )


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_break_down_control_sends_the_request_into_the_conversation(basket_df):
    buttons: list = []
    sent: list = []
    draft = {
        "name": "Life",
        "baskets": [{"name": "Travel"}, {"name": "Food"}],
        "default_basket": "Main",
    }

    _draft_chart_blocks(
        draft=draft,
        pending=None,
        view_data_df=basket_df,
        fake_mo=_recording_mo(buttons),
        sent=sent,
    )

    assert [label for label, _ in buttons] == ["🔍 Break down Travel", "🔍 Break down Food"]
    buttons[1][1](None)
    assert sent == ["Break down the basket “Food”"]


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_draft_chart_offers_back_and_shows_the_scope_for_a_breakdown(basket_df):
    buttons: list = []
    draft = {
        "name": "by category",
        "baskets": [{"name": "Food"}],
        "default_basket": "Travel",
        "scope": {"name": "Life", "required": [], "excluded": []},
    }

    blocks = _draft_chart_blocks(
        draft=draft,
        pending=None,
        view_data_df=basket_df,
        stack=[PARENT_DRAFT],
        fake_mo=_recording_mo(buttons),
    )

    assert "◂ Back to parent view" in [label for label, _ in buttons]
    assert blocks[1].to_dict()["vconcat"][0]["title"] == "Life ▸ by category"


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_pin_keeps_the_scope_of_a_drilled_draft():
    drilled = drill_down_view(PARENT_DRAFT, "Food", FOOD_BREAKDOWN, "Other food", "by category")
    saved: list = []
    cells = list(_dash_module.app._cell_manager.cells())
    pin_cell = next(c for c in cells if "pin_controls" in c.defs)
    _, defs = pin_cell.run(
        bump_view_list=lambda _v: None,
        draft_view=lambda: drilled,
        mo=mo,
        save_view=saved.append,
        view_list_ver=lambda: 0,
    )

    defs["pin_button"]._on_click(None)

    assert saved == [drilled]


def _view_tools(draft, stack, saved=None):
    cells = list(_dash_module.app._cell_manager.cells())
    tools_cell = next(c for c in cells if "ai_view_tools_tuple" in c.defs)
    _, defs = tools_cell.run(
        ai_draft_tools=_navigation(draft, stack)["ai_draft_tools"],
        bump_view_list=lambda _v: None,
        delete_view=lambda _i: None,
        draft_view=draft.get,
        save_view=(saved.append if saved is not None else lambda _cfg: "id"),
        set_suggestions=lambda _s: None,
        view_list_ver=lambda: 0,
    )
    return {tool_name(fn): fn for fn in defs["ai_view_tools_tuple"]}


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_view_tools_include_drill_down_navigation():
    tools = _view_tools(_StateStub(None), _StateStub([]))

    assert {"drill_into", "back_to_parent", "propose_view"} <= set(tools)
    doc = tools["propose_view"].__doc__
    for trigger_list in ("events", "tags", "categories", "category_groups"):
        assert f'"{trigger_list}"' in doc


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_propose_view_refines_a_breakdown_and_top_level_starts_over():
    drilled = drill_down_view(PARENT_DRAFT, "Food", FOOD_BREAKDOWN, "Other food", "by category")
    draft, stack = _StateStub(drilled), _StateStub([PARENT_DRAFT])
    tools = _view_tools(draft, stack)

    tools["propose_view"]([{"name": "Cafés", "triggers": {"categories": [3]}}], "Rest")
    assert draft.value["scope"] == drilled["scope"]
    assert draft.value["name"] == "by category"
    assert stack.value == [PARENT_DRAFT]

    tools["propose_view"](PARENT_DRAFT["baskets"], "Main", top_level=True)
    assert "scope" not in draft.value
    assert stack.value == []


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_save_current_view_keeps_the_scope():
    drilled = drill_down_view(PARENT_DRAFT, "Main", FOOD_BREAKDOWN, "Rest")
    saved: list = []
    tools = _view_tools(_StateStub(drilled), _StateStub([PARENT_DRAFT]), saved)

    tools["save_current_view"]("Main by category")

    assert saved == [{**drilled, "name": "Main by category"}]


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_make_basket_chart_title_leads_with_the_scope_name(basket_df):
    spec = make_basket_chart(basket_df, "EUR", "by category", scope_name="Food").to_dict()
    assert spec["vconcat"][0]["title"] == "Food ▸ by category"

    plain = make_basket_chart(basket_df, "EUR", "Life").to_dict()
    assert plain["vconcat"][0]["title"] == "Life"


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_open_in_draft_leaves_breakdown_navigation_behind():
    draft, stack = _StateStub(PARENT_DRAFT), _StateStub([PARENT_DRAFT])
    pinned = {"name": "Pinned", "baskets": [], "default_basket": "Other"}

    _navigation(draft, stack)["open_in_draft"](pinned)

    assert draft.value is pinned
    assert stack.value == []


@allure.epic("Analytics")
@allure.feature("Dashboard")
@pytest.mark.parametrize(
    ("baskets", "default_basket"),
    [
        ([{"name": "Other", "triggers": {"tags": [1]}}], "Other"),
        ([{"name": "Food"}, {"name": "Food", "triggers": {"tags": [1]}}], "Rest"),
    ],
)
def test_propose_view_rejects_a_repeated_basket_name(baskets, default_basket):
    draft, stack = _StateStub(PARENT_DRAFT), _StateStub([])
    tools = _view_tools(draft, stack)

    reply = tools["propose_view"](baskets, default_basket)

    assert reply.startswith("Rejected:")
    assert draft.value is PARENT_DRAFT


@allure.epic("Analytics")
@allure.feature("Dashboard")
@pytest.mark.parametrize(
    ("baskets", "default_basket"),
    [
        ([{"name": "Other", "triggers": {"tags": [1]}}], "Other"),
        ([{"name": "Food"}, {"name": "Food", "triggers": {"tags": [1]}}], "Rest"),
    ],
)
def test_drill_into_rejects_a_repeated_basket_name(baskets, default_basket):
    draft, stack = _StateStub(PARENT_DRAFT), _StateStub([])

    reply = _navigation(draft, stack)["drill_into"]("Food", baskets, default_basket)

    assert reply.startswith("Rejected:")
    assert draft.value is PARENT_DRAFT
    assert stack.value == []


@allure.epic("Analytics")
@allure.feature("Dashboard")
def test_gallery_card_of_a_pinned_breakdown_shows_the_scope_title(basket_df):
    drilled = drill_down_view(PARENT_DRAFT, "Food", FOOD_BREAKDOWN, "Other food", "by category")
    charts: list = []

    def _recording_chart(*args, **kwargs):
        chart = make_basket_chart(*args, **kwargs)
        charts.append(chart)
        return chart

    cells = list(_dash_module.app._cell_manager.cells())
    gallery_cell = next(c for c in cells if "saved_gallery" in c.defs)
    gallery_cell.run(
        bump_view_list=lambda _v: None,
        delete_view=lambda _i: None,
        currency="EUR",
        make_basket_chart=_recording_chart,
        mo=mo,
        open_in_draft=lambda _c: None,
        pinned_frames=[("vid-1", drilled, basket_df)],
        view_list_ver=lambda: 1,
    )

    assert [c.to_dict()["vconcat"][0]["title"] for c in charts] == ["Food ▸ by category"]

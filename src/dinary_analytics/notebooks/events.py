import marimo

__generated_with = "0.10.0"
app = marimo.App(width="wide", app_title="Events")


@app.cell
def _():
    import marimo as mo
    import polars as pl

    from dinary_analytics.charts import make_event_chart
    from dinary_analytics.connection import open_ledger

    return make_event_chart, mo, open_ledger, pl


@app.cell
def _(mo, open_ledger):
    _con = open_ledger()
    try:
        _rows = _con.execute("""
            SELECT
                ev.id,
                ev.name,
                STRFTIME(ev.date_from::DATE, '%Y-%m-%d') AS date_from,
                STRFTIME(ev.date_to::DATE, '%Y-%m-%d') AS date_to,
                CAST(COALESCE(SUM(e.amount), 0) AS DOUBLE) AS total
            FROM ledger.events ev
            LEFT JOIN ledger.expenses e ON e.event_id = ev.id
            GROUP BY ev.id, ev.name, ev.date_from, ev.date_to
            ORDER BY ev.date_to DESC
        """).fetchall()
    finally:
        _con.close()

    _event_opts = {f"{r[1]} ({r[2][:7]})": str(r[0]) for r in _rows}
    _event_totals = {str(r[0]): float(r[4]) for r in _rows}
    _event_dates = {str(r[0]): (r[2], r[3]) for r in _rows}
    _event_names = {str(r[0]): r[1] for r in _rows}

    event_selector = mo.ui.dropdown(
        options=_event_opts,
        value=next(iter(_event_opts), None),
        label="Event:",
    )
    event_selector
    return _event_dates, _event_names, _event_totals, event_selector


@app.cell
def _(
    _event_dates,
    _event_names,
    _event_totals,
    event_selector,
    make_event_chart,
    mo,
    open_ledger,
    pl,
):
    _eid = event_selector.value
    if not _eid:
        mo.md("*No events found.*")
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
        _ev_name = _event_names.get(_eid, _eid)
        _ev_total = _event_totals.get(_eid, 0.0)
        _date_from, _date_to = _event_dates.get(_eid, ("", ""))

        if _expense_df.is_empty():
            mo.md(f"*No expenses for **{_ev_name}**.*")
        else:
            mo.vstack(
                [
                    mo.callout(
                        mo.md(
                            f"**{_ev_name}** — {_date_from} to {_date_to} — "
                            f"total **€{_ev_total:,.0f}**",
                        ),
                        kind="info",
                    ),
                    make_event_chart(_expense_df, _ev_name),
                ],
            )


if __name__ == "__main__":
    app.run()

import marimo

__generated_with = "0.10.0"
app = marimo.App(width="wide", app_title="Tags")


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
        _year_rows = _con.execute("""
            SELECT DISTINCT EXTRACT(YEAR FROM datetime::TIMESTAMP)::INT AS yr
            FROM ledger.expenses
            ORDER BY yr DESC
        """).fetchall()
        _tag_rows = _con.execute("""
            SELECT id, name FROM ledger.tags
            WHERE is_active = TRUE
            ORDER BY name
        """).fetchall()
    finally:
        _con.close()

    _all_years = [str(r[0]) for r in _year_rows]
    _tag_opts = {r[1]: str(r[0]) for r in _tag_rows}

    year_selector = mo.ui.dropdown(
        options=_all_years,
        value=_all_years[0] if _all_years else None,
        label="Year:",
    )
    tag_multiselect = mo.ui.multiselect(
        options=_tag_opts,
        label="Tags:",
    )
    mo.hstack([year_selector, tag_multiselect], justify="start")
    return tag_multiselect, year_selector


@app.cell
def _(make_event_chart, mo, open_ledger, pl, tag_multiselect, year_selector):
    _yr = year_selector.value
    _tids = tag_multiselect.value  # list of ID strings

    if not _tids or not _yr:
        mo.md("*Select at least one tag and a year above.*")
    else:
        _charts = []
        for _tid in _tids:
            _con = open_ledger()
            try:
                _rows = _con.execute(
                    """
                    SELECT c.name AS category, CAST(SUM(e.amount) AS DOUBLE) AS total
                    FROM ledger.expenses e
                    JOIN ledger.categories c ON e.category_id = c.id
                    JOIN ledger.expense_tags et ON et.expense_id = e.id
                    JOIN ledger.tags t ON t.id = et.tag_id
                    WHERE et.tag_id = $1
                      AND EXTRACT(YEAR FROM e.datetime::TIMESTAMP)::INT = $2
                    GROUP BY c.name
                    ORDER BY total DESC
                    """,
                    [int(_tid), int(_yr)],
                ).fetchall()
                _tag_name_row = _con.execute(
                    "SELECT name FROM ledger.tags WHERE id = $1",
                    [int(_tid)],
                ).fetchone()
            finally:
                _con.close()

            _tag_name = _tag_name_row[0] if _tag_name_row else _tid
            _expense_df = pl.DataFrame(
                {"category": [r[0] for r in _rows], "total": [float(r[1]) for r in _rows]},
            )
            if not _expense_df.is_empty():
                _charts.append(make_event_chart(_expense_df, f"{_tag_name} {_yr}"))

        if _charts:
            mo.hstack(_charts, justify="start")
        else:
            mo.md(f"*No expenses found for selected tags in {_yr}.*")


if __name__ == "__main__":
    app.run()

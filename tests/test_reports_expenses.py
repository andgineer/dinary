"""Tests for ``dinary.reports.expenses`` — aggregated expense viewer."""

import argparse
import io
from decimal import Decimal

import allure
import pytest

from dinary.reports import expenses as expenses_report
from dinary.services import duckdb_repo


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "DB_PATH", tmp_path / "dinary.duckdb")


def _seed_catalog(con) -> None:
    """Seed the minimum catalog + three expenses across two months.

    Two of the expenses share the same 3D coord (``еда``, no event,
    tags ``собака``) so the aggregation test can verify both the
    grouping and the total sum.
    """
    con.execute(
        "INSERT INTO category_groups (id, name, sort_order, is_active)"
        " VALUES (1, 'group', 1, TRUE)",
    )
    con.execute(
        "INSERT INTO categories (id, name, group_id, is_active)"
        " VALUES (1, 'еда', 1, TRUE), (2, 'гаджеты', 1, TRUE)",
    )
    con.execute("INSERT INTO tags (id, name, is_active) VALUES (1, 'собака', TRUE)")
    con.execute(
        "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled)"
        " VALUES (1, 'отпуск-2026', '2026-06-01', '2026-06-30', TRUE)",
    )
    con.execute(
        "INSERT INTO expenses (id, client_expense_id, datetime, amount,"
        " amount_original, currency_original, category_id, event_id, comment)"
        " VALUES (1, 'c1', '2026-06-10 12:00', 100.00, 100.00, 'RSD', 1, NULL, 'a')",
    )
    con.execute(
        "INSERT INTO expenses (id, client_expense_id, datetime, amount,"
        " amount_original, currency_original, category_id, event_id, comment)"
        " VALUES (2, 'c2', '2026-06-15 12:00', 250.00, 250.00, 'RSD', 1, NULL, 'b')",
    )
    con.execute("INSERT INTO expense_tags (expense_id, tag_id) VALUES (1, 1), (2, 1)")
    con.execute(
        "INSERT INTO expenses (id, client_expense_id, datetime, amount,"
        " amount_original, currency_original, category_id, event_id, comment)"
        " VALUES (3, 'c3', '2025-12-20 12:00', 500.00, 500.00, 'RSD', 2, 1, 'c')",
    )


@pytest.fixture
def _seeded_con():
    duckdb_repo.init_db()
    con = duckdb_repo.get_connection()
    try:
        _seed_catalog(con)
        yield con
    finally:
        con.close()


@allure.epic("Reports")
@allure.feature("expenses — parse_month")
class TestParseMonth:
    def test_accepts_yyyy_mm(self):
        assert expenses_report.parse_month("2026-06") == (2026, 6)

    def test_rejects_non_dashed(self):
        with pytest.raises(argparse.ArgumentTypeError):
            expenses_report.parse_month("202606")

    def test_rejects_non_numeric(self):
        with pytest.raises(argparse.ArgumentTypeError):
            expenses_report.parse_month("abcd-ef")

    def test_rejects_out_of_range_month(self):
        with pytest.raises(argparse.ArgumentTypeError):
            expenses_report.parse_month("2026-13")


@allure.epic("Reports")
@allure.feature("expenses — _build_filter")
class TestBuildFilter:
    """Pin the SQL + params produced by each flag combination.

    The CLI guarantees mutex via ``argparse``'s mutually-exclusive
    group, so this helper never sees both year and month set at
    once. Here we exercise the three permitted states only.
    """

    def test_no_filter(self):
        where, params = expenses_report._build_filter(year=None, month=None)
        assert where == ""
        assert params == []

    def test_year_only(self):
        where, params = expenses_report._build_filter(year=2026, month=None)
        assert "EXTRACT(YEAR FROM e.datetime) = ?" in where
        assert "MONTH" not in where
        assert params == [2026]

    def test_month_and_year(self):
        where, params = expenses_report._build_filter(year=None, month=(2026, 6))
        assert "EXTRACT(YEAR FROM e.datetime) = ?" in where
        assert "EXTRACT(MONTH FROM e.datetime) = ?" in where
        assert params == [2026, 6]


@allure.epic("Reports")
@allure.feature("expenses — aggregate_expenses")
class TestAggregate:
    def test_no_filter_returns_all_coords(self, _seeded_con):
        rows = expenses_report.aggregate_expenses(_seeded_con)
        # Two distinct 3D coords: (еда, '', собака) with 2 rows,
        # (гаджеты, отпуск-2026, '') with 1 row.
        assert len(rows) == 2
        by_coord = {(r.category, r.event, r.tags): r for r in rows}
        assert by_coord[("еда", "", "собака")].rows == 2
        assert by_coord[("еда", "", "собака")].total == Decimal("350.00")
        assert by_coord[("гаджеты", "отпуск-2026", "")].rows == 1
        assert by_coord[("гаджеты", "отпуск-2026", "")].total == Decimal("500.00")

    def test_orders_by_total_desc(self, _seeded_con):
        rows = expenses_report.aggregate_expenses(_seeded_con)
        assert rows[0].total >= rows[1].total
        assert rows[0].category == "гаджеты"

    def test_year_filter(self, _seeded_con):
        rows = expenses_report.aggregate_expenses(_seeded_con, year=2025)
        assert len(rows) == 1
        assert rows[0].category == "гаджеты"
        assert rows[0].total == Decimal("500.00")

    def test_month_filter(self, _seeded_con):
        rows = expenses_report.aggregate_expenses(_seeded_con, month=(2026, 6))
        assert len(rows) == 1
        assert rows[0].category == "еда"
        assert rows[0].rows == 2

    def test_month_filter_empty_window(self, _seeded_con):
        rows = expenses_report.aggregate_expenses(_seeded_con, month=(2026, 1))
        assert rows == []


@allure.epic("Reports")
@allure.feature("expenses — render_csv")
class TestRenderCsv:
    def test_header_and_row_shape(self, _seeded_con):
        rows = expenses_report.aggregate_expenses(_seeded_con)
        buf = io.StringIO()
        expenses_report.render_csv(rows, stream=buf)
        lines = buf.getvalue().splitlines()
        assert lines[0] == "category,event,tags,rows,total"
        # Top row ought to be гаджеты (500) per the sort contract;
        # Russian text is wrapped when it contains no commas/quotes,
        # so the literal row is predictable.
        assert lines[1].split(",") == ["гаджеты", "отпуск-2026", "", "1", "500.00"]

    def test_empty_input_writes_only_header(self):
        buf = io.StringIO()
        expenses_report.render_csv([], stream=buf)
        assert buf.getvalue().strip() == "category,event,tags,rows,total"


@allure.epic("Reports")
@allure.feature("expenses — render_rich")
class TestRenderRich:
    """Smoke tests for the rich renderer.

    We don't parse the ANSI box-drawing output — rich's exact
    layout is considered a black-box contract — but we do pin that
    the renderer completes, prints the currency + every category
    name, and reaches the ``TOTAL`` footer when rows are present.
    ``Console(file=...)`` goes through rich's own width detection;
    here we pass a ``StringIO`` which rich treats as a non-terminal
    and falls back to monochrome ASCII.
    """

    def test_renders_rows_and_total(self, _seeded_con):
        rows = expenses_report.aggregate_expenses(_seeded_con)
        buf = io.StringIO()
        expenses_report.render_rich(
            rows,
            currency="RSD",
            title_suffix="all time",
            stream=buf,
        )
        out = buf.getvalue()
        assert "еда" in out
        assert "гаджеты" in out
        assert "RSD" in out
        assert "TOTAL" in out

    def test_renders_empty_placeholder(self):
        buf = io.StringIO()
        expenses_report.render_rich(
            [],
            currency="RSD",
            title_suffix="2099",
            stream=buf,
        )
        assert "no matching expenses" in buf.getvalue()


@allure.epic("Reports")
@allure.feature("expenses — run()")
class TestRun:
    def test_missing_db_returns_nonzero(self, tmp_path, monkeypatch, capsys):
        # Point DB_PATH at a path that does NOT exist. run() must
        # refuse to silently init an empty DB — the CLI already
        # offers --remote for the "no local snapshot" case.
        monkeypatch.setattr(duckdb_repo, "DB_PATH", tmp_path / "absent.duckdb")
        buf = io.StringIO()
        rc = expenses_report.run(year=None, month=None, as_csv=False, stream=buf)
        captured = capsys.readouterr()
        assert rc == 1
        assert "DB not found" in captured.err

    def test_csv_mode_end_to_end(self, _seeded_con):
        buf = io.StringIO()
        rc = expenses_report.run(year=2025, month=None, as_csv=True, stream=buf)
        assert rc == 0
        lines = buf.getvalue().splitlines()
        assert lines[0] == "category,event,tags,rows,total"
        assert len(lines) == 2  # header + one matching coord


@allure.epic("Reports")
@allure.feature("expenses — main() CLI")
class TestMainCli:
    def test_year_and_month_are_mutex(self, _seeded_con):
        with pytest.raises(SystemExit):
            expenses_report.main(["--year", "2026", "--month", "2026-06"])

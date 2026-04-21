"""Tests for ``dinary.reports.income`` — aggregated income viewer."""

import io
import json
from decimal import Decimal

import allure
import pytest

from dinary.reports import income as income_report
from dinary.services import duckdb_repo


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "DB_PATH", tmp_path / "dinary.duckdb")


def _seed_income(con) -> None:
    """Seed two years: 2024 has 12 full months, 2025 has only 3.

    2025's partial-year layout exercises the ``avg_month = total /
    months-with-data`` contract — with a 12-month divisor the
    average would halve, and the fixture would no longer catch
    future drift.
    """
    for month in range(1, 13):
        con.execute(
            "INSERT INTO income (year, month, amount) VALUES (?, ?, ?)",
            [2024, month, Decimal("100.00")],
        )
    for month in (1, 2, 3):
        con.execute(
            "INSERT INTO income (year, month, amount) VALUES (?, ?, ?)",
            [2025, month, Decimal("600.00")],
        )


@pytest.fixture
def _seeded_con():
    duckdb_repo.init_db()
    con = duckdb_repo.get_connection()
    try:
        _seed_income(con)
        yield con
    finally:
        con.close()


@allure.epic("Reports")
@allure.feature("income — aggregate_income")
class TestAggregate:
    def test_rolls_up_per_year(self, _seeded_con):
        rows = income_report.aggregate_income(_seeded_con)
        assert len(rows) == 2
        by_year = {r.year: r for r in rows}
        assert by_year[2024].months == 12
        assert by_year[2024].total == Decimal("1200.00")
        assert by_year[2024].avg_month == Decimal("100.00")
        assert by_year[2025].months == 3
        assert by_year[2025].total == Decimal("1800.00")
        # 1800 / 3 months-with-data = 600 (not 150 which is 1800/12).
        assert by_year[2025].avg_month == Decimal("600.00")

    def test_sorts_year_desc(self, _seeded_con):
        rows = income_report.aggregate_income(_seeded_con)
        years = [r.year for r in rows]
        assert years == sorted(years, reverse=True)

    def test_empty_income_table_returns_empty_list(self):
        duckdb_repo.init_db()
        con = duckdb_repo.get_connection()
        try:
            rows = income_report.aggregate_income(con)
        finally:
            con.close()
        assert rows == []


@allure.epic("Reports")
@allure.feature("income — render_csv")
class TestRenderCsv:
    def test_header_and_rows(self, _seeded_con):
        rows = income_report.aggregate_income(_seeded_con)
        buf = io.StringIO()
        income_report.render_csv(rows, stream=buf)
        lines = buf.getvalue().splitlines()
        assert lines[0] == "year,months,total,avg_month"
        # Newest-year-first sort guarantees 2025 is on the first data
        # row — pin it so a reversed sort would fail loud.
        assert lines[1].split(",") == ["2025", "3", "1800.00", "600.00"]
        assert lines[2].split(",") == ["2024", "12", "1200.00", "100.00"]

    def test_empty_writes_only_header(self):
        buf = io.StringIO()
        income_report.render_csv([], stream=buf)
        assert buf.getvalue().strip() == "year,months,total,avg_month"


@allure.epic("Reports")
@allure.feature("income — render_rich")
class TestRenderRich:
    def test_renders_rows_and_total(self, _seeded_con):
        rows = income_report.aggregate_income(_seeded_con)
        buf = io.StringIO()
        income_report.render_rich(rows, currency="RSD", stream=buf)
        out = buf.getvalue()
        assert "2024" in out
        assert "2025" in out
        assert "RSD" in out
        assert "TOTAL" in out

    def test_renders_empty_placeholder(self):
        buf = io.StringIO()
        income_report.render_rich([], currency="RSD", stream=buf)
        assert "no income rows" in buf.getvalue()


@allure.epic("Reports")
@allure.feature("income — run()")
class TestRun:
    def test_missing_db_returns_nonzero(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(duckdb_repo, "DB_PATH", tmp_path / "absent.duckdb")
        buf = io.StringIO()
        rc = income_report.run(as_csv=False, stream=buf)
        captured = capsys.readouterr()
        assert rc == 1
        assert "DB not found" in captured.err

    def test_csv_mode_end_to_end(self, _seeded_con):
        buf = io.StringIO()
        rc = income_report.run(as_csv=True, stream=buf)
        assert rc == 0
        lines = buf.getvalue().splitlines()
        assert lines[0] == "year,months,total,avg_month"
        assert len(lines) == 3


@allure.epic("Reports")
@allure.feature("income — render_json / rows_from_json (remote transport)")
class TestRenderJson:
    """The remote-execution path ships raw rows as JSON (``inv report-income
    --remote`` runs the query on the server, the client renders locally).
    These tests pin the wire format so a future refactor of the CLI
    renderers cannot silently drift the serialization used by the
    ``tasks.py`` transport layer.
    """

    def test_emits_valid_json_array(self, _seeded_con):
        rows = income_report.aggregate_income(_seeded_con)
        buf = io.StringIO()
        income_report.render_json(rows, stream=buf)
        parsed = json.loads(buf.getvalue())
        assert isinstance(parsed, list)
        assert len(parsed) == len(rows)

    def test_decimal_serialized_as_string_for_precision(self, _seeded_con):
        """JSON has no Decimal type; floats lose precision. Emitting
        Decimals as canonical decimal strings is the only way to keep
        cents-exact totals across the SSH transport boundary.
        """
        rows = income_report.aggregate_income(_seeded_con)
        buf = io.StringIO()
        income_report.render_json(rows, stream=buf)
        parsed = json.loads(buf.getvalue())
        for entry in parsed:
            assert isinstance(entry["total"], str)
            assert isinstance(entry["avg_month"], str)
            Decimal(entry["total"])
            Decimal(entry["avg_month"])

    def test_rows_from_json_roundtrip_preserves_decimal(self, _seeded_con):
        rows = income_report.aggregate_income(_seeded_con)
        buf = io.StringIO()
        income_report.render_json(rows, stream=buf)
        rebuilt = income_report.rows_from_json(json.loads(buf.getvalue()))
        assert rebuilt == rows

    def test_run_as_json_writes_json_only(self, _seeded_con):
        buf = io.StringIO()
        rc = income_report.run(as_json=True, stream=buf)
        assert rc == 0
        parsed = json.loads(buf.getvalue())
        years = [entry["year"] for entry in parsed]
        assert years == sorted(years, reverse=True)

    def test_run_mutex_csv_and_json_rejected(self, _seeded_con):
        buf = io.StringIO()
        with pytest.raises(ValueError, match="mutually exclusive"):
            income_report.run(as_csv=True, as_json=True, stream=buf)


@allure.epic("Reports")
@allure.feature("income — CLI")
class TestCli:
    def test_json_flag_emits_json(self, _seeded_con, capsys):
        rc = income_report.main(["--json"])
        assert rc == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert len(parsed) == 2

    def test_json_and_csv_are_mutex(self, _seeded_con):
        with pytest.raises(SystemExit):
            income_report.main(["--csv", "--json"])

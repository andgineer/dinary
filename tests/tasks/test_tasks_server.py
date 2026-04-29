"""Tests for healthcheck helpers in :mod:`tasks.server`.

Covers the pure-Python helpers that parse query results and emit
``OK:`` / ``FAIL:`` lines.  The SQL execution path (local) is tested
via a real SQLite file on ``tmp_path`` using ``monkeypatch.chdir``
— the same pattern as ``test_tasks_db.py``.
"""

import sqlite3
from unittest.mock import MagicMock

import allure
import pytest

import tasks.server


@allure.epic("Deploy")
@allure.feature("healthcheck: sheet logging status display")
class TestHealthcheckSheetLog:
    def test_logged_to_sheet(self, capsys):
        tasks.server._healthcheck_sheet_log({"sheet": "3889|"})
        assert "logged to sheet" in capsys.readouterr().out

    def test_pending_shows_in_progress(self, capsys):
        tasks.server._healthcheck_sheet_log({"sheet": "3889|pending"})
        out = capsys.readouterr().out
        assert "3889" in out
        assert "in progress" in out

    def test_in_progress_shows_in_progress(self, capsys):
        tasks.server._healthcheck_sheet_log({"sheet": "3889|in_progress"})
        assert "in progress" in capsys.readouterr().out

    def test_poisoned_exits_1_with_manual_fix_message(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            tasks.server._healthcheck_sheet_log({"sheet": "3889|poisoned"})
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "3889" in err
        assert "failed" in err
        assert "manual fix" in err

    def test_unexpected_status_exits_1(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            tasks.server._healthcheck_sheet_log({"sheet": "3889|weird_status"})
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "unexpected" in err
        assert "weird_status" in err

    def test_no_expenses_in_db(self, capsys):
        tasks.server._healthcheck_sheet_log({"sheet": ""})
        assert "no expenses" in capsys.readouterr().out


@allure.epic("Deploy")
@allure.feature("healthcheck: last expense info display")
class TestHealthcheckLastExpenseInfo:
    def test_shows_whole_amount_currency_category(self, capsys):
        tasks.server._healthcheck_last_expense_info(
            {"last_expense": "1500.00|RSD|Groceries", "prev_day_total": ""}
        )
        assert "1500 RSD (Groceries)" in capsys.readouterr().out

    def test_shows_fractional_amount(self, capsys):
        tasks.server._healthcheck_last_expense_info(
            {"last_expense": "1500.50|RSD|Groceries", "prev_day_total": ""}
        )
        assert "1500.50 RSD" in capsys.readouterr().out

    def test_shows_yesterday_total_single_currency(self, capsys):
        tasks.server._healthcheck_last_expense_info(
            {"last_expense": "", "prev_day_total": "RSD:8200.00"}
        )
        assert "yesterday total 8200 RSD" in capsys.readouterr().out

    def test_shows_yesterday_total_multiple_currencies(self, capsys):
        tasks.server._healthcheck_last_expense_info(
            {"last_expense": "", "prev_day_total": "RSD:5000.00,EUR:20.50"}
        )
        out = capsys.readouterr().out
        assert "5000 RSD" in out
        assert "20.50 EUR" in out

    def test_empty_results_prints_nothing(self, capsys):
        tasks.server._healthcheck_last_expense_info({})
        assert capsys.readouterr().out == ""


@allure.epic("Deploy")
@allure.feature("healthcheck: _healthcheck_run_queries local DB execution")
class TestHealthcheckRunQueriesLocal:
    @pytest.fixture
    def _cwd(self, tmp_path, monkeypatch):
        (tmp_path / "data").mkdir()
        monkeypatch.chdir(tmp_path)
        return tmp_path

    def test_returns_results_keyed_by_query_name(self, _cwd):
        db_path = _cwd / "data" / "dinary.db"
        with sqlite3.connect(db_path) as con:
            con.execute("CREATE TABLE t (v TEXT)")
            con.execute("INSERT INTO t VALUES ('hello')")

        results = tasks.server._healthcheck_run_queries(
            MagicMock(),
            False,
            greeting="SELECT v FROM t LIMIT 1",
            count="SELECT count(*) FROM t",
        )

        assert results["greeting"] == "hello"
        assert results["count"] == "1"

    def test_exits_1_when_db_missing(self, _cwd, capsys):
        with pytest.raises(SystemExit) as excinfo:
            tasks.server._healthcheck_run_queries(MagicMock(), False, q="SELECT 1")
        assert excinfo.value.code == 1
        assert "No local DB" in capsys.readouterr().err

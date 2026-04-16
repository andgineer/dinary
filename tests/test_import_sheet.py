"""Tests for the budget import (import_sheet) layer."""

from unittest.mock import MagicMock, patch

import allure
import pytest

from dinary.services import duckdb_repo
from dinary.services.import_sheet import import_year


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "CONFIG_DB", tmp_path / "config.duckdb")


def _seed_config():
    duckdb_repo.init_config_db()
    con = duckdb_repo.get_config_connection(read_only=False)
    try:
        con.execute("INSERT INTO categories VALUES (1, 'еда')")
        con.execute("INSERT INTO categories VALUES (2, 'мобильник')")
        con.execute("INSERT INTO categories VALUES (3, 'кафе')")
        con.execute("INSERT INTO family_members VALUES (1, 'собака')")
        con.execute("INSERT INTO tags VALUES (1, 'подписка')")
        con.execute(
            "INSERT INTO source_type_mapping VALUES (0, 'еда&бытовые', 'собака', 1, 1, NULL, NULL)"
        )
        con.execute(
            "INSERT INTO source_type_mapping VALUES (0, 'мобильник', '', 2, NULL, NULL, NULL)"
        )
        con.execute(
            "INSERT INTO source_type_mapping VALUES (0, 'кафе', 'путешествия', 3, NULL, NULL, NULL)"
        )
        con.execute(
            "INSERT INTO source_type_mapping VALUES (0, 'кафе', 'приложения', 3, NULL, NULL, [1])"
        )
        con.execute(
            "INSERT INTO events VALUES (1, 'отпуск-2026', '2026-01-01', '2026-12-31', true, NULL)"
        )
    finally:
        con.close()


SHEET_ROWS_DISPLAY = [
    ["Date", "RSD", "EUR", "Category", "Group", "Comment", "Month", "Rate"],
    ["2026-01-01", "4500", "", "еда&бытовые", "собака", "lunch; dinner", "1", "117"],
    ["2026-01-01", "400", "", "мобильник", "", "", "1", "117"],
    ["2026-01-01", "2000", "", "кафе", "путешествия", "resort", "1", "117"],
    ["2026-02-01", "1500", "", "еда&бытовые", "собака", "snack", "2", "117"],
    ["2026-02-01", "0", "", "мобильник", "", "", "2", "117"],
]

SHEET_ROWS_FORMULA = [
    ["Date", "RSD", "EUR", "Category", "Group", "Comment", "Month", "Rate"],
    ["2026-01-01", "=1500+3000", "", "еда&бытовые", "собака", "lunch; dinner", "1", "117"],
    ["2026-01-01", "=400", "", "мобильник", "", "", "1", "117"],
    ["2026-01-01", "=2000", "", "кафе", "путешествия", "resort", "1", "117"],
    ["2026-02-01", "=1500", "", "еда&бытовые", "собака", "snack", "2", "117"],
    ["2026-02-01", "0", "", "мобильник", "", "", "2", "117"],
]


def _mock_sheet():
    ws = MagicMock()
    ws.get_all_values.side_effect = [SHEET_ROWS_DISPLAY, SHEET_ROWS_FORMULA]
    ss = MagicMock()
    ss.sheet1 = ws
    return ss


@allure.epic("Import")
@allure.feature("Budget Rebuild")
class TestImportYear:
    def _run_import(self):
        _seed_config()
        with (
            patch("dinary.services.import_sheet.get_sheet", return_value=_mock_sheet()),
            patch("dinary.services.import_sheet._ensure_travel_event", return_value=1),
        ):
            return import_year(2026)

    def test_creates_expenses(self):
        result = self._run_import()
        assert result["expenses_created"] > 0
        assert result["errors"] == 0

    def test_correct_months(self):
        result = self._run_import()
        assert 1 in result["months"]
        assert 2 in result["months"]

    def test_food_gets_beneficiary(self):
        self._run_import()
        con = duckdb_repo.get_budget_connection(2026)
        try:
            rows = con.execute(
                "SELECT beneficiary_id FROM expenses WHERE category_id = 1"
            ).fetchall()
            assert all(r[0] == 1 for r in rows)
        finally:
            con.close()

    def test_travel_gets_event(self):
        self._run_import()
        con = duckdb_repo.get_budget_connection(2026)
        try:
            rows = con.execute("SELECT event_id FROM expenses WHERE category_id = 3").fetchall()
            assert all(r[0] is not None for r in rows)
        finally:
            con.close()

    def test_formula_split_into_individual_amounts(self):
        """Formula =1500+3000 should create 2 separate expenses."""
        self._run_import()
        con = duckdb_repo.get_budget_connection(2026)
        try:
            rows = con.execute(
                "SELECT amount FROM expenses "
                "WHERE category_id = 1 AND MONTH(datetime) = 1 "
                "ORDER BY amount"
            ).fetchall()
            amounts = [float(r[0]) for r in rows]
            assert 1500.0 in amounts
            assert 3000.0 in amounts
        finally:
            con.close()

    def test_idempotent_reimport(self):
        """Running import twice produces the same result."""
        self._run_import()
        con = duckdb_repo.get_budget_connection(2026)
        try:
            count1 = con.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
        finally:
            con.close()

        with (
            patch("dinary.services.import_sheet.get_sheet", return_value=_mock_sheet()),
            patch("dinary.services.import_sheet._ensure_travel_event", return_value=1),
        ):
            result2 = import_year(2026)

        con = duckdb_repo.get_budget_connection(2026)
        try:
            count2 = con.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
        finally:
            con.close()

        assert count1 == count2
        assert result2["expenses_created"] == count1

    def test_zero_amount_row_skipped(self):
        """Rows with zero amount should not create expenses."""
        self._run_import()
        con = duckdb_repo.get_budget_connection(2026)
        try:
            rows = con.execute(
                "SELECT COUNT(*) FROM expenses WHERE MONTH(datetime) = 2 AND category_id = 2"
            ).fetchone()
            assert rows[0] == 0
        finally:
            con.close()

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
        con.execute(
            "INSERT INTO sheet_import_sources VALUES (2026, 'test-sheet', '', 'eur_primary', NULL)"
        )
        con.execute("INSERT INTO categories VALUES (1, 'еда')")
        con.execute("INSERT INTO categories VALUES (2, 'мобильник')")
        con.execute("INSERT INTO categories VALUES (3, 'кафе')")
        con.execute("INSERT INTO family_members VALUES (1, 'собака')")
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


def _mock_sheet():
    ws = MagicMock()
    ws.get_all_values.return_value = SHEET_ROWS_DISPLAY
    ss = MagicMock()
    ss.sheet1 = ws
    return ss


def _mock_sheet_with_eur():
    ws = MagicMock()
    ws.get_all_values.return_value = [
        ["Date", "RSD", "EUR", "Category", "Group", "Comment", "Month", "Rate"],
        ["2026-01-01", "4500", "38.46", "еда&бытовые", "собака", "lunch; dinner", "1", "117"],
    ]
    ss = MagicMock()
    ss.sheet1 = ws
    return ss


def _mock_prefetch_rates(year, layout):
    """Return rates that produce 1:1 conversion (rate_cur == rate_eur)."""
    from decimal import Decimal

    one_to_one = {"rate_cur": Decimal("1"), "rate_eur": Decimal("1")}
    return {m: one_to_one for m in range(1, 13)}


@allure.epic("Import")
@allure.feature("Budget Rebuild")
class TestImportYear:
    def _run_import(self):
        _seed_config()
        with (
            patch("dinary.services.import_sheet.get_sheet", return_value=_mock_sheet()),
            patch("dinary.services.import_sheet._ensure_travel_event", return_value=1),
            patch(
                "dinary.services.import_sheet._prefetch_monthly_rates",
                side_effect=_mock_prefetch_rates,
            ),
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

    def test_no_formula_split(self):
        """One sheet row should create one expense (no formula splitting)."""
        self._run_import()
        con = duckdb_repo.get_budget_connection(2026)
        try:
            rows = con.execute(
                "SELECT amount_original FROM expenses WHERE category_id = 1 AND MONTH(datetime) = 1"
            ).fetchall()
            assert len(rows) == 1
            assert float(rows[0][0]) == 4500.0
        finally:
            con.close()

    def test_idempotent_reimport(self):
        """Running import twice produces the same result (DELETE + re-insert)."""
        self._run_import()
        con = duckdb_repo.get_budget_connection(2026)
        try:
            count1 = con.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
        finally:
            con.close()

        with (
            patch("dinary.services.import_sheet.get_sheet", return_value=_mock_sheet()),
            patch("dinary.services.import_sheet._ensure_travel_event", return_value=1),
            patch(
                "dinary.services.import_sheet._prefetch_monthly_rates",
                side_effect=_mock_prefetch_rates,
            ),
        ):
            result2 = import_year(2026)

        con = duckdb_repo.get_budget_connection(2026)
        try:
            count2 = con.execute("SELECT COUNT(*) FROM expenses").fetchone()[0]
        finally:
            con.close()

        assert count1 == count2

    def test_zero_amount_row_skipped(self):
        self._run_import()
        con = duckdb_repo.get_budget_connection(2026)
        try:
            rows = con.execute(
                "SELECT COUNT(*) FROM expenses WHERE MONTH(datetime) = 2 AND category_id = 2"
            ).fetchone()
            assert rows[0] == 0
        finally:
            con.close()

    def test_stores_amount_original_and_currency(self):
        self._run_import()
        con = duckdb_repo.get_budget_connection(2026)
        try:
            row = con.execute(
                "SELECT amount_original, currency_original FROM expenses "
                "WHERE category_id = 1 AND MONTH(datetime) = 1"
            ).fetchone()
            assert float(row[0]) == 4500.0
            assert row[1] == "RSD"
        finally:
            con.close()

    def test_uses_eur_column_as_canonical_amount_for_2026(self):
        _seed_config()
        with (
            patch("dinary.services.import_sheet.get_sheet", return_value=_mock_sheet_with_eur()),
            patch("dinary.services.import_sheet._ensure_travel_event", return_value=1),
            patch(
                "dinary.services.import_sheet._prefetch_monthly_rates",
                side_effect=_mock_prefetch_rates,
            ),
        ):
            import_year(2026)

        con = duckdb_repo.get_budget_connection(2026)
        try:
            row = con.execute(
                "SELECT amount, amount_original, currency_original FROM expenses WHERE category_id = 1"
            ).fetchone()
            assert row is not None
            assert float(row[0]) == 38.46
            assert float(row[1]) == 4500.0
            assert row[2] == "RSD"
        finally:
            con.close()

    def test_missing_import_source_raises(self):
        duckdb_repo.init_config_db()
        with pytest.raises(ValueError, match="sheet_import_sources is missing a row for year 2026"):
            import_year(2026)

"""Tests for the bootstrap budget import (3D)."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import allure
import pytest

from dinary.imports import expense_import
from dinary.imports.expense_import import import_year
from dinary.services import duckdb_repo


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "CONFIG_DB", tmp_path / "config.duckdb")


def _seed_config():
    duckdb_repo.init_config_db()
    con = duckdb_repo.get_config_connection(read_only=False)
    try:
        con.execute(
            "INSERT INTO import_sources"
            " (year, spreadsheet_id, worksheet_name, layout_key, notes)"
            " VALUES (2026, 'sheet-id', '', 'default', NULL)",
        )
        con.execute("INSERT INTO category_groups VALUES (1, 'g', 1)")
        con.execute("INSERT INTO categories VALUES (1, 'еда', 1)")
        con.execute("INSERT INTO categories VALUES (2, 'мобильник', 1)")
        con.execute("INSERT INTO categories VALUES (3, 'кафе', 1)")
        con.execute("INSERT INTO tags VALUES (1, 'собака')")
        con.execute(
            "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled)"
            " VALUES (1, 'отпуск-2026', '2026-01-01', '2026-12-31', true)",
        )
        # Russia-trip one-off event normally seeded by `EXPLICIT_EVENTS`;
        # `expense_import.import_year(2026)` looks it up by name.
        con.execute(
            "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled)"
            " VALUES (2, 'поездка в Россию', '2026-08-01', '2026-08-31', false)",
        )
        con.execute(
            "INSERT INTO import_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (1, 0, 'еда', 'собака', 1, NULL)",
        )
        con.execute("INSERT INTO import_mapping_tags VALUES (1, 1)")
        con.execute(
            "INSERT INTO import_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (2, 0, 'мобильник', '', 2, NULL)",
        )
        con.execute(
            "INSERT INTO import_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (3, 0, 'кафе', 'путешествия', 3, 1)",
        )
    finally:
        con.close()


SHEET_ROWS = [
    ["Date", "RSD", "EUR", "Category", "Group", "Comment", "Month", "Rate"],
    ["2026-01-01", "4500", "", "еда", "собака", "lunch", "1", "117"],
    ["2026-01-01", "400", "", "мобильник", "", "", "1", "117"],
    ["2026-01-01", "2000", "", "кафе", "путешествия", "resort", "1", "117"],
    ["2026-02-01", "1500", "", "еда", "собака", "snack", "2", "117"],
]


def _mock_sheet():
    ws = MagicMock()
    ws.get_all_values.return_value = SHEET_ROWS
    ss = MagicMock()
    ss.sheet1 = ws
    return ss


def _mock_prefetch_rates(_year, _layout, *, config_con=None):
    one_to_one = {"rate_cur": Decimal("1"), "rate_eur": Decimal("1")}
    return {m: one_to_one for m in range(1, 13)}


@allure.epic("Import")
@allure.feature("Bootstrap (3D)")
class TestImportYear:
    @patch(
        "dinary.imports.expense_import._prefetch_monthly_rates", side_effect=_mock_prefetch_rates
    )
    @patch("dinary.imports.expense_import.get_sheet")
    def test_imports_rows_with_3d_dimensions(self, mock_sheet, _mr):
        _seed_config()
        mock_sheet.return_value = _mock_sheet()

        result = import_year(2026)

        assert result["expenses_created"] == 4
        assert result["errors"] == 0

        bcon = duckdb_repo.get_budget_connection(2026)
        try:
            rows = bcon.execute(
                "SELECT category_id, event_id, sheet_category, sheet_group, comment"
                " FROM expenses ORDER BY datetime, sheet_category",
            ).fetchall()
            assert len(rows) == 4
            for cat_id, _ev_id, sheet_cat, sheet_grp, _ in rows:
                assert cat_id is not None
                assert sheet_cat is not None
                assert sheet_grp is not None
        finally:
            bcon.close()

    @patch(
        "dinary.imports.expense_import._prefetch_monthly_rates", side_effect=_mock_prefetch_rates
    )
    @patch("dinary.imports.expense_import.get_sheet")
    def test_attaches_tags_from_mapping(self, mock_sheet, _mr):
        _seed_config()
        mock_sheet.return_value = _mock_sheet()
        import_year(2026)

        bcon = duckdb_repo.get_budget_connection(2026)
        try:
            tag_rows = bcon.execute(
                "SELECT t.tag_id FROM expense_tags t"
                " JOIN expenses e ON e.id = t.expense_id"
                " WHERE e.sheet_category = 'еда'",
            ).fetchall()
            assert {r[0] for r in tag_rows} == {1}
        finally:
            bcon.close()

    @patch(
        "dinary.imports.expense_import._prefetch_monthly_rates", side_effect=_mock_prefetch_rates
    )
    @patch("dinary.imports.expense_import.get_sheet")
    def test_does_not_enqueue_logging_jobs(self, mock_sheet, _mr):
        _seed_config()
        mock_sheet.return_value = _mock_sheet()
        import_year(2026)

        bcon = duckdb_repo.get_budget_connection(2026)
        try:
            assert duckdb_repo.list_logging_jobs(bcon) == []
        finally:
            bcon.close()

    @patch(
        "dinary.imports.expense_import._prefetch_monthly_rates", side_effect=_mock_prefetch_rates
    )
    @patch("dinary.imports.expense_import.get_sheet")
    def test_re_import_is_destructive(self, mock_sheet, _mr):
        _seed_config()
        mock_sheet.return_value = _mock_sheet()
        first = import_year(2026)
        second = import_year(2026)
        assert first["expenses_created"] == second["expenses_created"]

    @patch(
        "dinary.imports.expense_import._prefetch_monthly_rates", side_effect=_mock_prefetch_rates
    )
    @patch("dinary.imports.expense_import.get_sheet")
    def test_re_import_with_pending_logging_jobs_does_not_violate_fk(
        self,
        mock_sheet,
        _mr,
    ):
        # Bug regression: if `sheet_logging_jobs` had pending rows from a
        # prior runtime session, `DELETE FROM expenses` would fail with a
        # foreign key violation because the queue rows reference
        # expenses.id with no ON DELETE CASCADE.
        _seed_config()
        mock_sheet.return_value = _mock_sheet()
        import_year(2026)

        bcon = duckdb_repo.get_budget_connection(2026)
        try:
            existing_id = bcon.execute("SELECT id FROM expenses LIMIT 1").fetchone()[0]
            bcon.execute(
                "INSERT INTO sheet_logging_jobs (expense_id, status) VALUES (?, 'pending')",
                [existing_id],
            )
        finally:
            bcon.close()

        result = import_year(2026)
        assert result["errors"] == 0
        bcon = duckdb_repo.get_budget_connection(2026)
        try:
            assert duckdb_repo.list_logging_jobs(bcon) == []
        finally:
            bcon.close()

    @patch(
        "dinary.imports.expense_import._prefetch_monthly_rates", side_effect=_mock_prefetch_rates
    )
    @patch("dinary.imports.expense_import.get_sheet")
    def test_resolve_dimensions_raise_skips_row_instead_of_aborting(
        self,
        mock_sheet,
        _mr,
        monkeypatch,
    ):
        # Bug regression: when `_resolve_dimensions` raised (e.g. via
        # `_resolve_tag_ids` -> "tag not found" on the no-mapping fallback),
        # the exception escaped the per-row guard and tore down the whole
        # import. The fix wraps `_resolve_dimensions` in a try/except that
        # bumps `errors` and continues.
        _seed_config()
        mock_sheet.return_value = _mock_sheet()

        real_resolve = expense_import._resolve_dimensions
        call_state = {"n": 0}

        def flaky_resolve(*args, **kwargs):
            call_state["n"] += 1
            if call_state["n"] == 1:
                msg = "tag 'phantom' not found in config.tags; re-seed required"
                raise ValueError(msg)
            return real_resolve(*args, **kwargs)

        monkeypatch.setattr(expense_import, "_resolve_dimensions", flaky_resolve)

        result = import_year(2026)
        assert result["errors"] == 1
        # 4 rows in SHEET_ROWS; first one fails resolution, other three
        # succeed.
        assert result["expenses_created"] == 3

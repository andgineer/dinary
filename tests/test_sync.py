"""Tests for the Google Sheets sync layer."""

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import allure
import pytest

from dinary.services import duckdb_repo
from dinary.services.sync import _build_aggregates, _sync_single_row, _write_aggregates_to_sheet

TRAVEL_ENVELOPE = duckdb_repo.TRAVEL_ENVELOPE


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "CONFIG_DB", tmp_path / "config.duckdb")


@pytest.fixture
def populated_db(tmp_path):
    """Config + budget DBs with a couple of expenses."""
    duckdb_repo.init_config_db()

    con = duckdb_repo.get_config_connection(read_only=False)
    try:
        con.execute("INSERT INTO categories VALUES (1, 'еда&бытовые')")
        con.execute("INSERT INTO categories VALUES (2, 'мобильник')")
        con.execute("INSERT INTO family_members VALUES (1, 'собака')")
        con.execute(
            "INSERT INTO source_type_mapping VALUES (0, 'еда&бытовые', 'собака', 1, 1, NULL, NULL)"
        )
        con.execute(
            "INSERT INTO source_type_mapping VALUES (0, 'мобильник', '', 2, NULL, NULL, NULL)"
        )
    finally:
        con.close()

    bcon = duckdb_repo.get_budget_connection(2026)
    try:
        duckdb_repo.insert_expense(
            bcon,
            "s1",
            datetime(2026, 4, 14, 10, 0),
            12.82,
            1500.0,
            "RSD",
            1,
            1,
            None,
            None,
            "lunch",
        )
        duckdb_repo.insert_expense(
            bcon,
            "s2",
            datetime(2026, 4, 15, 12, 0),
            25.64,
            3000.0,
            "RSD",
            1,
            1,
            None,
            None,
            "dinner",
        )
        duckdb_repo.insert_expense(
            bcon,
            "s3",
            datetime(2026, 4, 16, 9, 0),
            3.42,
            400.0,
            "RSD",
            2,
            None,
            None,
            None,
            "",
        )
    finally:
        bcon.close()


@allure.epic("Sync")
@allure.feature("Aggregation")
class TestBuildAggregates:
    def test_aggregates_by_sheet_key(self, populated_db):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            agg = _build_aggregates(con, 2026, 4)
            assert agg is not None
            assert ("еда&бытовые", "собака") in agg
            assert ("мобильник", "") in agg
        finally:
            con.close()

    def test_totals_correct(self, populated_db):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            agg = _build_aggregates(con, 2026, 4)
            food_total = agg[("еда&бытовые", "собака")]["total_rsd"]
            assert food_total == Decimal("4500")

            phone_total = agg[("мобильник", "")]["total_rsd"]
            assert phone_total == Decimal("400")
        finally:
            con.close()

    def test_individual_amounts_tracked(self, populated_db):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            agg = _build_aggregates(con, 2026, 4)
            food_amounts = agg[("еда&бытовые", "собака")]["amounts"]
            assert len(food_amounts) == 2
            assert Decimal("1500") in food_amounts
            assert Decimal("3000") in food_amounts
        finally:
            con.close()

    def test_empty_month_returns_none(self, populated_db):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            agg = _build_aggregates(con, 2026, 1)
            assert agg is None
        finally:
            con.close()

    def test_comments_collected(self, populated_db):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            agg = _build_aggregates(con, 2026, 4)
            comments = agg[("еда&бытовые", "собака")]["comments"]
            assert "lunch" in comments
            assert "dinner" in comments
        finally:
            con.close()


@allure.epic("Sync")
@allure.feature("Sheet Write")
class TestWriteAggregates:
    def _make_mock_ws(self, existing_formula=""):
        ws = MagicMock()
        vr = [[existing_formula]] if existing_formula else [[]]
        ws.batch_get.return_value = [vr]
        ws.batch_update = MagicMock()
        return ws

    def test_writes_running_sum_formula(self, populated_db):
        ws = self._make_mock_ws("")
        agg = {
            ("еда&бытовые", "собака"): {
                "total_rsd": Decimal("4500"),
                "amounts": [Decimal("1500"), Decimal("3000")],
                "comments": ["lunch", "dinner"],
            },
        }
        all_values = [
            ["Date", "RSD", "EUR", "Category", "Group", "Comment", "Month", "Rate"],
            ["Apr-1", "", "", "еда&бытовые", "собака", "", "4", ""],
        ]

        def find_row(vals, month, cat, grp):
            return 2

        with patch("dinary.services.sync.find_category_row", side_effect=find_row):
            written = _write_aggregates_to_sheet(ws, all_values, 4, agg)

        assert written > 0
        args = ws.batch_update.call_args
        batch = args[0][0]

        rsd_update = next(u for u in batch if u["values"][0][0].startswith("="))
        formula = rsd_update["values"][0][0]
        assert "+" in formula
        assert "1500" in formula
        assert "3000" in formula

    def test_skips_unchanged_total(self, populated_db):
        ws = self._make_mock_ws("=1500+3000")
        agg = {
            ("еда&бытовые", "собака"): {
                "total_rsd": Decimal("4500"),
                "amounts": [Decimal("1500"), Decimal("3000")],
                "comments": [],
            },
        }
        all_values = [
            ["Date", "RSD", "EUR", "Category", "Group", "Comment", "Month", "Rate"],
            ["Apr-1", "", "", "еда&бытовые", "собака", "", "4", ""],
        ]

        def find_row(vals, month, cat, grp):
            return 2

        with patch("dinary.services.sync.find_category_row", side_effect=find_row):
            written = _write_aggregates_to_sheet(ws, all_values, 4, agg)

        assert written == 0
        ws.batch_update.assert_not_called()


@allure.epic("Sync")
@allure.feature("Idempotency")
class TestSyncIdempotency:
    def test_rerun_produces_same_result(self, populated_db):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            agg1 = _build_aggregates(con, 2026, 4)
            agg2 = _build_aggregates(con, 2026, 4)
            for key in agg1:
                assert agg1[key]["total_rsd"] == agg2[key]["total_rsd"]
                assert agg1[key]["amounts"] == agg2[key]["amounts"]
        finally:
            con.close()

    def test_sync_clears_job(self, populated_db):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            jobs_before = duckdb_repo.get_dirty_sync_jobs(con)
            assert (2026, 4) in jobs_before
        finally:
            con.close()

        with (
            patch("dinary.services.sync.get_sheet") as mock_sheet,
            patch("dinary.services.sync.find_month_range", return_value=(2, 30)),
            patch("dinary.services.sync.find_category_row", return_value=2),
            patch("dinary.services.sync.get_month_rate", return_value="117"),
        ):
            ws_mock = MagicMock()
            ws_mock.get_all_values.return_value = [["header"]]
            ws_mock.batch_get.return_value = [[[""]]]
            mock_sheet.return_value.sheet1 = ws_mock

            from dinary.services.sync import _sync_month_core

            _sync_month_core(2026, 4, rate=Decimal("117"))

        con = duckdb_repo.get_budget_connection(2026)
        try:
            jobs_after = duckdb_repo.get_dirty_sync_jobs(con)
            assert (2026, 4) not in jobs_after
        finally:
            con.close()


@allure.epic("Sync")
@allure.feature("Targeted Row Sync")
class TestSyncSingleRow:
    def test_appends_amount_to_target_row(self, populated_db):
        all_values = [
            ["Date", "RSD", "EUR", "Category", "Group", "Comment", "Month", "Rate"],
            ["Apr-1", "=1500", "", "еда&бытовые", "собака", "lunch", "4", "117"],
        ]
        with (
            patch("dinary.services.sync.get_sheet") as mock_sheet,
            patch("dinary.services.sync.find_month_range", return_value=(2, 2)),
            patch("dinary.services.sync.find_category_row", return_value=2),
            patch("dinary.services.sync.get_month_rate", return_value="117"),
            patch("dinary.services.sync.append_to_rsd_formula") as mock_append,
            patch("dinary.services.sync.append_comment") as mock_comment,
        ):
            ws_mock = MagicMock()
            ws_mock.get_all_values.return_value = all_values
            mock_sheet.return_value.sheet1 = ws_mock

            _sync_single_row(2026, 4, "еда&бытовые", "собака", 500.0, "snack", date(2026, 4, 17))

            mock_append.assert_called_once_with(ws_mock, 2, 500.0)
            mock_comment.assert_called_once()


@allure.epic("Sync")
@allure.feature("Reverse Mapping Equivalence")
class TestSyncEquivalence:
    def test_simple_reverse_map(self, populated_db):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            result = duckdb_repo.reverse_lookup_mapping(con, 2026, 1, 1, None, None)
            assert result == ("еда&бытовые", "собака")
        finally:
            con.close()

    def test_no_envelope_reverse_map(self, populated_db):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            result = duckdb_repo.reverse_lookup_mapping(con, 2026, 2, None, None, None)
            assert result == ("мобильник", "")
        finally:
            con.close()

    def test_aggregates_match_original_keys(self, populated_db):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            agg = _build_aggregates(con, 2026, 4)
            assert agg is not None
            for cat, grp in agg:
                assert isinstance(cat, str)
                assert isinstance(grp, str)
        finally:
            con.close()

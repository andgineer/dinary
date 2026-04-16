"""Tests for the Google Sheets sync layer."""

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import allure
import pytest

from dinary.services import duckdb_repo
from dinary.services.sync import _build_aggregates, _sync_single_row, _write_aggregates_to_sheet


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
        con.execute("INSERT INTO category_groups VALUES (1, 'питание', NULL)")
        con.execute("INSERT INTO category_groups VALUES (2, '', NULL)")
        con.execute("INSERT INTO categories VALUES (1, 'еда&бытовые', 1)")
        con.execute("INSERT INTO categories VALUES (2, 'мобильник', 2)")
        con.execute("INSERT INTO family_members VALUES (1, 'собака')")
        con.execute(
            "INSERT INTO sheet_category_mapping "
            "VALUES ('еда&бытовые', 'собака', 1, 1, NULL, NULL, NULL)"
        )
        con.execute(
            "INSERT INTO sheet_category_mapping "
            "VALUES ('мобильник', '', 2, NULL, NULL, NULL, NULL)"
        )
    finally:
        duckdb_repo.close_connection(con)

    bcon = duckdb_repo.get_budget_connection(2026)
    try:
        duckdb_repo.insert_expense(
            bcon, "s1", datetime(2026, 4, 14, 10, 0),
            1500.0, "RSD", 1, 1, None, None, [], "lunch",
        )
        duckdb_repo.insert_expense(
            bcon, "s2", datetime(2026, 4, 15, 12, 0),
            3000.0, "RSD", 1, 1, None, None, [], "dinner",
        )
        duckdb_repo.insert_expense(
            bcon, "s3", datetime(2026, 4, 16, 9, 0),
            400.0, "RSD", 2, None, None, None, [], "",
        )
    finally:
        duckdb_repo.close_connection(bcon)


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
            duckdb_repo.close_connection(con)

    def test_totals_correct(self, populated_db):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            agg = _build_aggregates(con, 2026, 4)
            food_total = agg[("еда&бытовые", "собака")]["total_rsd"]
            assert food_total == Decimal("4500")

            phone_total = agg[("мобильник", "")]["total_rsd"]
            assert phone_total == Decimal("400")
        finally:
            duckdb_repo.close_connection(con)

    def test_individual_amounts_tracked(self, populated_db):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            agg = _build_aggregates(con, 2026, 4)
            food_amounts = agg[("еда&бытовые", "собака")]["amounts"]
            assert len(food_amounts) == 2
            assert Decimal("1500") in food_amounts
            assert Decimal("3000") in food_amounts
        finally:
            duckdb_repo.close_connection(con)

    def test_empty_month_returns_none(self, populated_db):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            agg = _build_aggregates(con, 2026, 1)
            assert agg is None
        finally:
            duckdb_repo.close_connection(con)

    def test_comments_collected(self, populated_db):
        con = duckdb_repo.get_budget_connection(2026)
        try:
            agg = _build_aggregates(con, 2026, 4)
            comments = agg[("еда&бытовые", "собака")]["comments"]
            assert "lunch" in comments
            assert "dinner" in comments
        finally:
            duckdb_repo.close_connection(con)


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
        """Running _build_aggregates twice gives the same totals."""
        con = duckdb_repo.get_budget_connection(2026)
        try:
            agg1 = _build_aggregates(con, 2026, 4)
            agg2 = _build_aggregates(con, 2026, 4)
            for key in agg1:
                assert agg1[key]["total_rsd"] == agg2[key]["total_rsd"]
                assert agg1[key]["amounts"] == agg2[key]["amounts"]
        finally:
            duckdb_repo.close_connection(con)

    def test_sync_clears_job(self, populated_db):
        """After a full sync_month_core, the dirty job is cleared."""
        con = duckdb_repo.get_budget_connection(2026)
        try:
            jobs_before = duckdb_repo.get_dirty_sync_jobs(con)
            assert (2026, 4) in jobs_before
        finally:
            duckdb_repo.close_connection(con)

        with (
            patch("dinary.services.sync.get_sheet") as mock_sheet,
            patch("dinary.services.sync.find_month_range", return_value=(2, 30)),
            patch("dinary.services.sync.find_category_row", return_value=2),
            patch("dinary.services.sync.get_month_rate", return_value="117"),
        ):
            ws_mock = MagicMock()
            ws_mock.get_all_values.return_value = [["header"]]
            ws_mock.batch_get.return_value = [[[""]] ]
            mock_sheet.return_value.sheet1 = ws_mock

            from dinary.services.sync import _sync_month_core
            _sync_month_core(2026, 4, rate=Decimal("117"))

        con = duckdb_repo.get_budget_connection(2026)
        try:
            jobs_after = duckdb_repo.get_dirty_sync_jobs(con)
            assert (2026, 4) not in jobs_after
        finally:
            duckdb_repo.close_connection(con)

    def test_sync_writes_rate_when_cell_empty(self, populated_db):
        """When the month's rate cell is empty and a rate is provided, sync writes it."""
        from dinary.services.sheets import COL_RATE_EUR
        from dinary.services.sync import _sync_month_core

        with (
            patch("dinary.services.sync.get_sheet") as mock_sheet,
            patch("dinary.services.sync.find_month_range", return_value=(2, 30)),
            patch("dinary.services.sync.find_category_row", return_value=2),
            patch("dinary.services.sync.get_month_rate", return_value=None),
        ):
            ws_mock = MagicMock()
            ws_mock.get_all_values.return_value = [["header"]]
            ws_mock.batch_get.return_value = [[[""]] ]
            mock_sheet.return_value.sheet1 = ws_mock

            _sync_month_core(2026, 4, rate=Decimal("117.5"))

            ws_mock.update_cell.assert_any_call(2, COL_RATE_EUR, "117.5")

    def test_sync_all_dirty_discovers_multiple_years(self, populated_db, tmp_path):
        """sync_all_dirty finds dirty jobs across multiple budget_YYYY.duckdb files."""
        from dinary.services.sync import sync_all_dirty

        bcon_2025 = duckdb_repo.get_budget_connection(2025)
        try:
            duckdb_repo.insert_expense(
                bcon_2025, "x25", datetime(2025, 12, 15, 10, 0),
                500.0, "RSD", 1, 1, None, None, [], "",
            )
        finally:
            duckdb_repo.close_connection(bcon_2025)

        with (
            patch("dinary.services.sync.get_sheet") as mock_sheet,
            patch("dinary.services.sync.find_month_range", return_value=(2, 30)),
            patch("dinary.services.sync.find_category_row", return_value=2),
            patch("dinary.services.sync.get_month_rate", return_value="117"),
        ):
            ws_mock = MagicMock()
            ws_mock.get_all_values.return_value = [["header"]]
            ws_mock.batch_get.return_value = [[[""]] ]
            mock_sheet.return_value.sheet1 = ws_mock

            synced = sync_all_dirty()

        assert synced >= 2

    def test_sync_skips_rate_when_cell_populated(self, populated_db):
        """When the month already has a rate, sync does not overwrite it."""
        from dinary.services.sync import _sync_month_core

        with (
            patch("dinary.services.sync.get_sheet") as mock_sheet,
            patch("dinary.services.sync.find_month_range", return_value=(2, 30)),
            patch("dinary.services.sync.find_category_row", return_value=2),
            patch("dinary.services.sync.get_month_rate", return_value="117"),
        ):
            ws_mock = MagicMock()
            ws_mock.get_all_values.return_value = [["header"]]
            ws_mock.batch_get.return_value = [[[""]] ]
            mock_sheet.return_value.sheet1 = ws_mock

            _sync_month_core(2026, 4, rate=Decimal("120"))

            for call in ws_mock.update_cell.call_args_list:
                assert call[0][1] != 8, "Should not write to rate column when rate already exists"


@allure.epic("Sync")
@allure.feature("Targeted Row Sync")
class TestSyncSingleRow:
    def test_appends_amount_to_target_row(self, populated_db):
        """_sync_single_row calls append_to_rsd_formula on the correct row."""
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

    def test_skips_comment_when_empty(self, populated_db):
        """No comment append when comment is empty."""
        all_values = [
            ["Date", "RSD", "EUR", "Category", "Group", "Comment", "Month", "Rate"],
            ["Apr-1", "", "", "мобильник", "", "", "4", "117"],
        ]
        with (
            patch("dinary.services.sync.get_sheet") as mock_sheet,
            patch("dinary.services.sync.find_month_range", return_value=(2, 2)),
            patch("dinary.services.sync.find_category_row", return_value=2),
            patch("dinary.services.sync.get_month_rate", return_value="117"),
            patch("dinary.services.sync.append_to_rsd_formula"),
            patch("dinary.services.sync.append_comment") as mock_comment,
        ):
            ws_mock = MagicMock()
            ws_mock.get_all_values.return_value = all_values
            mock_sheet.return_value.sheet1 = ws_mock

            _sync_single_row(2026, 4, "мобильник", "", 400.0, "", date(2026, 4, 16))

            mock_comment.assert_not_called()

    def test_creates_month_when_missing(self, populated_db):
        """If the month block doesn't exist, it's created before appending."""
        with (
            patch("dinary.services.sync.get_sheet") as mock_sheet,
            patch("dinary.services.sync.find_month_range", side_effect=[None, (2, 30)]),
            patch("dinary.services.sync.create_month_rows") as mock_create,
            patch("dinary.services.sync.find_category_row", return_value=2),
            patch("dinary.services.sync.get_month_rate", return_value="117"),
            patch("dinary.services.sync.append_to_rsd_formula"),
        ):
            ws_mock = MagicMock()
            ws_mock.get_all_values.return_value = [["header"]]
            mock_sheet.return_value.sheet1 = ws_mock

            _sync_single_row(2026, 5, "еда&бытовые", "собака", 100.0, "", date(2026, 5, 1))

            mock_create.assert_called_once()

    def test_writes_rate_when_missing(self, populated_db):
        """Exchange rate is written when cell is empty."""
        from dinary.services.sheets import COL_RATE_EUR

        with (
            patch("dinary.services.sync.get_sheet") as mock_sheet,
            patch("dinary.services.sync.find_month_range", return_value=(2, 10)),
            patch("dinary.services.sync.find_category_row", return_value=2),
            patch("dinary.services.sync.get_month_rate", return_value=None),
            patch("dinary.services.sync.append_to_rsd_formula"),
        ):
            ws_mock = MagicMock()
            ws_mock.get_all_values.return_value = [["header"]]
            mock_sheet.return_value.sheet1 = ws_mock

            _sync_single_row(2026, 4, "еда&бытовые", "собака", 100.0, "", date(2026, 4, 1), rate=Decimal("117.5"))

            ws_mock.update_cell.assert_any_call(2, COL_RATE_EUR, "117.5")

    def test_preserves_dirty_job_after_write(self, populated_db):
        """Dirty sync job must survive single-row sync so inv sync can do a full rebuild."""
        con = duckdb_repo.get_budget_connection(2026)
        try:
            assert (2026, 4) in duckdb_repo.get_dirty_sync_jobs(con)
        finally:
            duckdb_repo.close_connection(con)

        with (
            patch("dinary.services.sync.get_sheet") as mock_sheet,
            patch("dinary.services.sync.find_month_range", return_value=(2, 30)),
            patch("dinary.services.sync.find_category_row", return_value=2),
            patch("dinary.services.sync.get_month_rate", return_value="117"),
            patch("dinary.services.sync.append_to_rsd_formula"),
        ):
            ws_mock = MagicMock()
            ws_mock.get_all_values.return_value = [["header"]]
            mock_sheet.return_value.sheet1 = ws_mock

            _sync_single_row(2026, 4, "еда&бытовые", "собака", 100.0, "", date(2026, 4, 17))

        con = duckdb_repo.get_budget_connection(2026)
        try:
            assert (2026, 4) in duckdb_repo.get_dirty_sync_jobs(con)
        finally:
            duckdb_repo.close_connection(con)


@allure.epic("Sync")
@allure.feature("Targeted Row Sync")
class TestScheduleSyncWiring:
    @pytest.mark.anyio(loop_scope="function")
    @pytest.mark.parametrize("anyio_backend", ["asyncio"], indirect=True)
    async def test_async_sync_row_calls_sync_single_row(self, populated_db, anyio_backend):
        """_async_sync_row must delegate to _sync_single_row with correct args."""
        from dinary.services.sync import _async_sync_row

        with (
            patch("dinary.services.sync._sync_single_row") as mock_single,
            patch("dinary.services.sync.fetch_eur_rsd_rate", side_effect=Exception("skip")),
            patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)),
        ):
            await _async_sync_row(
                2026, 4, "еда&бытовые", "собака", 500.0, "test", date(2026, 4, 17),
            )

            mock_single.assert_called_once()
            args = mock_single.call_args[0]
            assert args[0] == 2026
            assert args[1] == 4
            assert args[2] == "еда&бытовые"
            assert args[3] == "собака"
            assert args[4] == 500.0
            assert args[5] == "test"

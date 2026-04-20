"""Tests for the queue-based, append-only sheet logging layer (3D)."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import allure
import duckdb
import pytest

from dinary.config import settings
from dinary.services import duckdb_repo, sheet_logging


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "CONFIG_DB", tmp_path / "config.duckdb")
    monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "test-spreadsheet-id")


@pytest.fixture
def setup():
    duckdb_repo.init_config_db()
    con = duckdb_repo.get_config_connection(read_only=False)
    try:
        con.execute("INSERT INTO category_groups VALUES (1, 'g', 1)")
        con.execute("INSERT INTO categories VALUES (1, 'еда', 1)")
        con.execute(
            "INSERT INTO import_sources"
            " (year, spreadsheet_id, worksheet_name, layout_key, notes)"
            " VALUES (2026, 'sheet-id', 'Sheet1', 'default', NULL)",
        )
        con.execute(
            "INSERT INTO import_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (1, 2026, 'Food', 'Essentials', 1, NULL)",
        )
        con.execute(
            "INSERT INTO logging_mapping (id, category_id, event_id,"
            " sheet_category, sheet_group) VALUES (1, 1, NULL, 'Food', 'Essentials')",
        )
    finally:
        con.close()

    bcon = duckdb_repo.get_budget_connection(2026)
    try:
        duckdb_repo.insert_expense(
            bcon,
            expense_id="exp1",
            expense_datetime=datetime(2026, 4, 14, 10),
            amount=12.0,
            amount_original=1500.0,
            currency_original="RSD",
            category_id=1,
            event_id=None,
            comment="lunch",
            sheet_category=None,
            sheet_group=None,
            tag_ids=[],
            enqueue_logging=True,
        )
    finally:
        bcon.close()


def _patch_sheet_io(values=None):
    """Common patches for the sheet I/O surface in `_drain_one_job`.

    Returns a list of `patch` context managers; tests stack them as needed.
    """
    return values


@allure.epic("SheetLogging")
@allure.feature("drain_pending")
class TestDrainPending:
    @patch("dinary.services.sheet_logging.get_sheet")
    @patch("dinary.services.sheet_logging._fetch_rate_blocking", return_value=None)
    @patch("dinary.services.sheet_logging.find_month_range", return_value=(2, 5))
    @patch("dinary.services.sheet_logging.get_month_rate", return_value="117.0")
    @patch("dinary.services.sheet_logging.ensure_category_row")
    @patch("dinary.services.sheet_logging.append_expense_atomic", return_value=True)
    def test_drains_pending_job(
        self,
        _aea,
        mock_ecr,
        _gmr,
        _fmr,
        _fr,
        mock_sheet,
        setup,
    ):
        ws = MagicMock()
        values = [["header"], ["row1"], ["row2"], ["row3"]]
        ws.get_all_values.return_value = values
        mock_sheet.return_value.worksheet.return_value = ws
        mock_sheet.return_value.sheet1 = ws
        mock_ecr.return_value = (3, values)

        result = sheet_logging.drain_pending()

        assert result["years"] == 1
        assert result["attempted"] == 1
        assert result["appended"] == 1
        assert result["already_logged"] == 0
        assert result["failed"] == 0
        assert result["recovered_with_duplicate"] == 0
        assert result["noop_orphan"] == 0

        bcon = duckdb_repo.get_budget_connection(2026)
        try:
            assert duckdb_repo.list_logging_jobs(bcon) == []
        finally:
            bcon.close()


@allure.epic("SheetLogging")
@allure.feature("drain_pending (category fallback)")
class TestDrainPendingCategoryFallback:
    """The sheet logging worker must use the category name as a guaranteed fallback
    when logging_mapping has no row for the category."""

    @patch("dinary.services.sheet_logging.get_sheet")
    @patch("dinary.services.sheet_logging._fetch_rate_blocking", return_value=None)
    @patch("dinary.services.sheet_logging.find_month_range", return_value=(2, 5))
    @patch("dinary.services.sheet_logging.get_month_rate", return_value="117.0")
    @patch("dinary.services.sheet_logging.ensure_category_row")
    @patch("dinary.services.sheet_logging.append_expense_atomic", return_value=True)
    def test_category_name_fallback_when_no_mapping(
        self,
        _aea,
        mock_ecr,
        _gmr,
        _fmr,
        _fr,
        mock_sheet,
        setup,
    ):
        """Remove the logging_mapping row so logging_projection returns None.
        The worker must fall back to (category.name, '') and still append."""
        con = duckdb_repo.get_config_connection(read_only=False)
        try:
            con.execute("DELETE FROM logging_mapping_tags")
            con.execute("DELETE FROM logging_mapping")
        finally:
            con.close()

        ws = MagicMock()
        values = [["header"], ["row1"], ["row2"], ["row3"]]
        ws.get_all_values.return_value = values
        mock_sheet.return_value.worksheet.return_value = ws
        mock_sheet.return_value.sheet1 = ws
        mock_ecr.return_value = (3, values)

        result = sheet_logging.drain_pending()

        assert result["appended"] == 1
        assert result["failed"] == 0

        ecr_call_args = mock_ecr.call_args
        assert ecr_call_args[0][2] == 4
        assert ecr_call_args[0][3] == "еда"
        assert ecr_call_args[0][4] == ""


@allure.epic("SheetLogging")
@allure.feature("schedule_logging")
def test_schedule_logging_no_loop_is_safe():
    """schedule_logging with logging enabled but no event loop just logs."""
    sheet_logging.schedule_logging("anything", 2026)


@allure.epic("SheetLogging")
@allure.feature("_drain_one_job")
class TestDrainOneJobConnectionLifecycle:
    """Regression tests: every early-return path in `_drain_one_job` must
    close its budget connection. Previously three branches (no claim, no
    expense, no projection target) leaked the connection because `con.close()`
    only ran inside a `finally` attached to the second `try` block."""

    def test_missing_expense_closes_connection(self, setup):
        """Queue row whose expense lookup returns None -> clear the row,
        return `NOOP_ORPHAN` (Nit J7: distinct from APPENDED so the
        operational metric reflects only actual Sheets writes), and (the
        bug we're guarding against) close the budget connection so the
        underlying file is not held open. Real DuckDB FKs prevent this
        state via DELETE, so we simulate it by patching
        `get_expense_by_id` to return None."""
        opened: list[object] = []
        real_get_budget = duckdb_repo.get_budget_connection

        def tracking_get_budget(year):
            con = real_get_budget(year)
            opened.append(con)
            return con

        with (
            patch.object(duckdb_repo, "get_budget_connection", tracking_get_budget),
            patch.object(duckdb_repo, "get_expense_by_id", return_value=None),
        ):
            result = sheet_logging._drain_one_job("exp1", 2026)

        assert result is sheet_logging.DrainResult.NOOP_ORPHAN
        assert opened, "expected _drain_one_job to open a budget connection"
        with pytest.raises(duckdb.ConnectionException):
            opened[-1].execute("SELECT 1")

    def test_no_target_closes_connection(self, setup):
        """Forward projection returning None must release the claim AND
        close the budget connection."""
        opened: list[object] = []
        real_get_budget = duckdb_repo.get_budget_connection

        def tracking_get_budget(year):
            con = real_get_budget(year)
            opened.append(con)
            return con

        with (
            patch.object(duckdb_repo, "get_budget_connection", tracking_get_budget),
            patch.object(duckdb_repo, "logging_projection", return_value=None),
            patch.object(duckdb_repo, "get_category_name", return_value=None),
        ):
            result = sheet_logging._drain_one_job("exp1", 2026)

        assert result is sheet_logging.DrainResult.FAILED
        assert opened
        with pytest.raises(duckdb.ConnectionException):
            opened[-1].execute("SELECT 1")

    def test_unclaimable_job_closes_connection(self, setup):
        """A queue row whose claim cannot be acquired (already in flight by
        another worker, or row deleted under us) must still close the
        connection."""
        opened: list[object] = []
        real_get_budget = duckdb_repo.get_budget_connection

        def tracking_get_budget(year):
            con = real_get_budget(year)
            opened.append(con)
            return con

        with (
            patch.object(duckdb_repo, "get_budget_connection", tracking_get_budget),
            patch.object(duckdb_repo, "claim_logging_job", return_value=None),
        ):
            result = sheet_logging._drain_one_job("exp1", 2026)

        assert result is sheet_logging.DrainResult.FAILED
        assert opened
        with pytest.raises(duckdb.ConnectionException):
            opened[-1].execute("SELECT 1")


@allure.epic("SheetLogging")
@allure.feature("_drain_one_job (post-append claim-stolen recovery)")
class TestDrainOneJobClaimStolen:
    """Bug NN + AAA regression: when `clear_logging_job` returns False
    after we already appended to Sheets, we must:

    1. Force-delete the queue row (so the next sweep can't trigger a
       *third* append) — Bug NN.
    2. Surface the outcome as `RECOVERED_WITH_DUPLICATE` so the sweep
       summary distinguishes "audit Sheets" from "retry pending" — Bug AAA.
    """

    @patch("dinary.services.sheet_logging.get_sheet")
    @patch("dinary.services.sheet_logging._fetch_rate_blocking", return_value=None)
    @patch("dinary.services.sheet_logging.find_month_range", return_value=(2, 5))
    @patch("dinary.services.sheet_logging.get_month_rate", return_value="117.0")
    @patch("dinary.services.sheet_logging.ensure_category_row")
    @patch("dinary.services.sheet_logging.append_expense_atomic", return_value=True)
    def test_force_delete_after_stolen_claim(
        self,
        _aea,
        mock_ecr,
        _gmr,
        _fmr,
        _fr,
        mock_sheet,
        setup,
    ):
        ws = MagicMock()
        values = [["header"], ["row1"], ["row2"], ["row3"]]
        ws.get_all_values.return_value = values
        mock_sheet.return_value.worksheet.return_value = ws
        mock_sheet.return_value.sheet1 = ws
        mock_ecr.return_value = (3, values)

        # `clear_logging_job` returns False -> our claim_token didn't
        # match (stolen). `force_clear_logging_job` runs against the real
        # DB and finds the row (the in-test setup left it
        # `pending`/`in_progress` under our token), so it returns True ->
        # the simulated thief branch.
        with patch.object(duckdb_repo, "clear_logging_job", return_value=False):
            result = sheet_logging._drain_one_job("exp1", 2026)

        # Bug AAA: surface the abnormal flow as a distinct outcome, not
        # conflated with FAILED (which means "needs retry").
        assert result is sheet_logging.DrainResult.RECOVERED_WITH_DUPLICATE
        # Bug NN: queue row must be deleted regardless of claim_token
        # mismatch, to prevent a third sweep + third append.
        bcon = duckdb_repo.get_budget_connection(2026)
        try:
            assert duckdb_repo.list_logging_jobs(bcon) == []
        finally:
            bcon.close()

    @patch("dinary.services.sheet_logging.get_sheet")
    @patch("dinary.services.sheet_logging._fetch_rate_blocking", return_value=None)
    @patch("dinary.services.sheet_logging.find_month_range", return_value=(2, 5))
    @patch("dinary.services.sheet_logging.get_month_rate", return_value="117.0")
    @patch("dinary.services.sheet_logging.ensure_category_row")
    @patch("dinary.services.sheet_logging.append_expense_atomic", return_value=True)
    def test_recovered_when_row_already_gone(
        self,
        _aea,
        mock_ecr,
        _gmr,
        _fmr,
        _fr,
        mock_sheet,
        setup,
    ):
        """Operator-wipe sub-case: the queue row was deleted out from
        under us mid-append (no thief, no duplicate). Both
        `clear_logging_job` and `force_clear_logging_job` find nothing,
        but we still surface the outcome as RECOVERED_WITH_DUPLICATE
        because we cannot distinguish this case from a stolen claim and
        the safe direction is to over-warn (cost = a sheet audit; vs.
        silently leaking a duplicate)."""
        ws = MagicMock()
        values = [["header"], ["row1"], ["row2"], ["row3"]]
        ws.get_all_values.return_value = values
        mock_sheet.return_value.worksheet.return_value = ws
        mock_sheet.return_value.sheet1 = ws
        mock_ecr.return_value = (3, values)

        with (
            patch.object(duckdb_repo, "clear_logging_job", return_value=False),
            patch.object(duckdb_repo, "force_clear_logging_job", return_value=False),
        ):
            result = sheet_logging._drain_one_job("exp1", 2026)

        assert result is sheet_logging.DrainResult.RECOVERED_WITH_DUPLICATE


@allure.epic("SheetLogging")
@allure.feature("_drain_one_job (return contract)")
class TestDrainOneJobReturnContract:
    """Bug J1 regression: every documented `DrainResult` must actually
    be returned by the corresponding code path — bare `bool`s slipping
    through would defeat downstream `match`/`if-elif` branching over
    the enum (and were the original Bug J1)."""

    @patch("dinary.services.sheet_logging.get_sheet")
    @patch("dinary.services.sheet_logging._fetch_rate_blocking", return_value=None)
    @patch("dinary.services.sheet_logging.find_month_range", return_value=(2, 5))
    @patch("dinary.services.sheet_logging.get_month_rate", return_value="117.0")
    @patch("dinary.services.sheet_logging.ensure_category_row")
    @patch(
        "dinary.services.sheet_logging.append_expense_atomic",
        side_effect=RuntimeError("simulated sheet failure"),
    )
    def test_append_failure_returns_drain_result_failed(
        self,
        _aea,
        mock_ecr,
        _gmr,
        _fmr,
        _fr,
        mock_sheet,
        setup,
    ):
        """The Sheets-append-failure branch must return
        `DrainResult.FAILED`, not bare `False`."""
        ws = MagicMock()
        values = [["header"], ["row1"], ["row2"], ["row3"]]
        ws.get_all_values.return_value = values
        mock_sheet.return_value.worksheet.return_value = ws
        mock_sheet.return_value.sheet1 = ws
        mock_ecr.return_value = (3, values)

        result = sheet_logging._drain_one_job("exp1", 2026)

        assert result is sheet_logging.DrainResult.FAILED, (
            f"expected DrainResult.FAILED, got {result!r}"
        )
        # Queue row should remain `pending` (claim released) for the
        # next sweep to retry.
        bcon = duckdb_repo.get_budget_connection(2026)
        try:
            assert duckdb_repo.list_logging_jobs(bcon) == ["exp1"]
        finally:
            bcon.close()


@allure.epic("SheetLogging")
@allure.feature("drain_pending (counter accounting)")
class TestDrainPendingCounters:
    """Bug AAA regression: `drain_pending` must split clean appends,
    real failures, and post-append-recovery into three distinct counters
    so an operator scanning the summary can tell "needs retry" from
    "audit the sheet for duplicates"."""

    @patch("dinary.services.sheet_logging.get_sheet")
    @patch("dinary.services.sheet_logging._fetch_rate_blocking", return_value=None)
    @patch("dinary.services.sheet_logging.find_month_range", return_value=(2, 5))
    @patch("dinary.services.sheet_logging.get_month_rate", return_value="117.0")
    @patch("dinary.services.sheet_logging.ensure_category_row")
    @patch("dinary.services.sheet_logging.append_expense_atomic", return_value=True)
    def test_recovered_with_duplicate_increments_dedicated_counter(
        self,
        _aea,
        mock_ecr,
        _gmr,
        _fmr,
        _fr,
        mock_sheet,
        setup,
    ):
        ws = MagicMock()
        values = [["header"], ["row1"], ["row2"], ["row3"]]
        ws.get_all_values.return_value = values
        mock_sheet.return_value.worksheet.return_value = ws
        mock_sheet.return_value.sheet1 = ws
        mock_ecr.return_value = (3, values)

        with patch.object(duckdb_repo, "clear_logging_job", return_value=False):
            result = sheet_logging.drain_pending()

        assert result["appended"] == 0, "must not double-count as appended"
        assert result["failed"] == 0, (
            "must not conflate a successful append with a real failure that needs retry"
        )
        assert result["recovered_with_duplicate"] == 1


@allure.epic("SheetLogging")
@allure.feature("F0 idempotency marker")
class TestF0IdempotencyMarker:
    """Marker-based idempotency: when ``append_expense_atomic`` returns
    False (existing marker on the row), the drain must NOT count it as
    APPENDED but as ALREADY_LOGGED, and must still clear the queue row.
    """

    @patch("dinary.services.sheet_logging.get_sheet")
    @patch("dinary.services.sheet_logging._fetch_rate_blocking", return_value=None)
    @patch("dinary.services.sheet_logging.find_month_range", return_value=(2, 5))
    @patch("dinary.services.sheet_logging.get_month_rate", return_value="117.0")
    @patch("dinary.services.sheet_logging.ensure_category_row")
    @patch("dinary.services.sheet_logging.append_expense_atomic", return_value=False)
    def test_marker_present_returns_already_logged_and_clears_queue(
        self,
        _aea,
        mock_ecr,
        _gmr,
        _fmr,
        _fr,
        mock_sheet,
        setup,
    ):
        """Simulates the timeout-after-success retry: the marker is
        already on the row, so ``append_expense_atomic`` returns False
        without writing. The drain must still clear the queue row AND
        report ALREADY_LOGGED so the operational counter doesn't
        over-state actual sheet writes."""
        ws = MagicMock()
        values = [["header"], ["row1"], ["row2"], ["row3"]]
        ws.get_all_values.return_value = values
        mock_sheet.return_value.worksheet.return_value = ws
        mock_sheet.return_value.sheet1 = ws
        mock_ecr.return_value = (3, values)

        result = sheet_logging.drain_pending()

        assert result["appended"] == 0
        assert result["already_logged"] == 1
        assert result["failed"] == 0
        assert result["recovered_with_duplicate"] == 0

        bcon = duckdb_repo.get_budget_connection(2026)
        try:
            assert duckdb_repo.list_logging_jobs(bcon) == []
        finally:
            bcon.close()


@allure.epic("SheetLogging")
@allure.feature("Year-aware insert: rate-write alignment")
class TestAppendRowYearListSplice:
    """Regression: when ``ensure_category_row`` inserts a new
    ``(year, month)`` block, ``_append_row_to_sheet`` must splice the
    new year into ``years_by_row`` so the follow-up
    ``get_month_rate`` / ``find_month_range`` calls operate on a list
    aligned with the post-insert ``all_values``.

    Without the splice, the trailing rows of the unsplit list become
    out-of-bounds — ``_row_year_matches`` then treats them as
    "year unknown → wildcard match", which can either silently skip
    the new row's rate write **or** redirect it onto another year's
    rate cell. The latter is the corruption case this test pins down.
    """

    HEADER = ["Date", "", "Sum", "Category", "Group", "Comment", "Month", "Euro"]

    @patch("dinary.services.sheet_logging.append_expense_atomic", return_value=True)
    @patch("dinary.services.sheet_logging.get_sheet")
    def test_new_year_block_rate_does_not_corrupt_existing_year(self, mock_get_sheet, _aea):
        from datetime import date
        from decimal import Decimal

        from dinary.services.sheets import COL_RATE_EUR

        # Two existing same-month blocks for two different years, both
        # missing a rate — so the rate-write branch is forced to fire.
        existing = [
            self.HEADER,
            ["Apr-1", "1000", "9", "Food", "Essentials", "", "4", ""],
            ["Apr-1", "5000", "43", "Food", "Essentials", "", "4", ""],
        ]
        # Sheets serials (1899-12-30 epoch) for the dates Google would
        # show as "Apr-1" in those rows. Hard-pinned literals — if the
        # epoch shifts (or someone "simplifies" _SHEETS_EPOCH), this
        # test fails before the year-list logic gets a chance to.
        a_2027 = 46478  # date(2027, 4, 1) - date(1899, 12, 30)
        a_2026 = 46113  # date(2026, 4, 1) - date(1899, 12, 30)
        assert a_2027 == (date(2027, 4, 1) - date(1899, 12, 30)).days
        assert a_2026 == (date(2026, 4, 1) - date(1899, 12, 30)).days

        # After ensure_category_row inserts Apr 2028 at the top.
        refreshed = [
            self.HEADER,
            ["2028-04-01", "", "", "Food", "Essentials", "", "4", ""],
            existing[1],
            existing[2],
        ]

        ws = MagicMock()
        # 1st call inside _append_row_to_sheet, 2nd inside ensure_category_row.
        ws.get_all_values.side_effect = [existing, refreshed]
        # batch_get for column A — only the pre-insert 3 rows.
        ws.batch_get.return_value = [[["Date"], [a_2027], [a_2026]]]
        mock_get_sheet.return_value.sheet1 = ws

        sheet_logging._append_row_to_sheet(
            spreadsheet_id="sid",
            expense_id="exp-2028",
            month=4,
            sheet_category="Food",
            sheet_group="Essentials",
            amount=2500.0,
            comment="",
            expense_date=date(2028, 4, 1),
            rate=Decimal("130"),
        )

        rate_writes = [
            call for call in ws.update_cell.call_args_list if call.args[1] == COL_RATE_EUR
        ]
        assert len(rate_writes) == 1, (
            f"expected exactly one rate write, got {ws.update_cell.call_args_list}"
        )
        target_row, _, value = rate_writes[0].args
        assert target_row == 2, (
            "rate must land on the newly inserted 2028 row (row 2), not on a "
            f"shifted neighbour (got row {target_row})"
        )
        assert value == "130"


@allure.epic("SheetLogging")
@allure.feature("sheet logging disabled")
class TestSheetLoggingDisabled:
    """When DINARY_SHEET_LOGGING_SPREADSHEET is empty, everything is a no-op."""

    def test_schedule_logging_noop_when_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "")
        sheet_logging.schedule_logging("anything", 2026)

    def test_drain_pending_returns_disabled(self, setup, monkeypatch):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "")
        result = sheet_logging.drain_pending()
        assert result == {"disabled": True}

    def test_drain_returns_failed_when_disabled(self, setup, monkeypatch):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "")
        result = sheet_logging._drain_one_job("exp1", 2026)
        assert result is sheet_logging.DrainResult.FAILED


@allure.epic("DuckDB")
@allure.feature("claim_logging_job (TransactionException handling)")
class TestClaimLoggingJobTransactionConflict:
    """Concern GG regression: a `duckdb.TransactionException` raised by
    DuckDB's optimistic-concurrency layer when two workers race on the
    same row must surface as a clean `None` return (not a noisy
    re-raise). The caller treats `None` as "skip this row, the winner
    will handle it"."""

    def test_transaction_exception_returns_none(self, setup):
        bcon = duckdb_repo.get_budget_connection(2026)
        try:

            class _Exploding:
                """DuckDB connection wrapper that raises TransactionException
                on the SELECT inside `claim_logging_job` so the caught
                branch fires deterministically. We can't easily provoke a
                real conflict from a single-threaded test, so we simulate
                the exception path."""

                def __init__(self, real):
                    self._real = real
                    self._calls = 0

                def execute(self, sql, *args, **kwargs):
                    self._calls += 1
                    # First call is BEGIN, second is the SELECT we want
                    # to fail. After that ROLLBACK should be passed
                    # through to the real connection.
                    if self._calls == 2:  # noqa: PLR2004
                        raise duckdb.TransactionException("simulated conflict")
                    return self._real.execute(sql, *args, **kwargs)

                def __getattr__(self, name):
                    return getattr(self._real, name)

            exploding = _Exploding(bcon)
            token = duckdb_repo.claim_logging_job(exploding, "exp1")
            assert token is None
        finally:
            bcon.close()

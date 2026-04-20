"""Tests for the queue-based, append-only sheet logging layer (3D)."""

from datetime import date, datetime
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


# ---------------------------------------------------------------------------
# Rate-limit, TTL, and year-window tests for drain_pending
# ---------------------------------------------------------------------------


def _insert_expense_with_date(year: int, expense_id: str, expense_date: date) -> None:
    """Insert a minimal expense + queue row into budget_<year>.duckdb."""
    bcon = duckdb_repo.get_budget_connection(year)
    try:
        duckdb_repo.insert_expense(
            bcon,
            expense_id=expense_id,
            expense_datetime=datetime(expense_date.year, expense_date.month, expense_date.day, 10),
            amount=10.0,
            amount_original=10.0,
            currency_original="EUR",
            category_id=1,
            event_id=None,
            comment="",
            sheet_category=None,
            sheet_group=None,
            tag_ids=[],
            enqueue_logging=True,
        )
    finally:
        bcon.close()


def _count_pending(year: int) -> int:
    """Return the number of rows still in sheet_logging_jobs for a year."""
    bcon = duckdb_repo.get_budget_connection(year)
    try:
        return duckdb_repo.count_logging_jobs(bcon)
    finally:
        bcon.close()


@allure.epic("SheetLogging")
@allure.feature("drain_pending rate-limit and TTL")
class TestDrainRateLimit:
    """Exercise the rate-limiting, TTL, and year-window behaviour added
    alongside the in-process periodic drain loop.
    """

    @patch("dinary.services.sheet_logging._drain_one_job")
    def test_cap_honored(self, mock_drain_one, setup, monkeypatch):
        """Hard cap stops the sweep after max_attempts."""
        monkeypatch.setattr(settings, "sheet_logging_drain_max_attempts_per_iteration", 5)
        monkeypatch.setattr(settings, "sheet_logging_drain_inter_row_delay_sec", 0)
        monkeypatch.setattr(settings, "sheet_logging_drain_max_age_days", 90)

        for i in range(25):
            _insert_expense_with_date(2026, f"cap-{i:03d}", date(2026, 6, 1 + i % 25))

        mock_drain_one.return_value = sheet_logging.DrainResult.APPENDED
        summary = sheet_logging.drain_pending(today=date(2026, 6, 15))

        assert mock_drain_one.call_count == 5
        assert summary["cap_reached"] is True
        assert summary["attempted"] == 5
        # exp1 from setup + 20 untouched = 21 pending (5 drained + queue-cleared)
        # Actually _drain_one_job is mocked so the queue rows are NOT cleared
        assert _count_pending(2026) == 26  # 25 + 1 from setup

    @patch("dinary.services.sheet_logging._drain_one_job")
    def test_inter_row_sleep_observed(self, mock_drain_one, setup, monkeypatch):
        """Sleep is called between attempts (before each except the first)."""
        monkeypatch.setattr(settings, "sheet_logging_drain_max_attempts_per_iteration", 10)
        monkeypatch.setattr(settings, "sheet_logging_drain_inter_row_delay_sec", 0.001)
        monkeypatch.setattr(settings, "sheet_logging_drain_max_age_days", 90)

        for i in range(3):
            _insert_expense_with_date(2026, f"sleep-{i}", date(2026, 6, 1 + i))

        mock_drain_one.return_value = sheet_logging.DrainResult.APPENDED
        sleep_mock = MagicMock()
        monkeypatch.setattr(sheet_logging.time, "sleep", sleep_mock)

        sheet_logging.drain_pending(today=date(2026, 6, 15))

        # exp1 from setup + 3 new = 4 total attempts; sleep before 2nd, 3rd, 4th
        assert sleep_mock.call_count == 3
        for call in sleep_mock.call_args_list:
            assert call.args[0] == 0.001

    @patch("dinary.services.sheet_logging._drain_one_job")
    def test_inter_row_sleep_cross_year(self, mock_drain_one, setup, monkeypatch):
        """Sleep also happens at the year boundary (between last row of
        year A and first row of year B)."""
        monkeypatch.setattr(settings, "sheet_logging_drain_max_attempts_per_iteration", 20)
        monkeypatch.setattr(settings, "sheet_logging_drain_inter_row_delay_sec", 0.001)
        monkeypatch.setattr(settings, "sheet_logging_drain_max_age_days", 90)

        _insert_expense_with_date(2026, "cross-2026-a", date(2026, 4, 1))
        _insert_expense_with_date(2025, "cross-2025-a", date(2025, 12, 20))
        _insert_expense_with_date(2025, "cross-2025-b", date(2025, 12, 21))

        mock_drain_one.return_value = sheet_logging.DrainResult.APPENDED
        sleep_mock = MagicMock()
        monkeypatch.setattr(sheet_logging.time, "sleep", sleep_mock)

        # today=2026-01-15 → cutoff=2025-10-17 → both 2026 and 2025 in window
        sheet_logging.drain_pending(today=date(2026, 1, 15))

        # 2026: exp1 (setup) + cross-2026-a = 2 rows
        # 2025: cross-2025-a + cross-2025-b = 2 rows
        # Total: 4 attempts; sleep before 2nd, 3rd, 4th
        assert sleep_mock.call_count == 3

    @patch("dinary.services.sheet_logging._drain_one_job")
    def test_ttl_skips_old_expenses(self, mock_drain_one, setup, monkeypatch):
        """TTL filter skips rows older than max_age_days in the same year DB."""
        monkeypatch.setattr(settings, "sheet_logging_drain_max_attempts_per_iteration", 100)
        monkeypatch.setattr(settings, "sheet_logging_drain_inter_row_delay_sec", 0)
        monkeypatch.setattr(settings, "sheet_logging_drain_max_age_days", 90)

        # Fresh rows (within 90 days of 2026-06-15)
        _insert_expense_with_date(2026, "fresh-1", date(2026, 6, 1))
        _insert_expense_with_date(2026, "fresh-2", date(2026, 6, 10))

        # Old rows (older than 90 days but same year DB)
        _insert_expense_with_date(2026, "old-1", date(2026, 1, 5))
        _insert_expense_with_date(2026, "old-2", date(2026, 1, 6))
        _insert_expense_with_date(2026, "old-3", date(2026, 1, 7))

        mock_drain_one.return_value = sheet_logging.DrainResult.APPENDED

        summary = sheet_logging.drain_pending(today=date(2026, 6, 15))

        # exp1 from setup is dated 2026-04-14 → within 90 days of 2026-06-15
        assert mock_drain_one.call_count == 3  # exp1 + fresh-1 + fresh-2
        assert summary["skipped_expired"] == 3  # old-1, old-2, old-3

    @patch("dinary.services.sheet_logging._drain_one_job")
    def test_year_window_restriction(self, mock_drain_one, setup, monkeypatch):
        """Year-window prevents opening budget DBs far outside the TTL range."""
        monkeypatch.setattr(settings, "sheet_logging_drain_max_attempts_per_iteration", 100)
        monkeypatch.setattr(settings, "sheet_logging_drain_inter_row_delay_sec", 0)
        monkeypatch.setattr(settings, "sheet_logging_drain_max_age_days", 90)

        # Create a DB for 2020 with pending rows
        _insert_expense_with_date(2020, "old-year-1", date(2020, 6, 1))
        _insert_expense_with_date(2020, "old-year-2", date(2020, 6, 2))

        budget_path_calls = []
        original_budget_path = duckdb_repo.budget_path

        def _recording_budget_path(year):
            budget_path_calls.append(year)
            return original_budget_path(year)

        iter_calls = []
        original_iter = duckdb_repo.iter_budget_years

        def _recording_iter():
            iter_calls.append(True)
            return original_iter()

        monkeypatch.setattr(duckdb_repo, "budget_path", _recording_budget_path)
        monkeypatch.setattr(duckdb_repo, "iter_budget_years", _recording_iter)

        mock_drain_one.return_value = sheet_logging.DrainResult.APPENDED

        summary = sheet_logging.drain_pending(today=date(2026, 6, 15))

        # Only 2026 should be visited; 2020 should never be opened
        assert 2020 not in budget_path_calls
        assert 2026 in budget_path_calls
        assert len(iter_calls) == 0  # TTL>0, so hardcoded path, no glob
        assert summary["skipped_expired"] == 0  # 2020 DB never opened

    @patch("dinary.services.sheet_logging._drain_one_job")
    def test_ttl_zero_disables_filter(self, mock_drain_one, setup, monkeypatch):
        """TTL=0 visits all years and does not skip any rows."""
        monkeypatch.setattr(settings, "sheet_logging_drain_max_attempts_per_iteration", 100)
        monkeypatch.setattr(settings, "sheet_logging_drain_inter_row_delay_sec", 0)
        monkeypatch.setattr(settings, "sheet_logging_drain_max_age_days", 0)

        # Old rows in same year
        _insert_expense_with_date(2026, "old-ttl0-1", date(2026, 1, 5))
        _insert_expense_with_date(2026, "old-ttl0-2", date(2026, 1, 6))
        # Fresh rows
        _insert_expense_with_date(2026, "fresh-ttl0-1", date(2026, 6, 1))

        iter_calls = []
        original_iter = duckdb_repo.iter_budget_years

        def _recording_iter():
            iter_calls.append(True)
            return original_iter()

        monkeypatch.setattr(duckdb_repo, "iter_budget_years", _recording_iter)
        mock_drain_one.return_value = sheet_logging.DrainResult.APPENDED

        summary = sheet_logging.drain_pending(today=date(2026, 6, 15))

        # exp1 from setup + 3 new = 4 total
        assert mock_drain_one.call_count == 4
        assert summary["skipped_expired"] == 0
        assert len(iter_calls) == 1  # TTL=0 uses iter_budget_years

    @patch("dinary.services.sheet_logging._drain_one_job")
    def test_ttl_and_cap_combined(self, mock_drain_one, setup, monkeypatch):
        """Expired rows do not count against the cap."""
        monkeypatch.setattr(settings, "sheet_logging_drain_max_attempts_per_iteration", 3)
        monkeypatch.setattr(settings, "sheet_logging_drain_inter_row_delay_sec", 0)
        monkeypatch.setattr(settings, "sheet_logging_drain_max_age_days", 90)

        # 5 expired rows
        for i in range(5):
            _insert_expense_with_date(2026, f"expired-cap-{i}", date(2026, 1, 1 + i))
        # 10 fresh rows
        for i in range(10):
            _insert_expense_with_date(2026, f"fresh-cap-{i}", date(2026, 6, 1 + i))

        mock_drain_one.return_value = sheet_logging.DrainResult.APPENDED

        summary = sheet_logging.drain_pending(today=date(2026, 6, 15))

        # exp1 from setup is 2026-04-14 → within 90 days → attempted
        # cap=3 → only 3 attempts (all from fresh + exp1)
        assert mock_drain_one.call_count == 3
        assert summary["skipped_expired"] == 5
        assert summary["cap_reached"] is True

    @patch("dinary.services.sheet_logging._drain_one_job")
    def test_orphan_rows_still_drained(self, mock_drain_one, setup, monkeypatch):
        """Orphan queue rows (no matching expense) bypass the TTL filter."""
        monkeypatch.setattr(settings, "sheet_logging_drain_max_attempts_per_iteration", 100)
        monkeypatch.setattr(settings, "sheet_logging_drain_inter_row_delay_sec", 0)
        monkeypatch.setattr(settings, "sheet_logging_drain_max_age_days", 90)

        # Create orphan queue rows: recreate the table without FK so we can
        # insert expense_ids that don't exist in expenses.
        bcon = duckdb_repo.get_budget_connection(2026)
        try:
            bcon.execute("CREATE TABLE _slj_copy AS SELECT * FROM sheet_logging_jobs")
            bcon.execute("DROP TABLE sheet_logging_jobs")
            bcon.execute(
                "CREATE TABLE sheet_logging_jobs ("
                "  expense_id TEXT PRIMARY KEY,"
                "  status TEXT NOT NULL DEFAULT 'pending',"
                "  claim_token TEXT,"
                "  claimed_at TIMESTAMP,"
                "  CHECK (status IN ('pending', 'in_progress'))"
                ")"
            )
            bcon.execute("INSERT INTO sheet_logging_jobs SELECT * FROM _slj_copy")
            bcon.execute("DROP TABLE _slj_copy")
            bcon.execute(
                "INSERT INTO sheet_logging_jobs (expense_id, status) VALUES ('orphan-1', 'pending')"
            )
            bcon.execute(
                "INSERT INTO sheet_logging_jobs (expense_id, status) VALUES ('orphan-2', 'pending')"
            )
        finally:
            bcon.close()

        mock_drain_one.return_value = sheet_logging.DrainResult.NOOP_ORPHAN

        summary = sheet_logging.drain_pending(today=date(2026, 6, 15))

        # exp1 from setup + 2 orphans = 3
        assert mock_drain_one.call_count == 3

    def test_logging_disabled_returns_early(self, monkeypatch):
        """When spreadsheet is unset, drain_pending returns bare disabled dict."""
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "")

        duckdb_repo.init_config_db()
        summary = sheet_logging.drain_pending(today=date(2026, 6, 15))

        assert summary == {"disabled": True}
        assert "attempted" not in summary
        assert "cap_reached" not in summary
        assert "skipped_expired" not in summary

"""Tests for the queue-based, append-only sync layer (3D)."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import allure
import duckdb
import pytest

from dinary.services import duckdb_repo, sync


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "CONFIG_DB", tmp_path / "config.duckdb")
    # The export-target cache is module-level; without invalidation, a
    # second test would read the previous tmp_path's spreadsheet_id.
    sync.invalidate_export_target_cache()


@pytest.fixture
def setup():
    duckdb_repo.init_config_db()
    con = duckdb_repo.get_config_connection(read_only=False)
    try:
        con.execute("INSERT INTO category_groups VALUES (1, 'g', 1)")
        con.execute("INSERT INTO categories VALUES (1, 'еда', 1)")
        con.execute(
            "INSERT INTO sheet_import_sources"
            " (year, spreadsheet_id, worksheet_name, layout_key, notes)"
            " VALUES (2026, 'sheet-id', 'Sheet1', 'default', NULL)",
        )
        con.execute(
            "INSERT INTO sheet_mapping (id, year, sheet_category, sheet_group,"
            " category_id, event_id) VALUES (1, 2026, 'Food', 'Essentials', 1, NULL)",
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
            enqueue_sync=True,
        )
    finally:
        bcon.close()


@allure.epic("Sync")
@allure.feature("sync_pending")
class TestSyncPending:
    @patch("dinary.services.sync.get_sheet")
    @patch("dinary.services.sync._fetch_rate_blocking", return_value=None)
    @patch("dinary.services.sync.find_month_range", return_value=(2, 5))
    @patch("dinary.services.sync.get_month_rate", return_value="117.0")
    @patch("dinary.services.sync.find_category_row", return_value=3)
    @patch("dinary.services.sync.append_to_rsd_formula")
    @patch("dinary.services.sync.append_comment")
    def test_drains_pending_job(
        self,
        _ac,
        _af,
        _fcr,
        _gmr,
        _fmr,
        _fr,
        mock_sheet,
        setup,
    ):
        ws = MagicMock()
        ws.get_all_values.return_value = [["header"], ["row1"], ["row2"], ["row3"]]
        mock_sheet.return_value.worksheet.return_value = ws
        mock_sheet.return_value.sheet1 = ws

        result = sync.sync_pending()

        assert result["years"] == 1
        assert result["attempted"] == 1
        assert result["appended"] == 1
        assert result["failed"] == 0
        assert result["recovered_with_duplicate"] == 0
        # K4: pin the new key so a future change that misclassifies a
        # clean append as orphaned (or vice versa) shows up here instead
        # of slipping through with a green test.
        assert result["noop_orphan"] == 0

        bcon = duckdb_repo.get_budget_connection(2026)
        try:
            assert duckdb_repo.list_sync_jobs(bcon) == []
        finally:
            bcon.close()

    @patch("dinary.services.sync.get_sheet")
    @patch("dinary.services.sync._fetch_rate_blocking", return_value=None)
    @patch("dinary.services.sync.find_month_range", return_value=(2, 5))
    @patch("dinary.services.sync.get_month_rate", return_value="117.0")
    @patch("dinary.services.sync.find_category_row", return_value=None)
    def test_missing_row_keeps_job_pending(
        self,
        _fcr,
        _gmr,
        _fmr,
        _fr,
        mock_sheet,
        setup,
    ):
        ws = MagicMock()
        ws.get_all_values.return_value = [["header"]]
        mock_sheet.return_value.worksheet.return_value = ws
        mock_sheet.return_value.sheet1 = ws

        result = sync.sync_pending()

        assert result["failed"] == 1
        bcon = duckdb_repo.get_budget_connection(2026)
        try:
            assert duckdb_repo.list_sync_jobs(bcon) == ["exp1"]
        finally:
            bcon.close()


@allure.epic("Sync")
@allure.feature("schedule_sync")
def test_schedule_sync_no_loop_is_safe():
    sync.schedule_sync("anything", 2026)


@allure.epic("Sync")
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
            result = sync._drain_one_job("exp1", 2026)

        assert result is sync.DrainResult.NOOP_ORPHAN
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
            patch.object(duckdb_repo, "forward_projection", return_value=None),
        ):
            result = sync._drain_one_job("exp1", 2026)

        assert result is sync.DrainResult.FAILED
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
            patch.object(duckdb_repo, "claim_sync_job", return_value=None),
        ):
            result = sync._drain_one_job("exp1", 2026)

        assert result is sync.DrainResult.FAILED
        assert opened
        with pytest.raises(duckdb.ConnectionException):
            opened[-1].execute("SELECT 1")


@allure.epic("Sync")
@allure.feature("_drain_one_job (post-append claim-stolen recovery)")
class TestDrainOneJobClaimStolen:
    """Bug NN + AAA regression: when `clear_sync_job` returns False after
    we already appended to Sheets, we must:

    1. Force-delete the queue row (so the next sweep can't trigger a
       *third* append) — Bug NN.
    2. Surface the outcome as `RECOVERED_WITH_DUPLICATE` so the sweep
       summary distinguishes "audit Sheets" from "retry pending" — Bug AAA.
    """

    @patch("dinary.services.sync.get_sheet")
    @patch("dinary.services.sync._fetch_rate_blocking", return_value=None)
    @patch("dinary.services.sync.find_month_range", return_value=(2, 5))
    @patch("dinary.services.sync.get_month_rate", return_value="117.0")
    @patch("dinary.services.sync.find_category_row", return_value=3)
    @patch("dinary.services.sync.append_to_rsd_formula")
    @patch("dinary.services.sync.append_comment")
    def test_force_delete_after_stolen_claim(
        self,
        _ac,
        _af,
        _fcr,
        _gmr,
        _fmr,
        _fr,
        mock_sheet,
        setup,
    ):
        ws = MagicMock()
        ws.get_all_values.return_value = [["header"], ["row1"], ["row2"], ["row3"]]
        mock_sheet.return_value.worksheet.return_value = ws
        mock_sheet.return_value.sheet1 = ws

        # `clear_sync_job` returns False -> our claim_token didn't match
        # (stolen). `force_clear_sync_job` runs against the real DB and
        # finds the row (the in-test setup left it `pending`/`in_progress`
        # under our token), so it returns True -> the simulated thief
        # branch.
        with patch.object(duckdb_repo, "clear_sync_job", return_value=False):
            result = sync._drain_one_job("exp1", 2026)

        # Bug AAA: surface the abnormal flow as a distinct outcome, not
        # conflated with FAILED (which means "needs retry").
        assert result is sync.DrainResult.RECOVERED_WITH_DUPLICATE
        # Bug NN: queue row must be deleted regardless of claim_token
        # mismatch, to prevent a third sweep + third append.
        bcon = duckdb_repo.get_budget_connection(2026)
        try:
            assert duckdb_repo.list_sync_jobs(bcon) == []
        finally:
            bcon.close()

    @patch("dinary.services.sync.get_sheet")
    @patch("dinary.services.sync._fetch_rate_blocking", return_value=None)
    @patch("dinary.services.sync.find_month_range", return_value=(2, 5))
    @patch("dinary.services.sync.get_month_rate", return_value="117.0")
    @patch("dinary.services.sync.find_category_row", return_value=3)
    @patch("dinary.services.sync.append_to_rsd_formula")
    @patch("dinary.services.sync.append_comment")
    def test_recovered_when_row_already_gone(
        self,
        _ac,
        _af,
        _fcr,
        _gmr,
        _fmr,
        _fr,
        mock_sheet,
        setup,
    ):
        """Operator-wipe sub-case: the queue row was deleted out from
        under us mid-append (no thief, no duplicate). Both
        `clear_sync_job` and `force_clear_sync_job` find nothing, but we
        still surface the outcome as RECOVERED_WITH_DUPLICATE because we
        cannot distinguish this case from a stolen claim and the safe
        direction is to over-warn (cost = a sheet audit; vs. silently
        leaking a duplicate)."""
        ws = MagicMock()
        ws.get_all_values.return_value = [["header"], ["row1"], ["row2"], ["row3"]]
        mock_sheet.return_value.worksheet.return_value = ws
        mock_sheet.return_value.sheet1 = ws

        with (
            patch.object(duckdb_repo, "clear_sync_job", return_value=False),
            patch.object(duckdb_repo, "force_clear_sync_job", return_value=False),
        ):
            result = sync._drain_one_job("exp1", 2026)

        assert result is sync.DrainResult.RECOVERED_WITH_DUPLICATE


@allure.epic("Sync")
@allure.feature("_drain_one_job (return contract)")
class TestDrainOneJobReturnContract:
    """Bug J1 regression: every documented `DrainResult` must actually
    be returned by the corresponding code path — bare `bool`s slipping
    through would defeat downstream `match`/`if-elif` branching over
    the enum (and were the original Bug J1)."""

    @patch("dinary.services.sync.get_sheet")
    @patch("dinary.services.sync._fetch_rate_blocking", return_value=None)
    @patch("dinary.services.sync.find_month_range", return_value=(2, 5))
    @patch("dinary.services.sync.get_month_rate", return_value="117.0")
    @patch("dinary.services.sync.find_category_row", return_value=3)
    @patch(
        "dinary.services.sync.append_to_rsd_formula",
        side_effect=RuntimeError("simulated sheet failure"),
    )
    def test_append_failure_returns_drain_result_failed(
        self,
        _af,
        _fcr,
        _gmr,
        _fmr,
        _fr,
        mock_sheet,
        setup,
    ):
        """The Sheets-append-failure branch must return
        `DrainResult.FAILED`, not bare `False`. They are currently
        functionally equivalent in `sync_pending`'s if/elif/else, but a
        future `match` over `DrainResult` would leave a `False` return
        silently unhandled."""
        ws = MagicMock()
        ws.get_all_values.return_value = [["header"], ["row1"], ["row2"], ["row3"]]
        mock_sheet.return_value.worksheet.return_value = ws
        mock_sheet.return_value.sheet1 = ws

        result = sync._drain_one_job("exp1", 2026)

        assert result is sync.DrainResult.FAILED, f"expected DrainResult.FAILED, got {result!r}"
        # Queue row should remain `pending` (claim released) for the
        # next sweep to retry.
        bcon = duckdb_repo.get_budget_connection(2026)
        try:
            assert duckdb_repo.list_sync_jobs(bcon) == ["exp1"]
        finally:
            bcon.close()


@allure.epic("Sync")
@allure.feature("sync_pending (counter accounting)")
class TestSyncPendingCounters:
    """Bug AAA regression: `sync_pending` must split clean appends,
    real failures, and post-append-recovery into three distinct counters
    so an operator scanning the summary can tell "needs retry" from
    "audit the sheet for duplicates"."""

    @patch("dinary.services.sync.get_sheet")
    @patch("dinary.services.sync._fetch_rate_blocking", return_value=None)
    @patch("dinary.services.sync.find_month_range", return_value=(2, 5))
    @patch("dinary.services.sync.get_month_rate", return_value="117.0")
    @patch("dinary.services.sync.find_category_row", return_value=3)
    @patch("dinary.services.sync.append_to_rsd_formula")
    @patch("dinary.services.sync.append_comment")
    def test_recovered_with_duplicate_increments_dedicated_counter(
        self,
        _ac,
        _af,
        _fcr,
        _gmr,
        _fmr,
        _fr,
        mock_sheet,
        setup,
    ):
        ws = MagicMock()
        ws.get_all_values.return_value = [["header"], ["row1"], ["row2"], ["row3"]]
        mock_sheet.return_value.worksheet.return_value = ws
        mock_sheet.return_value.sheet1 = ws

        with patch.object(duckdb_repo, "clear_sync_job", return_value=False):
            result = sync.sync_pending()

        assert result["appended"] == 0, "must not double-count as appended"
        assert result["failed"] == 0, (
            "must not conflate a successful append with a real failure that needs retry"
        )
        assert result["recovered_with_duplicate"] == 1


@allure.epic("Sync")
@allure.feature("get_export_target (TTL cache)")
class TestExportTargetCache:
    """Concern DD regression: the cache must (a) hand out the same value
    within the TTL, (b) re-resolve after `force_refresh=True`, and (c)
    drop a stale entry when resolution raises so the next caller doesn't
    get pinned to a known-broken value for the full TTL window."""

    def _seed_source(self, year: int, ssid: str) -> None:
        duckdb_repo.init_config_db()
        con = duckdb_repo.get_config_connection(read_only=False)
        try:
            con.execute("DELETE FROM sheet_import_sources")
            con.execute(
                "INSERT INTO sheet_import_sources"
                " (year, spreadsheet_id, worksheet_name, layout_key, notes)"
                " VALUES (?, ?, 'Sheet1', 'default', NULL)",
                [year, ssid],
            )
        finally:
            con.close()

    def test_cache_hit_within_ttl(self):
        self._seed_source(2026, "ssid-A")
        first = sync.get_export_target()
        # Mutate the underlying row; without invalidation the cache must
        # still return the original value (cache hit within TTL).
        self._seed_source(2026, "ssid-B")
        second = sync.get_export_target()
        assert first.spreadsheet_id == "ssid-A"
        assert second.spreadsheet_id == "ssid-A"

    def test_force_refresh_bypasses_cache(self):
        self._seed_source(2026, "ssid-A")
        sync.get_export_target()
        self._seed_source(2026, "ssid-B")
        refreshed = sync.get_export_target(force_refresh=True)
        assert refreshed.spreadsheet_id == "ssid-B"

    def test_invalidate_drops_cache(self):
        self._seed_source(2026, "ssid-A")
        sync.get_export_target()
        self._seed_source(2026, "ssid-B")
        sync.invalidate_export_target_cache()
        assert sync.get_export_target().spreadsheet_id == "ssid-B"

    def test_misconfig_raises_and_drops_stale_entry(self):
        # Pin a good value first, then corrupt the source. We deliberately
        # do NOT call `invalidate_export_target_cache` here: the regression
        # we're proving is that the `except RuntimeError` branch in
        # `get_export_target` clears a *populated* cache. Pre-clearing the
        # cache would make the test pass even if that line were deleted
        # (the cache would already be None going in), turning this into a
        # no-op test.
        self._seed_source(2026, "ssid-A")
        assert sync.get_export_target().spreadsheet_id == "ssid-A"

        duckdb_repo.init_config_db()
        con = duckdb_repo.get_config_connection(read_only=False)
        try:
            con.execute("DELETE FROM sheet_import_sources")
        finally:
            con.close()

        # Force a re-resolve without touching the cache: TTL is 60s so a
        # plain call would still hit the cache. The point of this test is
        # what happens when re-resolution raises while the cache is hot.
        with pytest.raises(RuntimeError):
            sync.get_export_target(force_refresh=True)

        # If the error path failed to clear the cache, the next non-forced
        # call would return the stale "ssid-A" value (TTL hasn't expired).
        # The fix at sync.get_export_target's `except RuntimeError` branch
        # is what makes this re-read happen.
        self._seed_source(2026, "ssid-fixed")
        assert sync.get_export_target().spreadsheet_id == "ssid-fixed"


@allure.epic("DuckDB")
@allure.feature("claim_sync_job (TransactionException handling)")
class TestClaimSyncJobTransactionConflict:
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
                on the SELECT inside `claim_sync_job` so the caught branch
                fires deterministically. We can't easily provoke a real
                conflict from a single-threaded test, so we simulate the
                exception path."""

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
            token = duckdb_repo.claim_sync_job(exploding, "exp1")
            assert token is None
        finally:
            bcon.close()

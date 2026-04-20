"""Tests for the queue-based sheet logging layer on the unified dinary.duckdb."""

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
    monkeypatch.setattr(duckdb_repo, "DB_PATH", tmp_path / "dinary.duckdb")
    monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "test-spreadsheet-id")


@pytest.fixture(autouse=True)
def _reset_backoff():
    # Circuit breaker state is module-level; clear it between tests so
    # a prior "transient error" test doesn't stall the next drain with
    # ``{backoff_active: True}``.
    sheet_logging._reset_backoff()
    yield
    sheet_logging._reset_backoff()


@pytest.fixture
def setup() -> int:
    """Seed the unified DB with one expense and its queue row.

    Returns the integer PK of that expense — the sheet-logging layer
    now keys queue rows on ``expenses.id`` rather than on a legacy
    string id.
    """
    duckdb_repo.init_db()
    con = duckdb_repo.get_connection()
    try:
        con.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active)"
            " VALUES (1, 'g', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'еда', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO import_sources"
            " (year, spreadsheet_id, worksheet_name, layout_key, notes)"
            " VALUES (2026, 'sheet-id', 'Sheet1', 'default', NULL)",
        )
        con.execute(
            "INSERT INTO runtime_mapping (row_order, category_id, event_pattern,"
            " sheet_category, sheet_group) VALUES (1, 1, '', 'Food', 'Essentials')",
        )
    finally:
        con.close()

    con = duckdb_repo.get_connection()
    try:
        duckdb_repo.insert_expense(
            con,
            client_expense_id="exp1-client-key",
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
        pk_row = con.execute(
            "SELECT id FROM expenses WHERE client_expense_id = 'exp1-client-key'",
        ).fetchone()
    finally:
        con.close()
    assert pk_row is not None
    return int(pk_row[0])


@allure.epic("SheetLogging")
@allure.feature("drain_pending")
class TestDrainPending:
    @patch("dinary.services.sheet_logging.get_sheet")
    @patch("dinary.services.sheet_logging.get_rate", return_value="117.0")
    @patch("dinary.services.sheet_logging.ensure_category_row")
    @patch("dinary.services.sheet_logging.append_expense_atomic", return_value=True)
    def test_drains_pending_job(
        self,
        mock_append,
        mock_ecr,
        _gr,
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

        assert result["attempted"] == 1
        assert result["appended"] == 1
        assert result["already_logged"] == 0
        assert result["failed"] == 0
        assert result["recovered_with_duplicate"] == 0
        assert result["noop_orphan"] == 0
        assert result["poisoned"] == 0

        # J-marker contract: the key passed into ``append_expense_atomic``
        # is the expense's ``client_expense_id`` UUID (not the integer
        # PK). Regression test for the pre-fix bug where ``ExpenseRow``
        # did not expose ``client_expense_id`` at all and the marker
        # fell back to ``str(expense_pk)``.
        call_kwargs = mock_append.call_args.kwargs
        assert call_kwargs.get("marker_key") == "exp1-client-key"

        con = duckdb_repo.get_connection()
        try:
            assert duckdb_repo.list_logging_jobs(con) == []
        finally:
            con.close()


@allure.epic("SheetLogging")
@allure.feature("drain_pending (poison path)")
class TestDrainPendingPoisonsUnresolvedCategory:
    """If an expense's ``category_id`` does not resolve to any
    ``categories`` row (neither by mapping nor by fallback name), the
    worker must poison the queue row: delete it and log the reason,
    so a single corrupted row never blocks the rest of the queue.

    In practice FK-safe catalog sync prevents this state from existing
    on disk, but the poison branch is the safety net and must be
    covered.
    """

    @patch("dinary.services.sheet_logging.get_sheet")
    @patch("dinary.services.sheet_logging.get_rate", return_value="117.0")
    @patch("dinary.services.sheet_logging.ensure_category_row")
    @patch("dinary.services.sheet_logging.append_expense_atomic", return_value=True)
    def test_unresolved_category_is_poisoned(
        self,
        _aea,
        _ecr,
        _gr,
        _sheet,
        setup,
    ):
        con = duckdb_repo.get_connection()
        try:
            con.execute("DELETE FROM runtime_mapping_tags")
            con.execute("DELETE FROM runtime_mapping")
        finally:
            con.close()

        expense_pk = setup
        with patch.object(duckdb_repo, "get_category_name", return_value=None):
            result = sheet_logging.drain_pending()

        assert result["poisoned"] == 1
        assert result["appended"] == 0
        assert result["failed"] == 0
        con = duckdb_repo.get_connection()
        try:
            assert duckdb_repo.list_logging_jobs(con) == []
            # The queue row is still on disk but in status='poisoned',
            # which is why ``list_logging_jobs`` (pending + stale
            # in_progress) doesn't surface it. The expense ledger row
            # itself is untouched — poison only marks the queue row,
            # never the underlying expense.
            poisoned = con.execute(
                "SELECT COUNT(*) FROM sheet_logging_jobs"
                " WHERE expense_id = ? AND status = 'poisoned'",
                [expense_pk],
            ).fetchone()[0]
            assert poisoned == 1
            count = con.execute(
                "SELECT COUNT(*) FROM expenses WHERE id = ?",
                [expense_pk],
            ).fetchone()[0]
        finally:
            con.close()
        assert count == 1


@allure.epic("SheetLogging")
@allure.feature("drain_pending (null-uuid poison path)")
class TestDrainPendingPoisonsNullClientExpenseId:
    """A queue row whose underlying expense has
    ``client_expense_id = NULL`` must be poisoned rather than
    append-with-fallback-marker. Bootstrap-imported rows carry a NULL
    UUID but are explicitly never enqueued (``enqueue_logging=False``),
    so this branch catches a misbehaving runtime producer — silently
    writing a non-UUID marker (e.g. the server PK) into column J would
    corrupt the idempotency contract for every later append to the
    same row.
    """

    @patch("dinary.services.sheet_logging.get_sheet")
    @patch("dinary.services.sheet_logging.get_rate", return_value="117.0")
    @patch("dinary.services.sheet_logging.ensure_category_row")
    @patch("dinary.services.sheet_logging.append_expense_atomic", return_value=True)
    def test_null_client_expense_id_is_poisoned(
        self,
        mock_append,
        _ecr,
        _gr,
        _sheet,
    ):
        duckdb_repo.init_db()

        # Seed minimal catalog + a single expense with
        # client_expense_id = NULL, then force a queue row for it so we
        # simulate the "malformed runtime producer" condition. We bypass
        # ``insert_expense(enqueue_logging=True)`` for this leg because
        # the public path refuses to let NULL + enqueue coexist on a
        # runtime call — which is exactly the invariant we're testing.
        con = duckdb_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (1, 'g', 1, TRUE)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'еда', 1, TRUE)",
            )
            duckdb_repo.insert_expense(
                con,
                client_expense_id=None,
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
                enqueue_logging=False,
            )
            expense_pk = con.execute(
                "SELECT id FROM expenses WHERE client_expense_id IS NULL",
            ).fetchone()[0]
            con.execute(
                "INSERT INTO sheet_logging_jobs (expense_id, status) VALUES (?, 'pending')",
                [expense_pk],
            )
        finally:
            con.close()

        result = sheet_logging.drain_pending()

        assert result["poisoned"] == 1
        assert result["appended"] == 0
        assert result["failed"] == 0
        mock_append.assert_not_called()

        con = duckdb_repo.get_connection()
        try:
            assert duckdb_repo.list_logging_jobs(con) == []
            row = con.execute(
                "SELECT status, last_error FROM sheet_logging_jobs WHERE expense_id = ?",
                [expense_pk],
            ).fetchone()
        finally:
            con.close()
        assert row is not None
        status, reason = row
        assert status == "poisoned"
        assert "client_expense_id" in (reason or "")


@allure.epic("SheetLogging")
@allure.feature("drain_pending (category fallback)")
class TestDrainPendingCategoryFallback:
    """When ``runtime_mapping`` has no matching row for the expense's
    category, the worker must fall back to the category name as the
    sheet category, with an empty sheet group."""

    @patch("dinary.services.sheet_logging.get_sheet")
    @patch("dinary.services.sheet_logging.get_rate", return_value="117.0")
    @patch("dinary.services.sheet_logging.ensure_category_row")
    @patch("dinary.services.sheet_logging.append_expense_atomic", return_value=True)
    def test_category_name_fallback_when_no_mapping(
        self,
        _aea,
        mock_ecr,
        _gr,
        mock_sheet,
        setup,
    ):
        con = duckdb_repo.get_connection()
        try:
            con.execute("DELETE FROM runtime_mapping_tags")
            con.execute("DELETE FROM runtime_mapping")
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
        # The helper takes ``(ws, all_values, month, category, group, ...)``
        # positionally; the month is 4, the category is "еда", and the
        # fallback group is the empty string.
        assert ecr_call_args[0][2] == 4
        assert ecr_call_args[0][3] == "еда"
        assert ecr_call_args[0][4] == ""


@allure.epic("SheetLogging")
@allure.feature("_drain_one_job (return contract)")
class TestDrainOneJobReturnContract:
    @patch("dinary.services.sheet_logging.get_sheet")
    @patch("dinary.services.sheet_logging.get_rate", return_value="117.0")
    @patch("dinary.services.sheet_logging.ensure_category_row")
    @patch(
        "dinary.services.sheet_logging.append_expense_atomic",
        side_effect=RuntimeError("simulated sheet failure"),
    )
    def test_append_failure_re_raises_and_releases_claim(
        self,
        _aea,
        mock_ecr,
        _gr,
        mock_sheet,
        setup,
    ):
        """``_drain_one_job`` re-raises on append failure so
        ``drain_pending`` can classify the error as transient/permanent.
        The claim must be released so the next sweep can retry."""
        ws = MagicMock()
        values = [["header"], ["row1"], ["row2"], ["row3"]]
        ws.get_all_values.return_value = values
        mock_sheet.return_value.worksheet.return_value = ws
        mock_sheet.return_value.sheet1 = ws
        mock_ecr.return_value = (3, values)

        expense_pk = setup
        with pytest.raises(RuntimeError, match="simulated sheet failure"):
            sheet_logging._drain_one_job(
                expense_pk,
                spreadsheet_id="test-spreadsheet-id",
            )

        # Queue row remains ``pending`` (claim released) so the next
        # sweep retries.
        con = duckdb_repo.get_connection()
        try:
            assert duckdb_repo.list_logging_jobs(con) == [expense_pk]
        finally:
            con.close()


@allure.epic("SheetLogging")
@allure.feature("_drain_one_job (post-append claim-stolen recovery)")
class TestDrainOneJobClaimStolen:
    """When ``clear_logging_job`` returns False after we already appended
    to Sheets, ``_drain_one_job`` must:

    1. Force-delete the queue row (so the next sweep can't trigger a
       third append).
    2. Surface the outcome as ``RECOVERED_WITH_DUPLICATE`` — distinct
       from ``FAILED`` so the sweep summary tells "audit the sheet"
       from "retry pending" apart.
    """

    @patch("dinary.services.sheet_logging.get_sheet")
    @patch("dinary.services.sheet_logging.get_rate", return_value="117.0")
    @patch("dinary.services.sheet_logging.ensure_category_row")
    @patch("dinary.services.sheet_logging.append_expense_atomic", return_value=True)
    def test_force_delete_after_stolen_claim(
        self,
        _aea,
        mock_ecr,
        _gr,
        mock_sheet,
        setup,
    ):
        ws = MagicMock()
        values = [["header"], ["row1"], ["row2"], ["row3"]]
        ws.get_all_values.return_value = values
        mock_sheet.return_value.worksheet.return_value = ws
        mock_sheet.return_value.sheet1 = ws
        mock_ecr.return_value = (3, values)

        expense_pk = setup
        with patch.object(duckdb_repo, "clear_logging_job", return_value=False):
            result = sheet_logging._drain_one_job(
                expense_pk,
                spreadsheet_id="test-spreadsheet-id",
            )

        assert result is sheet_logging.DrainResult.RECOVERED_WITH_DUPLICATE
        con = duckdb_repo.get_connection()
        try:
            assert duckdb_repo.list_logging_jobs(con) == []
        finally:
            con.close()

    @patch("dinary.services.sheet_logging.get_sheet")
    @patch("dinary.services.sheet_logging.get_rate", return_value="117.0")
    @patch("dinary.services.sheet_logging.ensure_category_row")
    @patch("dinary.services.sheet_logging.append_expense_atomic", return_value=True)
    def test_recovered_when_row_already_gone(
        self,
        _aea,
        mock_ecr,
        _gr,
        mock_sheet,
        setup,
    ):
        """Operator-wipe sub-case: the queue row was deleted out from
        under us mid-append. Both ``clear_logging_job`` and
        ``force_clear_logging_job`` find nothing, but we still surface
        ``RECOVERED_WITH_DUPLICATE`` — we cannot distinguish this case
        from a stolen claim and over-warning is safer than silently
        leaking a duplicate."""
        ws = MagicMock()
        values = [["header"], ["row1"], ["row2"], ["row3"]]
        ws.get_all_values.return_value = values
        mock_sheet.return_value.worksheet.return_value = ws
        mock_sheet.return_value.sheet1 = ws
        mock_ecr.return_value = (3, values)

        expense_pk = setup
        with (
            patch.object(duckdb_repo, "clear_logging_job", return_value=False),
            patch.object(duckdb_repo, "force_clear_logging_job", return_value=False),
        ):
            result = sheet_logging._drain_one_job(
                expense_pk,
                spreadsheet_id="test-spreadsheet-id",
            )

        assert result is sheet_logging.DrainResult.RECOVERED_WITH_DUPLICATE


@allure.epic("SheetLogging")
@allure.feature("drain_pending (counter accounting)")
class TestDrainPendingCounters:
    """``drain_pending`` must split clean appends, real failures, and
    post-append recovery into three distinct counters so an operator
    scanning the summary can tell "needs retry" from "audit the sheet
    for duplicates"."""

    @patch("dinary.services.sheet_logging.get_sheet")
    @patch("dinary.services.sheet_logging.get_rate", return_value="117.0")
    @patch("dinary.services.sheet_logging.ensure_category_row")
    @patch("dinary.services.sheet_logging.append_expense_atomic", return_value=True)
    def test_recovered_with_duplicate_increments_dedicated_counter(
        self,
        _aea,
        mock_ecr,
        _gr,
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

        assert result["appended"] == 0
        assert result["failed"] == 0
        assert result["recovered_with_duplicate"] == 1


@allure.epic("SheetLogging")
@allure.feature("Idempotency marker (last-key-only)")
class TestIdempotencyMarker:
    """When ``append_expense_atomic`` returns False (marker already
    present on the row), the drain must count it as ``ALREADY_LOGGED``
    and still clear the queue row."""

    @patch("dinary.services.sheet_logging.get_sheet")
    @patch("dinary.services.sheet_logging.get_rate", return_value="117.0")
    @patch("dinary.services.sheet_logging.ensure_category_row")
    @patch("dinary.services.sheet_logging.append_expense_atomic", return_value=False)
    def test_marker_present_returns_already_logged_and_clears_queue(
        self,
        _aea,
        mock_ecr,
        _gr,
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

        assert result["appended"] == 0
        assert result["already_logged"] == 1
        assert result["failed"] == 0
        assert result["recovered_with_duplicate"] == 0

        con = duckdb_repo.get_connection()
        try:
            assert duckdb_repo.list_logging_jobs(con) == []
        finally:
            con.close()


@allure.epic("SheetLogging")
@allure.feature("sheet logging disabled")
class TestSheetLoggingDisabled:
    """When ``DINARY_SHEET_LOGGING_SPREADSHEET`` is empty, the drain
    is a no-op that returns a bare ``{"disabled": True}``."""

    def test_drain_pending_returns_disabled(self, setup, monkeypatch):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "")
        result = sheet_logging.drain_pending()
        assert result == {"disabled": True}


@allure.epic("SheetLogging")
@allure.feature("Circuit breaker")
class TestCircuitBreaker:
    """Module-level backoff state means a transient failure stalls the
    next drain attempt with ``{backoff_active: True}`` instead of
    re-hammering Sheets."""

    def test_backoff_active_short_circuits_drain(self, setup):
        sheet_logging._activate_backoff()
        result = sheet_logging.drain_pending()
        assert result == {"backoff_active": True}


@allure.epic("DuckDB")
@allure.feature("claim_logging_job (TransactionException handling)")
class TestClaimLoggingJobTransactionConflict:
    """A ``duckdb.TransactionException`` raised by DuckDB's
    optimistic-concurrency layer when two workers race on the same row
    surfaces as a clean ``None`` return — the caller treats ``None`` as
    "skip this row, the winner will handle it"."""

    def test_transaction_exception_returns_none(self, setup):
        expense_pk = setup
        con = duckdb_repo.get_connection()
        try:

            class _Exploding:
                """Connection wrapper that raises TransactionException on
                the SELECT inside ``claim_logging_job`` so the caught
                branch fires deterministically. We can't easily provoke a
                real conflict from a single-threaded test."""

                def __init__(self, real):
                    self._real = real
                    self._calls = 0

                def execute(self, sql, *args, **kwargs):
                    self._calls += 1
                    # 1st call is BEGIN, 2nd is the SELECT we want to
                    # fail. After that ROLLBACK is passed through.
                    if self._calls == 2:  # noqa: PLR2004
                        raise duckdb.TransactionException("simulated conflict")
                    return self._real.execute(sql, *args, **kwargs)

                def __getattr__(self, name):
                    return getattr(self._real, name)

            exploding = _Exploding(con)
            token = duckdb_repo.claim_logging_job(exploding, expense_pk)
            assert token is None
        finally:
            con.close()


@allure.epic("SheetLogging")
@allure.feature("drain_pending rate-limit")
class TestDrainRateLimit:
    """Rate-limiting and inter-row sleep on ``drain_pending``. The
    single-DB refactor dropped the TTL + year-window code paths, so the
    remaining surface is just ``max_attempts_per_iteration`` and
    ``inter_row_delay_sec``."""

    def _insert_additional_expenses(self, n: int) -> None:
        con = duckdb_repo.get_connection()
        try:
            for i in range(n):
                duckdb_repo.insert_expense(
                    con,
                    client_expense_id=f"extra-{i:03d}",
                    expense_datetime=datetime(2026, 6, 1 + i % 25, 10),
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
            con.close()

    @patch("dinary.services.sheet_logging._drain_one_job")
    def test_cap_honored(self, mock_drain_one, setup, monkeypatch):
        """Hard cap stops the sweep after ``max_attempts``."""
        monkeypatch.setattr(settings, "sheet_logging_drain_max_attempts_per_iteration", 5)
        monkeypatch.setattr(settings, "sheet_logging_drain_inter_row_delay_sec", 0)

        self._insert_additional_expenses(25)

        mock_drain_one.return_value = sheet_logging.DrainResult.APPENDED
        summary = sheet_logging.drain_pending()

        assert mock_drain_one.call_count == 5
        assert summary["cap_reached"] is True
        assert summary["attempted"] == 5

    @patch("dinary.services.sheet_logging._drain_one_job")
    def test_inter_row_sleep_observed(self, mock_drain_one, setup, monkeypatch):
        """Sleep is called between attempts (before each except the first)."""
        monkeypatch.setattr(settings, "sheet_logging_drain_max_attempts_per_iteration", 10)
        monkeypatch.setattr(settings, "sheet_logging_drain_inter_row_delay_sec", 0.001)

        self._insert_additional_expenses(3)

        mock_drain_one.return_value = sheet_logging.DrainResult.APPENDED
        sleep_mock = MagicMock()
        monkeypatch.setattr(sheet_logging.time, "sleep", sleep_mock)

        sheet_logging.drain_pending()

        # 1 expense from setup + 3 new = 4 total attempts; sleep before
        # 2nd, 3rd, 4th.
        assert sleep_mock.call_count == 3
        for call in sleep_mock.call_args_list:
            assert call.args[0] == 0.001

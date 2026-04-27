"""Edge-case tests for the sheet-logging queue.

Idempotency-marker, ``DINARY_SHEET_LOGGING_SPREADSHEET=""`` short-circuit,
circuit-breaker module-level state, ``claim_logging_job`` lock conflict,
and ``drain_pending``'s rate-limit / inter-row delay knobs.

Sibling files cover the larger surfaces:

* :file:`test_sheet_logging_derive.py` —
  ``_derive_app_currency_amount_for_sheet``.
* :file:`test_sheet_logging_drain.py` — drain_pending happy path,
  poisoning, fallback, counters.
* :file:`test_sheet_logging_drain_one.py` — ``_drain_one_job``
  return-contract + post-append claim-stolen recovery.
"""

import sqlite3
from datetime import datetime
from unittest.mock import MagicMock, patch

import allure

from dinary.config import settings
from dinary.services import ledger_repo, sheet_logging

from _sheet_logging_helpers import (  # noqa: F401  (autouse + fixtures)
    _reset_backoff,
    _tmp_data_dir,
    setup,
)


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

        con = ledger_repo.get_connection()
        try:
            assert ledger_repo.list_logging_jobs(con) == []
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


@allure.epic("SheetLogging")
@allure.feature("claim_logging_job (lock-conflict handling)")
class TestClaimLoggingJobLockConflict:
    """A ``sqlite3.OperationalError`` raised by SQLite's write-lock
    timeout when two workers race on the same row surfaces as a clean
    ``None`` return — the caller treats ``None`` as "skip this row, the
    winner will handle it"."""

    def test_lock_conflict_on_begin_returns_none(self, setup):
        expense_pk = setup
        # Provoke a real SQLite write-lock conflict, not a mock: one
        # connection holds ``BEGIN IMMEDIATE`` (the write lock); a
        # second connection opened with ``timeout=0`` cannot wait for
        # it and ``BEGIN IMMEDIATE`` from ``claim_logging_job`` surfaces
        # as ``OperationalError("database is locked")`` immediately.
        # This is the exact runtime shape two drain workers hit when
        # they race on the same queue row.
        holder = ledger_repo.get_connection()
        loser = sqlite3.connect(
            str(ledger_repo.DB_PATH),
            isolation_level=None,
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
            timeout=0,
        )
        try:
            holder.execute("BEGIN IMMEDIATE")
            token = ledger_repo.claim_logging_job(loser, expense_pk)
            assert token is None
            holder.execute("COMMIT")
        finally:
            loser.close()
            holder.close()


@allure.epic("SheetLogging")
@allure.feature("drain_pending rate-limit")
class TestDrainRateLimit:
    """Rate-limiting and inter-row sleep on ``drain_pending``. The
    single-DB refactor dropped the TTL + year-window code paths, so the
    remaining surface is just ``max_attempts_per_iteration`` and
    ``inter_row_delay_sec``."""

    def _insert_additional_expenses(self, n: int) -> None:
        con = ledger_repo.get_connection()
        try:
            for i in range(n):
                ledger_repo.insert_expense(
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

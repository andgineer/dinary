"""Tests for ``drain_pending``: happy path, poisoning, fallback,
counters.

Sibling files:

* :file:`test_sheet_logging_derive.py` —
  ``_derive_app_currency_amount_for_sheet``.
* :file:`test_sheet_logging_drain_one.py` — ``_drain_one_job``
  return-contract + post-append claim-stolen recovery.
* :file:`test_sheet_logging.py` — idempotency / circuit breaker /
  disabled / lock conflict / rate limit.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import allure

from dinary.services import ledger_repo, sheet_logging

from _sheet_logging_helpers import (  # noqa: F401  (autouse + fixtures)
    _reset_backoff,
    _tmp_data_dir,
    setup,
)


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

        con = ledger_repo.get_connection()
        try:
            assert ledger_repo.list_logging_jobs(con) == []
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
        con = ledger_repo.get_connection()
        try:
            con.execute("DELETE FROM sheet_mapping_tags")
            con.execute("DELETE FROM sheet_mapping")
        finally:
            con.close()

        expense_pk = setup
        with patch.object(ledger_repo, "get_category_name", return_value=None):
            result = sheet_logging.drain_pending()

        assert result["poisoned"] == 1
        assert result["appended"] == 0
        assert result["failed"] == 0
        con = ledger_repo.get_connection()
        try:
            assert ledger_repo.list_logging_jobs(con) == []
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
        ledger_repo.init_db()

        # Seed minimal catalog + a single expense with
        # client_expense_id = NULL, then force a queue row for it so we
        # simulate the "malformed runtime producer" condition. We bypass
        # ``insert_expense(enqueue_logging=True)`` for this leg because
        # the public path refuses to let NULL + enqueue coexist on a
        # runtime call — which is exactly the invariant we're testing.
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (1, 'g', 1, TRUE)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'еда', 1, TRUE)",
            )
            ledger_repo.insert_expense(
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

        con = ledger_repo.get_connection()
        try:
            assert ledger_repo.list_logging_jobs(con) == []
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
    """When ``sheet_mapping`` has no matching row for the expense's
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
        con = ledger_repo.get_connection()
        try:
            con.execute("DELETE FROM sheet_mapping_tags")
            con.execute("DELETE FROM sheet_mapping")
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

        with patch.object(ledger_repo, "clear_logging_job", return_value=False):
            result = sheet_logging.drain_pending()

        assert result["appended"] == 0
        assert result["failed"] == 0
        assert result["recovered_with_duplicate"] == 1

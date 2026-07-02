"""Tests for ``drain_pending``: happy path, poisoning, fallback, counters.
Sibling files cover derive (``test_sheet_logging_derive.py``), single-job drain
(``test_sheet_logging_drain_one.py``), and idempotency/circuit-breaker
(``test_sheet_logging.py``)."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import allure

import shutil

from dinary.db import storage
from dinary.background.sheet_logging import sheet_logging
from dinary.background.sheet_logging.logging_jobs import list_logging_jobs
from dinary.db.expenses import ExpensePayload, insert_expense

from _sheet_logging_helpers import (  # noqa: F401  (autouse + fixtures)
    _reset_backoff,
    data_dir,
    setup,
)


@allure.epic("Sheets Sync")
@allure.feature("Sheet logging")
@allure.story("Drain pending")
class TestDrainPending:
    @patch("dinary.background.sheet_logging.sheet_logging.get_sheet")
    @patch("dinary.background.sheet_logging.sheet_logging.get_rate", return_value="117.0")
    @patch("dinary.background.sheet_logging.sheet_logging.ensure_category_row")
    @patch("dinary.background.sheet_logging.sheet_logging.append_expense_atomic", return_value=True)
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

        # marker_key must be the client_expense_id UUID, not the integer PK.
        call_kwargs = mock_append.call_args.kwargs
        assert call_kwargs.get("marker_key") == "exp1-client-key"

        con = storage.get_connection()
        try:
            assert list_logging_jobs(con) == []
        finally:
            con.close()


@allure.epic("Sheets Sync")
@allure.feature("Sheet logging")
@allure.story("Drain pending")
class TestDrainPendingPoisonsUnresolvedCategory:
    """An unresolvable ``category_id`` must poison the queue row so it never
    blocks the rest — a safety net FK-safe sync should normally prevent."""

    @patch("dinary.background.sheet_logging.sheet_logging.get_sheet")
    @patch("dinary.background.sheet_logging.sheet_logging.get_rate", return_value="117.0")
    @patch("dinary.background.sheet_logging.sheet_logging.ensure_category_row")
    @patch("dinary.background.sheet_logging.sheet_logging.append_expense_atomic", return_value=True)
    def test_unresolved_category_is_poisoned(
        self,
        _aea,
        _ecr,
        _gr,
        _sheet,
        setup,
    ):
        con = storage.get_connection()
        try:
            con.execute("DELETE FROM sheet_mapping_tags")
            con.execute("DELETE FROM sheet_mapping")
        finally:
            con.close()

        expense_pk = setup
        with patch(
            "dinary.background.sheet_logging.sheet_logging.logging_projection", return_value=None
        ):
            result = sheet_logging.drain_pending()

        assert result["poisoned"] == 1
        assert result["appended"] == 0
        assert result["failed"] == 0
        con = storage.get_connection()
        try:
            assert list_logging_jobs(con) == []
            # Poison marks the queue row only; the expense ledger row is untouched.
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


@allure.epic("Sheets Sync")
@allure.feature("Sheet logging")
@allure.story("Drain pending")
class TestDrainPendingPoisonsNullClientExpenseId:
    """A NULL ``client_expense_id`` is always a producer bug, see
    ``specs/reference/sheets.md``."""

    @patch("dinary.background.sheet_logging.sheet_logging.get_sheet")
    @patch("dinary.background.sheet_logging.sheet_logging.get_rate", return_value="117.0")
    @patch("dinary.background.sheet_logging.sheet_logging.ensure_category_row")
    @patch("dinary.background.sheet_logging.sheet_logging.append_expense_atomic", return_value=True)
    def test_null_client_expense_id_is_poisoned(
        self,
        mock_append,
        _ecr,
        _gr,
        _sheet,
        blank_db,
    ):
        shutil.copy(blank_db, storage.DB_PATH)

        # Bypasses insert_expense(enqueue_logging=True) since the public path
        # refuses to let NULL + enqueue coexist — the invariant under test.
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (1, 'g', 1, TRUE)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'food', 1, TRUE)",
            )
            insert_expense(
                con,
                ExpensePayload(
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
                ),
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

        con = storage.get_connection()
        try:
            assert list_logging_jobs(con) == []
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


@allure.epic("Sheets Sync")
@allure.feature("Sheet logging")
@allure.story("Drain pending")
class TestDrainPendingCategoryFallback:
    """When ``sheet_mapping`` has no matching row for the expense's
    category, the worker must fall back to the category name as the
    sheet category, with an empty sheet group."""

    @patch("dinary.background.sheet_logging.sheet_logging.get_sheet")
    @patch("dinary.background.sheet_logging.sheet_logging.get_rate", return_value="117.0")
    @patch("dinary.background.sheet_logging.sheet_logging.ensure_category_row")
    @patch("dinary.background.sheet_logging.sheet_logging.append_expense_atomic", return_value=True)
    def test_category_name_fallback_when_no_mapping(
        self,
        _aea,
        mock_ecr,
        _gr,
        mock_sheet,
        setup,
    ):
        con = storage.get_connection()
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
        # positionally; the month is 4, the category is "food", and the
        # fallback group is the empty string.
        assert ecr_call_args[0][2] == 4
        assert ecr_call_args[0][3] == "food"
        assert ecr_call_args[0][4] == ""


@allure.epic("Sheets Sync")
@allure.feature("Sheet logging")
@allure.story("Drain pending")
class TestDrainPendingCounters:
    """``drain_pending`` must split clean appends, real failures, and
    post-append recovery into three distinct counters so an operator
    scanning the summary can tell "needs retry" from "audit the sheet
    for duplicates"."""

    @patch("dinary.background.sheet_logging.sheet_logging.get_sheet")
    @patch("dinary.background.sheet_logging.sheet_logging.get_rate", return_value="117.0")
    @patch("dinary.background.sheet_logging.sheet_logging.ensure_category_row")
    @patch("dinary.background.sheet_logging.sheet_logging.append_expense_atomic", return_value=True)
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

        with patch(
            "dinary.background.sheet_logging.sheet_logging.clear_logging_job", return_value=False
        ):
            result = sheet_logging.drain_pending()

        assert result["appended"] == 0
        assert result["failed"] == 0
        assert result["recovered_with_duplicate"] == 1

"""Tests for the ``_drain_one_job`` per-row contract.

Sibling files:

* :file:`test_sheet_logging_derive.py` —
  ``_derive_app_currency_amount_for_sheet``.
* :file:`test_sheet_logging_drain.py` — drain_pending happy path,
  poisoning, fallback, counters.
* :file:`test_sheet_logging.py` — idempotency / circuit breaker /
  disabled / lock conflict / rate limit.
"""

from unittest.mock import MagicMock, patch

import allure
import pytest

from dinary.services import ledger_repo, sheet_logging

from _sheet_logging_helpers import (  # noqa: F401  (autouse + fixtures)
    _reset_backoff,
    _tmp_data_dir,
    setup,
)


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
        con = ledger_repo.get_connection()
        try:
            assert ledger_repo.list_logging_jobs(con) == [expense_pk]
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
        with patch.object(ledger_repo, "clear_logging_job", return_value=False):
            result = sheet_logging._drain_one_job(
                expense_pk,
                spreadsheet_id="test-spreadsheet-id",
            )

        assert result is sheet_logging.DrainResult.RECOVERED_WITH_DUPLICATE
        con = ledger_repo.get_connection()
        try:
            assert ledger_repo.list_logging_jobs(con) == []
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
            patch.object(ledger_repo, "clear_logging_job", return_value=False),
            patch.object(ledger_repo, "force_clear_logging_job", return_value=False),
        ):
            result = sheet_logging._drain_one_job(
                expense_pk,
                spreadsheet_id="test-spreadsheet-id",
            )

        assert result is sheet_logging.DrainResult.RECOVERED_WITH_DUPLICATE

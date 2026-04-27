"""Tests for the ``_derive_app_currency_amount_for_sheet`` helper.

Sibling files:

* :file:`test_sheet_logging_drain.py` — drain_pending happy path,
  poisoning, fallback, counters.
* :file:`test_sheet_logging_drain_one.py` — _drain_one_job
  return-contract + post-append claim-stolen recovery.
* :file:`test_sheet_logging.py` — idempotency / circuit breaker /
  disabled / lock conflict / rate limit.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import allure

from dinary.config import settings
from dinary.services import sheet_logging

from _sheet_logging_helpers import (  # noqa: F401  (autouse + helper)
    _expense_row,
    _reset_backoff,
    _tmp_data_dir,
)


@allure.epic("SheetLogging")
@allure.feature("_derive_app_currency_amount_for_sheet")
class TestDeriveRsdForSheet:
    """Column B on the Sheets mirror is RSD-denominated (the sheet's
    native "original" currency post-Apr-2022). DB rows are stored in
    ``settings.accounting_currency`` (EUR by default). The helper must
    bridge that gap without ever writing a wrong-currency amount.
    """

    _DATE = date(2026, 4, 14)

    def test_rsd_input_returns_amount_original_verbatim(self):
        """PWA default path: operator typed in RSD, so ``amount_original``
        is already the correct sheet value. No rate lookup, no rounding
        drift — bit-identical to what the user saw in the app."""
        row = _expense_row(
            amount=Decimal("12.82"),
            amount_original=Decimal("1500.00"),
            currency_original="RSD",
        )
        with patch("dinary.services.sheet_logging.get_rate") as mock_rate:
            out = sheet_logging._derive_app_currency_amount_for_sheet(
                con=None,
                expense=row,
                app_currency_rate=Decimal("117.0"),
                expense_date=self._DATE,
            )
        assert out == 1500.0
        # RSD shortcut must not consult NBS rates at all, even if
        # ``app_currency_rate`` happens to be present.
        mock_rate.assert_not_called()

    def test_eur_accounting_converts_via_supplied_app_currency_rate(
        self,
        monkeypatch,
    ):
        """Default setup: ``accounting_currency=EUR``, expense stored
        in EUR, operator typed in some non-RSD currency. Helper must
        use the already-fetched EUR/RSD column-H rate — no second
        ``get_rate`` call."""
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        row = _expense_row(
            amount=Decimal("10.00"),
            amount_original=Decimal("12.00"),
            currency_original="USD",
        )
        with patch("dinary.services.sheet_logging.get_rate") as mock_rate:
            out = sheet_logging._derive_app_currency_amount_for_sheet(
                con=None,
                expense=row,
                app_currency_rate=Decimal("117.0"),
                expense_date=self._DATE,
            )
        assert out == 1170.00
        mock_rate.assert_not_called()

    def test_eur_accounting_without_rate_returns_none(self, monkeypatch):
        """``app_currency_rate=None`` means no rate available for the expense
        date. Helper must signal failure (``None``) so the caller
        requeues the job — never silently write 0 or a stale value."""
        monkeypatch.setattr(settings, "accounting_currency", "EUR")
        row = _expense_row(
            amount=Decimal("10.00"),
            amount_original=Decimal("12.00"),
            currency_original="USD",
        )
        with patch(
            "dinary.services.sheet_logging.get_rate",
            side_effect=ValueError("no rate"),
        ):
            out = sheet_logging._derive_app_currency_amount_for_sheet(
                con=None,
                expense=row,
                app_currency_rate=None,
                expense_date=self._DATE,
            )
        assert out is None

    def test_rsd_accounting_returns_amount_directly(self, monkeypatch):
        """Edge case: ``accounting_currency=RSD`` (the pre-split legacy
        setup). ``expenses.amount`` is already RSD, so the helper just
        forwards it. Keeps backwards compatibility for anyone who
        overrides the default."""
        monkeypatch.setattr(settings, "accounting_currency", "RSD")
        row = _expense_row(
            amount=Decimal("1500.00"),
            amount_original=Decimal("12.00"),
            currency_original="EUR",
        )
        with patch("dinary.services.sheet_logging.get_rate") as mock_rate:
            out = sheet_logging._derive_app_currency_amount_for_sheet(
                con=None,
                expense=row,
                app_currency_rate=None,
                expense_date=self._DATE,
            )
        assert out == 1500.0
        mock_rate.assert_not_called()

    def test_exotic_accounting_currency_fetches_cross_rate(
        self,
        monkeypatch,
    ):
        """If someone configures an accounting currency that is neither
        EUR nor RSD, the helper must resolve the cross-rate on demand
        via ``get_rate(date, accounting_currency)``. This is the only
        branch that issues a fresh NBS lookup."""
        monkeypatch.setattr(settings, "accounting_currency", "USD")
        row = _expense_row(
            amount=Decimal("10.00"),
            amount_original=Decimal("10.00"),
            currency_original="USD",
        )
        with patch(
            "dinary.services.sheet_logging.get_rate",
            return_value=Decimal("108.50"),
        ) as mock_rate:
            out = sheet_logging._derive_app_currency_amount_for_sheet(
                con=None,
                expense=row,
                app_currency_rate=None,
                expense_date=self._DATE,
            )
        assert out == 1085.00
        mock_rate.assert_called_once()
        call_args = mock_rate.call_args
        assert call_args.args[1] == self._DATE
        assert call_args.args[2] == "USD"
        assert call_args.args[3] == "RSD"

    def test_exotic_accounting_currency_without_rate_returns_none(
        self,
        monkeypatch,
    ):
        """``get_rate`` raises ``ValueError``/``OSError`` when NBS has
        no data for the requested date. The helper must trap both and
        return ``None`` so the drain loop requeues instead of blowing
        up."""
        monkeypatch.setattr(settings, "accounting_currency", "USD")
        row = _expense_row(
            amount=Decimal("10.00"),
            amount_original=Decimal("10.00"),
            currency_original="USD",
        )
        with patch(
            "dinary.services.sheet_logging.get_rate",
            side_effect=ValueError("no rate"),
        ):
            out = sheet_logging._derive_app_currency_amount_for_sheet(
                con=None,
                expense=row,
                app_currency_rate=None,
                expense_date=self._DATE,
            )
        assert out is None

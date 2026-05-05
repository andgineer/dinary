"""Unit tests for the NBP fallback resolver (``dinary.services.nbp``).

NBP quotes ``1 X = N PLN`` and we bridge any pair through PLN. This
suite pins:

* The two-table fetch order (table A then table B) inside
  ``_fetch_nbp_pln_leg``.
* The "specific date 404 → no-date latest" walk inside
  ``_pln_leg``.
* The PLN-bridge product, identity, and missing-leg cases inside
  ``_resolve_from_nbp``.

Cross-resolver chain tests (NBS → NBP fallback) live in
:file:`test_currency_rates_misc.py`.
"""

from decimal import Decimal
from unittest.mock import MagicMock, call, patch

import allure

from dinary.services.nbp import _fetch_nbp_pln_leg, _pln_leg, _resolve_from_nbp

from _currency_rates_helpers import (  # noqa: F401  (autouse + fixtures)
    _MON,
    _clear_ttl_caches,
)


@allure.epic("Services")
@allure.feature("NBP Exchange Rate")
@allure.story("_fetch_nbp_pln_leg — table A then table B")
class TestFetchNbpPlnLeg:
    @patch("dinary.services.nbp._get_json_or_none")
    def test_table_a_hit_skips_table_b(self, mock_json):
        # USD lives in table A: NBP responds to the table-A URL and
        # we never need to ask table B.
        mock_json.return_value = {"rates": [{"mid": "3.6303"}]}
        rate = _fetch_nbp_pln_leg(_MON, "USD")
        assert rate == Decimal("3.6303")
        mock_json.assert_called_once()
        ((url,), _kwargs) = mock_json.call_args
        assert "/rates/A/usd/" in url

    @patch("dinary.services.nbp._get_json_or_none")
    def test_table_b_used_when_table_a_404(self, mock_json):
        # RSD lives only in table B (less-common, weekly). Table-A
        # call 404s; table-B call returns the rate.
        mock_json.side_effect = [None, {"rates": [{"mid": "0.0362"}]}]
        rate = _fetch_nbp_pln_leg(_MON, "RSD")
        assert rate == Decimal("0.0362")
        # First call hit table A, second hit table B.
        urls = [c.args[0] for c in mock_json.call_args_list]
        assert "/rates/A/rsd/" in urls[0]
        assert "/rates/B/rsd/" in urls[1]

    @patch("dinary.services.nbp._get_json_or_none")
    def test_returns_none_when_both_tables_404(self, mock_json):
        mock_json.return_value = None
        assert _fetch_nbp_pln_leg(_MON, "ZZZ") is None
        # Both tables consulted before giving up.
        assert mock_json.call_count == 2  # noqa: PLR2004

    @patch("dinary.services.nbp._get_json_or_none")
    def test_no_date_form_when_rate_date_is_none(self, mock_json):
        # ``rate_date=None`` means "give me the latest published rate".
        mock_json.return_value = {"rates": [{"mid": "0.0362"}]}
        _fetch_nbp_pln_leg(None, "RSD")
        url = mock_json.call_args.args[0]
        # Trailing slash with no date segment means "latest" in NBP API.
        assert url.endswith("/rates/A/rsd/")


@allure.epic("Services")
@allure.feature("NBP Exchange Rate")
@allure.story("_pln_leg — date-then-latest walk")
class TestPlnLeg:
    def test_pln_self_is_identity(self):
        # PLN/PLN does not need an HTTP call; it's just 1.
        assert _pln_leg(_MON, "PLN") == Decimal(1)

    @patch("dinary.services.nbp._fetch_nbp_pln_leg")
    def test_returns_date_specific_rate_when_available(self, mock_fetch):
        mock_fetch.return_value = Decimal("3.6303")
        rate = _pln_leg(_MON, "USD")
        assert rate == Decimal("3.6303")
        mock_fetch.assert_called_once_with(_MON, "USD")

    @patch("dinary.services.nbp._fetch_nbp_pln_leg")
    def test_falls_back_to_latest_when_date_specific_404(self, mock_fetch):
        # Date-specific 404 (typical for table-B currencies on a
        # non-Wednesday): walk to the no-date "latest published" form.
        mock_fetch.side_effect = [None, Decimal("0.0362")]
        rate = _pln_leg(_MON, "RSD")
        assert rate == Decimal("0.0362")
        assert mock_fetch.call_args_list == [
            call(_MON, "RSD"),
            call(None, "RSD"),
        ]

    @patch("dinary.services.nbp._fetch_nbp_pln_leg")
    def test_returns_none_when_both_date_and_latest_404(self, mock_fetch):
        mock_fetch.return_value = None
        assert _pln_leg(_MON, "ZZZ") is None


@allure.epic("Services")
@allure.feature("NBP Exchange Rate")
@allure.story("_resolve_from_nbp — bridge through PLN")
class TestResolveFromNbp:
    @patch("dinary.services.nbp._save_db_rate")
    @patch("dinary.services.nbp._pln_leg")
    def test_bridges_two_legs(self, mock_leg, mock_save):
        # X/Y = (X/PLN) / (Y/PLN). RSD/EUR ≈ 0.0362 / 4.27 ≈ 0.008479.
        mock_leg.side_effect = [Decimal("0.0362"), Decimal("4.2700")]
        con = MagicMock()
        rate = _resolve_from_nbp(con, _MON, "RSD", "EUR")
        expected = (Decimal("0.0362") / Decimal("4.2700")).quantize(Decimal("0.000001"))
        assert rate == expected
        mock_save.assert_called_once_with(con, _MON, "RSD", "EUR", expected)

    @patch("dinary.services.nbp._save_db_rate")
    @patch("dinary.services.nbp._pln_leg")
    def test_returns_none_when_source_leg_missing(self, mock_leg, mock_save):
        mock_leg.return_value = None
        con = MagicMock()
        assert _resolve_from_nbp(con, _MON, "ZZZ", "EUR") is None
        # Source-leg miss short-circuits before the target leg query.
        mock_leg.assert_called_once()
        mock_save.assert_not_called()

    @patch("dinary.services.nbp._save_db_rate")
    @patch("dinary.services.nbp._pln_leg")
    def test_returns_none_when_target_leg_missing(self, mock_leg, mock_save):
        # Source resolved but target has no NBP coverage.
        mock_leg.side_effect = [Decimal("0.0362"), None]
        con = MagicMock()
        assert _resolve_from_nbp(con, _MON, "RSD", "ZZZ") is None
        mock_save.assert_not_called()

    @patch("dinary.services.nbp._save_db_rate")
    @patch("dinary.services.nbp._pln_leg")
    def test_identity_pair_short_circuits_without_fetch_or_db_write(self, mock_leg, mock_save):
        # Same source/target: return 1 without touching NBP HTTP and
        # without writing a useless ``(X, X, 1)`` row to ``exchange_rates``.
        # ``get_rate`` already short-circuits identity before reaching
        # this resolver, but defending here too keeps direct callers
        # (and any future ``get_rate`` rewiring) from polluting the DB.
        con = MagicMock()
        rate = _resolve_from_nbp(con, _MON, "EUR", "EUR")
        assert rate == Decimal(1)
        mock_leg.assert_not_called()
        mock_save.assert_not_called()

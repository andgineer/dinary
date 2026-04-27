"""Cross-cutting ``exchange_rates`` plumbing tests.

Pin three contracts that don't fit the per-resolver split:

* ``get_rate`` end-to-end — fetches an EUR→RSD rate, persists it,
  and short-circuits the same-currency identity case without DB
  access.
* Failure caching (DOS guard) — HTTP failures are cached for the
  full TTL so a down upstream is not hammered with retries on
  every incoming request. Do NOT change caching to skip ``None``
  results.
* ``offline=True`` — returns the DB rate without HTTP calls and
  falls back to online resolution only when the DB has no rate.

Per-resolver pipeline tests live in
:file:`test_currency_rates_resolve.py`.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import allure

from dinary.services import db_migrations, ledger_repo, sqlite_types
from dinary.services.exchange_rates import _fetch_frankfurter_rate, get_rate
from dinary.services.nbs import _fetch_nbs_rate

from _currency_rates_helpers import (  # noqa: F401  (autouse + fixtures)
    _MON,
    _RATE,
    _SOURCE,
    _TARGET,
    _clear_ttl_caches,
)


@allure.epic("Services")
@allure.feature("Exchange Rate")
class TestExchangeRate:
    @patch("dinary.services.rate_helpers.httpx.get")
    def test_get_rate_fetches_and_stores(self, mock_get, tmp_path):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"exchange_middle": 117.32}
        mock_resp.raise_for_status = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        ledger_repo.ensure_data_dir()
        db_path = tmp_path / "dinary.db"
        db_migrations.migrate_db(db_path)

        con = sqlite_types.connect(str(db_path))
        try:
            rate = get_rate(con, date(2026, 4, 1), "EUR", "RSD")
            assert rate == Decimal("117.32")

            stored = con.execute(
                "SELECT rate FROM exchange_rates"
                " WHERE source_currency = 'EUR' AND target_currency = 'RSD'"
                " AND date = '2026-04-01'"
            ).fetchone()
            assert stored is not None
        finally:
            con.close()

    def test_get_rate_identity_for_same_currency(self):
        """EUR to EUR should return rate 1 without any DB access."""
        assert get_rate(None, date(2026, 4, 1), "EUR", "EUR") == Decimal(1)

    @patch("dinary.services.rate_helpers.httpx.get")
    def test_get_rate_rsd_to_eur(self, mock_get, tmp_path):
        db_path = tmp_path / "dinary.db"
        db_migrations.migrate_db(db_path)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"exchange_middle": 117.0}
        mock_get.return_value = mock_resp

        con = sqlite_types.connect(str(db_path))
        try:
            rate = get_rate(con, date(2026, 4, 1), "RSD", "EUR")
            # NBS gives 1 EUR = 117 RSD → 1 RSD = 1/117 EUR
            assert (Decimal("11700") * rate).quantize(Decimal("0.01")) == Decimal("100.00")
        finally:
            con.close()


@allure.epic("Services")
@allure.feature("Exchange Rate")
@allure.story("Failure caching — DOS protection")
class TestFailureCaching:
    """HTTP failures MUST be cached for the full TTL so a down upstream is not
    hammered with retries on every incoming request.

    This is intentional — do NOT change caching to skip ``None`` results.
    These tests exist to catch that mistake.
    """

    @patch("dinary.services.nbs._get_json_or_none")
    def test_nbs_failure_is_cached(self, mock_json):
        mock_json.return_value = None
        assert _fetch_nbs_rate(_MON, _SOURCE) is None
        assert _fetch_nbs_rate(_MON, _SOURCE) is None
        mock_json.assert_called_once()

    @patch("dinary.services.exchange_rates._get_json_or_none")
    def test_frankfurter_failure_is_cached(self, mock_json):
        mock_json.return_value = None
        assert _fetch_frankfurter_rate(_MON, "USD", "EUR") is None
        assert _fetch_frankfurter_rate(_MON, "USD", "EUR") is None
        mock_json.assert_called_once()


@allure.epic("Services")
@allure.feature("Exchange Rate")
@allure.story("get_rate — offline mode")
class TestGetRateOffline:
    """offline=True returns DB rate without HTTP calls; falls back to
    online resolution only when DB has no rate."""

    @patch("dinary.services.exchange_rates._resolve_from_nbs")
    @patch("dinary.services.exchange_rates._get_db_rate")
    def test_offline_returns_db_rate_without_fetch(self, mock_db, mock_nbs):
        mock_db.return_value = _RATE
        con = MagicMock()
        result = get_rate(con, _MON, _SOURCE, _TARGET, offline=True)
        assert result == _RATE
        mock_nbs.assert_not_called()

    @patch("dinary.services.exchange_rates._resolve_from_frankfurter")
    @patch("dinary.services.exchange_rates._resolve_from_nbs")
    @patch("dinary.services.exchange_rates._get_db_rate")
    def test_offline_falls_back_to_online_when_db_empty(self, mock_db, mock_nbs, mock_frank):
        mock_db.return_value = None
        mock_nbs.return_value = _RATE
        con = MagicMock()
        result = get_rate(con, _MON, _SOURCE, _TARGET, offline=True)
        assert result == _RATE
        mock_nbs.assert_called_once()

    @patch("dinary.services.exchange_rates._resolve_from_nbs")
    @patch("dinary.services.exchange_rates._get_db_rate")
    def test_online_does_not_check_db_first(self, mock_db, mock_nbs):
        mock_nbs.return_value = _RATE
        con = MagicMock()
        result = get_rate(con, _MON, _SOURCE, _TARGET, offline=False)
        assert result == _RATE
        mock_db.assert_not_called()

    def test_offline_identity_no_db_call(self):
        result = get_rate(None, _MON, "EUR", "EUR", offline=True)
        assert result == Decimal(1)

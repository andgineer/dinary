"""Cross-cutting ``exchange_rates`` plumbing tests.

Pin three contracts that don't fit the per-resolver split:

* ``get_rate`` end-to-end — fetches a real rate, persists it, and
  short-circuits the same-currency identity case without DB
  access.
* Failure caching (DOS guard) — HTTP failures from each upstream
  are cached for the full TTL so a down upstream is not hammered
  with retries on every incoming request. Do NOT change caching
  to skip ``None`` results.
* The full NBS → NBP fallback chain — pair shapes, bridge
  invocations, and the ``offline=True`` short-circuit.

Per-resolver pipeline tests live in
:file:`test_currency_rates_resolve.py` (NBS) and
:file:`test_currency_rates_nbp.py` (NBP).
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import allure
import pytest

from dinary.db import db_migrations, storage
from dinary.adapters.exchange_rates import get_rate
from dinary.adapters.nbp import _fetch_nbp_pln_leg
from dinary.adapters.nbs import _fetch_nbs_rate

from _currency_rates_helpers import (  # noqa: F401  (autouse + fixtures)
    _MON,
    _RATE,
    _SOURCE,
    _TARGET,
    _clear_ttl_caches,
)


@allure.epic("Currencies")
@allure.feature("Rate resolution")
class TestExchangeRate:
    @patch("dinary.adapters.rate_helpers.httpx.get")
    def test_get_rate_fetches_and_stores(self, mock_get, tmp_path):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"exchange_middle": 117.32}
        mock_resp.raise_for_status = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        storage.ensure_data_dir()
        db_path = tmp_path / "dinary.db"
        db_migrations.migrate_db(db_path)

        con = storage.connect(str(db_path))
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

    @patch("dinary.adapters.rate_helpers.httpx.get")
    def test_get_rate_rsd_to_eur(self, mock_get, tmp_path):
        db_path = tmp_path / "dinary.db"
        db_migrations.migrate_db(db_path)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"exchange_middle": 117.0}
        mock_get.return_value = mock_resp

        con = storage.connect(str(db_path))
        try:
            rate = get_rate(con, date(2026, 4, 1), "RSD", "EUR")
            # NBS gives 1 EUR = 117 RSD → 1 RSD = 1/117 EUR
            assert (Decimal("11700") * rate).quantize(Decimal("0.01")) == Decimal("100.00")
        finally:
            con.close()


@allure.epic("Currencies")
@allure.feature("Rate resolution")
@allure.story("Failure caching — DOS protection")
class TestFailureCaching:
    """HTTP failures MUST be cached for the full TTL so a down upstream is not
    hammered with retries on every incoming request.

    This is intentional — do NOT change caching to skip ``None`` results.
    These tests exist to catch that mistake.
    """

    @patch("dinary.adapters.nbs._get_json_or_none")
    def test_nbs_failure_is_cached(self, mock_json):
        mock_json.return_value = None
        assert _fetch_nbs_rate(_MON, _SOURCE) is None
        assert _fetch_nbs_rate(_MON, _SOURCE) is None
        mock_json.assert_called_once()

    @patch("dinary.adapters.nbp._get_json_or_none")
    def test_nbp_failure_is_cached(self, mock_json):
        mock_json.return_value = None
        # Both table-A and table-B paths run on a single NBP miss
        # (see ``_fetch_nbp_pln_leg``); the second invocation must
        # not re-issue either of them.
        assert _fetch_nbp_pln_leg(_MON, "USD") is None
        first_call_count = mock_json.call_count
        assert _fetch_nbp_pln_leg(_MON, "USD") is None
        assert mock_json.call_count == first_call_count


@allure.epic("Currencies")
@allure.feature("Rate resolution")
@allure.story("get_rate — NBS → NBP fallback chain")
class TestGetRateFallbackChain:
    """The full multi-source resolution policy: NBS first (direct
    for RSD-pairs, RSD-bridge for the rest), then NBP (PLN-bridge)
    when NBS doesn't serve the pair.
    """

    @patch("dinary.adapters.exchange_rates.resolve_from_nbp")
    @patch("dinary.adapters.exchange_rates.resolve_from_nbs")
    def test_rsd_pair_uses_nbs_direct(self, mock_nbs, mock_nbp):
        # RSD pair: NBS resolves directly. NBP must not be touched.
        mock_nbs.return_value = Decimal("117.32")
        con = MagicMock()

        rate = get_rate(con, _MON, "EUR", "RSD")

        assert rate == Decimal("117.32")
        mock_nbp.assert_not_called()
        mock_nbs.assert_called_once_with(con, _MON, "EUR", "RSD")

    @patch("dinary.adapters.exchange_rates.save_db_rate")
    @patch("dinary.adapters.exchange_rates.resolve_from_nbp")
    @patch("dinary.adapters.exchange_rates.resolve_from_nbs")
    def test_non_rsd_pair_bridges_through_rsd_via_nbs(
        self,
        mock_nbs,
        mock_nbp,
        mock_save,
    ):
        # Non-RSD pair: bridge through RSD via NBS. NBP only runs if
        # the bridge fails — but here both NBS legs succeed.
        mock_nbs.side_effect = [Decimal("60.0167"), Decimal("0.008519")]
        con = MagicMock()

        rate = get_rate(con, _MON, "BAM", "EUR")

        expected = (Decimal("60.0167") * Decimal("0.008519")).quantize(Decimal("0.000001"))
        assert rate == expected
        # NBS hit twice (BAM/RSD, then RSD/EUR), NBP untouched.
        assert mock_nbs.call_count == 2  # noqa: PLR2004
        mock_nbp.assert_not_called()
        mock_save.assert_called_once_with(con, _MON, "BAM", "EUR", expected)

    @patch("dinary.adapters.exchange_rates.resolve_from_nbp")
    @patch("dinary.adapters.exchange_rates.resolve_from_nbs")
    def test_rsd_pair_falls_back_to_nbp_when_nbs_unavailable(
        self,
        mock_nbs,
        mock_nbp,
        caplog,
    ):
        # NBS is down (returns None for the direct RSD-pair lookup);
        # NBP picks up via PLN bridge.
        mock_nbs.return_value = None
        mock_nbp.return_value = Decimal("0.008519")
        con = MagicMock()

        with caplog.at_level("WARNING", logger="dinary.adapters.exchange_rates"):
            rate = get_rate(con, _MON, "RSD", "EUR")

        assert rate == Decimal("0.008519")
        mock_nbp.assert_called_once_with(con, _MON, "RSD", "EUR")
        # Surface NBS outages in journald so a sustained primary
        # degradation is visible instead of being silently masked.
        assert any(
            "falling back to NBP" in r.message and r.levelname == "WARNING" for r in caplog.records
        )

    @patch("dinary.adapters.exchange_rates.resolve_from_nbp")
    @patch("dinary.adapters.exchange_rates.resolve_from_nbs")
    def test_non_rsd_pair_falls_back_to_nbp_when_nbs_bridge_fails(
        self,
        mock_nbs,
        mock_nbp,
    ):
        # NBS doesn't list the source side (or one leg simply has no
        # rate); NBP catches it via PLN bridge.
        mock_nbs.return_value = None
        mock_nbp.return_value = Decimal("0.5113")
        con = MagicMock()

        rate = get_rate(con, _MON, "BAM", "EUR")

        assert rate == Decimal("0.5113")
        mock_nbp.assert_called_once_with(con, _MON, "BAM", "EUR")

    @patch("dinary.adapters.exchange_rates.resolve_from_nbp")
    @patch("dinary.adapters.exchange_rates.resolve_from_nbs")
    def test_raises_when_both_sources_fail(self, mock_nbs, mock_nbp):
        # Surface ValueError so ``POST /api/expenses`` can propagate
        # an error instead of silently returning a stale or zero rate.
        mock_nbs.return_value = None
        mock_nbp.return_value = None
        con = MagicMock()

        with pytest.raises(ValueError, match="Could not find rate"):
            get_rate(con, _MON, "BAM", "EUR")


@allure.epic("Currencies")
@allure.feature("Rate resolution")
@allure.story("get_rate — offline mode")
class TestGetRateOffline:
    """offline=True returns DB rate without HTTP calls; falls back to
    online resolution only when DB has no rate."""

    @patch("dinary.adapters.exchange_rates.resolve_from_nbs")
    @patch("dinary.adapters.exchange_rates.get_db_rate")
    def test_offline_returns_db_rate_without_fetch(self, mock_db, mock_nbs):
        mock_db.return_value = _RATE
        con = MagicMock()
        result = get_rate(con, _MON, _SOURCE, _TARGET, offline=True)
        assert result == _RATE
        mock_nbs.assert_not_called()

    @patch("dinary.adapters.exchange_rates.resolve_from_nbp")
    @patch("dinary.adapters.exchange_rates.resolve_from_nbs")
    @patch("dinary.adapters.exchange_rates.get_db_rate")
    def test_offline_falls_back_to_online_when_db_empty(
        self,
        mock_db,
        mock_nbs,
        mock_nbp,
    ):
        mock_db.return_value = None
        mock_nbs.return_value = _RATE
        con = MagicMock()
        result = get_rate(con, _MON, _SOURCE, _TARGET, offline=True)
        assert result == _RATE
        mock_nbs.assert_called_once()
        mock_nbp.assert_not_called()

    @patch("dinary.adapters.exchange_rates.resolve_from_nbs")
    @patch("dinary.adapters.exchange_rates.get_db_rate")
    def test_online_does_not_check_db_first(self, mock_db, mock_nbs):
        mock_nbs.return_value = _RATE
        con = MagicMock()
        result = get_rate(con, _MON, _SOURCE, _TARGET, offline=False)
        assert result == _RATE
        mock_db.assert_not_called()

    def test_offline_identity_no_db_call(self):
        result = get_rate(None, _MON, "EUR", "EUR", offline=True)
        assert result == Decimal(1)

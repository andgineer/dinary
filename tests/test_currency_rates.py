from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, call, patch

import allure
import pytest

from dinary.services import db_migrations, ledger_repo, sqlite_types
from dinary.services.exchange_rates import (
    _fetch_frankfurter_rate,
    _resolve_from_frankfurter,
    get_rate,
)
from dinary.services.nbs import _fetch_nbs_rate, _resolve_from_nbs


@pytest.fixture(autouse=True)
def _clear_ttl_caches():
    """Clear in-memory TTL caches between tests to avoid cross-test pollution."""
    _fetch_nbs_rate.cache.clear()
    _fetch_frankfurter_rate.cache.clear()
    yield
    _fetch_nbs_rate.cache.clear()
    _fetch_frankfurter_rate.cache.clear()


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


# --- _resolve_from_nbs unit tests ---
# All external I/O (_get_db_rate, _save_db_rate, _fetch_nbs_rate) is mocked.

# 2025-02-24 Mon, 25 Tue, 28 Fri, Mar 1 Sat, Mar 2 Sun
_MON = date(2025, 2, 24)
_TUE = date(2025, 2, 25)
_FRI = date(2025, 2, 28)
_SAT = date(2025, 3, 1)
_SUN = date(2025, 3, 2)
# 2025-01-01 Wed — Serbian New Year holiday
_HOLIDAY = date(2025, 1, 1)
_DAY_BEFORE_HOLIDAY = date(2024, 12, 31)

_SOURCE = "EUR"
_TARGET = "RSD"
_RATE = Decimal("117.32")
_CON = MagicMock(name="con")


@pytest.fixture
def nbs_mocks():
    with (
        patch("dinary.services.nbs._get_db_rate") as get_db,
        patch("dinary.services.nbs._save_db_rate") as save_db,
        patch("dinary.services.nbs._fetch_nbs_rate") as fetch_nbs,
    ):
        get_db.return_value = None
        fetch_nbs.return_value = None
        yield get_db, save_db, fetch_nbs


@allure.epic("Services")
@allure.feature("NBS Exchange Rate")
@allure.story("_resolve_from_nbs — working day, direct hit")
class TestResolveWorkingDayHit:
    """rate_date is a working day, API returns a rate for it."""

    def test_returns_rate(self, nbs_mocks):
        _, _, fetch_nbs = nbs_mocks
        fetch_nbs.return_value = _RATE
        assert _resolve_from_nbs(_CON, _MON, _SOURCE, _TARGET) == _RATE

    def test_stores_under_rate_date(self, nbs_mocks):
        _, save_db, fetch_nbs = nbs_mocks
        fetch_nbs.return_value = _RATE
        _resolve_from_nbs(_CON, _MON, _SOURCE, _TARGET)
        save_db.assert_called_once_with(_CON, _MON, _SOURCE, _TARGET, _RATE)

    def test_fetches_for_rate_date(self, nbs_mocks):
        _, _, fetch_nbs = nbs_mocks
        fetch_nbs.return_value = _RATE
        _resolve_from_nbs(_CON, _MON, _SOURCE, _TARGET)
        fetch_nbs.assert_called_once_with(_MON, _SOURCE)


@allure.epic("Services")
@allure.feature("NBS Exchange Rate")
@allure.story("_resolve_from_nbs — working day, DB hit")
class TestResolveWorkingDayCacheHit:
    """rate_date is a working day, rate already in DB."""

    def test_returns_cached(self, nbs_mocks):
        get_db, _, _ = nbs_mocks
        get_db.return_value = _RATE
        assert _resolve_from_nbs(_CON, _MON, _SOURCE, _TARGET) == _RATE

    def test_no_fetch(self, nbs_mocks):
        get_db, _, fetch_nbs = nbs_mocks
        get_db.return_value = _RATE
        _resolve_from_nbs(_CON, _MON, _SOURCE, _TARGET)
        fetch_nbs.assert_not_called()

    def test_no_save(self, nbs_mocks):
        get_db, save_db, _ = nbs_mocks
        get_db.return_value = _RATE
        _resolve_from_nbs(_CON, _MON, _SOURCE, _TARGET)
        save_db.assert_not_called()


@allure.epic("Services")
@allure.feature("NBS Exchange Rate")
@allure.story("_resolve_from_nbs — working day, pre-publication (before 08:00)")
class TestResolveWorkingDayPrePublication:
    """rate_date is a working day but API has no rate yet (before 08:00).

    Should return previous day's rate but NOT store under rate_date.
    """

    def test_returns_previous_day_rate(self, nbs_mocks):
        _, _, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == _MON else None
        assert _resolve_from_nbs(_CON, _TUE, _SOURCE, _TARGET) == _RATE

    def test_stores_only_under_previous_day(self, nbs_mocks):
        _, save_db, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == _MON else None
        _resolve_from_nbs(_CON, _TUE, _SOURCE, _TARGET)
        save_db.assert_called_once_with(_CON, _MON, _SOURCE, _TARGET, _RATE)

    def test_previous_day_in_db(self, nbs_mocks):
        get_db, save_db, _ = nbs_mocks
        get_db.side_effect = lambda con, d, s, t: _RATE if d == _MON else None
        assert _resolve_from_nbs(_CON, _TUE, _SOURCE, _TARGET) == _RATE
        save_db.assert_not_called()


@allure.epic("Services")
@allure.feature("NBS Exchange Rate")
@allure.story("_resolve_from_nbs — weekend")
class TestResolveWeekend:
    """rate_date is Saturday or Sunday — walks back to Friday."""

    def test_saturday_returns_friday_rate(self, nbs_mocks):
        _, _, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == _FRI else None
        assert _resolve_from_nbs(_CON, _SAT, _SOURCE, _TARGET) == _RATE

    def test_sunday_returns_friday_rate(self, nbs_mocks):
        _, _, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == _FRI else None
        assert _resolve_from_nbs(_CON, _SUN, _SOURCE, _TARGET) == _RATE

    def test_saturday_stores_under_friday_and_saturday(self, nbs_mocks):
        _, save_db, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == _FRI else None
        _resolve_from_nbs(_CON, _SAT, _SOURCE, _TARGET)
        assert save_db.call_args_list == [
            call(_CON, _FRI, _SOURCE, _TARGET, _RATE),
            call(_CON, _SAT, _SOURCE, _TARGET, _RATE),
        ]

    def test_sunday_stores_under_friday_and_sunday(self, nbs_mocks):
        _, save_db, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == _FRI else None
        _resolve_from_nbs(_CON, _SUN, _SOURCE, _TARGET)
        assert save_db.call_args_list == [
            call(_CON, _FRI, _SOURCE, _TARGET, _RATE),
            call(_CON, _SUN, _SOURCE, _TARGET, _RATE),
        ]

    def test_no_fetch_for_weekend_days(self, nbs_mocks):
        _, _, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == _FRI else None
        _resolve_from_nbs(_CON, _SAT, _SOURCE, _TARGET)
        fetch_nbs.assert_called_once_with(_FRI, _SOURCE)

    def test_friday_in_db_saves_under_saturday(self, nbs_mocks):
        get_db, save_db, _ = nbs_mocks
        get_db.side_effect = lambda con, d, s, t: _RATE if d == _FRI else None
        _resolve_from_nbs(_CON, _SAT, _SOURCE, _TARGET)
        save_db.assert_called_once_with(_CON, _SAT, _SOURCE, _TARGET, _RATE)


@allure.epic("Services")
@allure.feature("NBS Exchange Rate")
@allure.story("_resolve_from_nbs — weekend, stale walkback")
class TestResolveWeekendStaleWalkback:
    """Weekend, Friday has no rate — walks back to Thursday.

    Rate is older than the immediately previous working day,
    so it must NOT be aliased under rate_date.
    """

    _THU = date(2025, 2, 27)

    def test_returns_thursday_rate(self, nbs_mocks):
        _, _, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == self._THU else None
        assert _resolve_from_nbs(_CON, _SAT, _SOURCE, _TARGET) == _RATE

    def test_stores_only_under_thursday(self, nbs_mocks):
        _, save_db, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == self._THU else None
        _resolve_from_nbs(_CON, _SAT, _SOURCE, _TARGET)
        save_db.assert_called_once_with(_CON, self._THU, _SOURCE, _TARGET, _RATE)

    def test_db_thursday_no_alias(self, nbs_mocks):
        """Thursday rate in DB, Friday had no rate — no alias under Saturday."""
        get_db, save_db, fetch_nbs = nbs_mocks
        get_db.side_effect = lambda con, d, s, t: _RATE if d == self._THU else None
        fetch_nbs.return_value = None
        _resolve_from_nbs(_CON, _SAT, _SOURCE, _TARGET)
        save_db.assert_not_called()


@allure.epic("Services")
@allure.feature("NBS Exchange Rate")
@allure.story("_resolve_from_nbs — weekend, Saturday already in DB")
class TestResolveWeekendSaturdayCached:
    """Saturday itself has a DB rate — return it, no alias save needed."""

    def test_returns_db_saturday(self, nbs_mocks):
        get_db, save_db, _ = nbs_mocks
        get_db.side_effect = lambda con, d, s, t: _RATE if d == _SAT else None
        assert _resolve_from_nbs(_CON, _SAT, _SOURCE, _TARGET) == _RATE
        save_db.assert_not_called()


@allure.epic("Services")
@allure.feature("NBS Exchange Rate")
@allure.story("_resolve_from_nbs — working day, fetch error then walkback")
class TestResolveWorkingDayErrorWalkback:
    """Working day, today's fetch fails — walks back, does NOT alias-store."""

    def test_no_alias_on_working_day(self, nbs_mocks):
        _, save_db, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == _MON else None
        _resolve_from_nbs(_CON, _TUE, _SOURCE, _TARGET)
        save_db.assert_called_once_with(_CON, _MON, _SOURCE, _TARGET, _RATE)


@allure.epic("Services")
@allure.feature("NBS Exchange Rate")
@allure.story("_resolve_from_nbs — holiday")
class TestResolveHoliday:
    """rate_date is a weekday holiday — walks back to previous working day."""

    def test_returns_previous_working_day_rate(self, nbs_mocks):
        _, _, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == _DAY_BEFORE_HOLIDAY else None
        assert _resolve_from_nbs(_CON, _HOLIDAY, _SOURCE, _TARGET) == _RATE

    def test_stores_under_both_dates(self, nbs_mocks):
        _, save_db, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == _DAY_BEFORE_HOLIDAY else None
        _resolve_from_nbs(_CON, _HOLIDAY, _SOURCE, _TARGET)
        assert save_db.call_args_list == [
            call(_CON, _DAY_BEFORE_HOLIDAY, _SOURCE, _TARGET, _RATE),
            call(_CON, _HOLIDAY, _SOURCE, _TARGET, _RATE),
        ]


@allure.epic("Services")
@allure.feature("NBS Exchange Rate")
@allure.story("_resolve_from_nbs — no rate found")
class TestResolveNoRateFound:
    """No rate found within 10 days."""

    def test_returns_none(self, nbs_mocks):
        assert _resolve_from_nbs(_CON, _MON, _SOURCE, _TARGET) is None

    def test_no_save(self, nbs_mocks):
        _, save_db, _ = nbs_mocks
        _resolve_from_nbs(_CON, _MON, _SOURCE, _TARGET)
        save_db.assert_not_called()


# --- Frankfurter resolution tests ---


@allure.epic("Services")
@allure.feature("Exchange Rate")
@allure.story("Frankfurter — direct fetch")
class TestFrankfurterDirect:
    """Frankfurter API used for non-RSD pairs."""

    @patch("dinary.services.exchange_rates._fetch_frankfurter_rate")
    def test_returns_rate(self, mock_fetch):
        mock_fetch.return_value = Decimal("1.08")
        con = MagicMock()
        result = _resolve_from_frankfurter(con, _MON, "USD", "EUR")
        assert result == Decimal("1.08")

    @patch("dinary.services.exchange_rates._fetch_frankfurter_rate")
    @patch("dinary.services.exchange_rates._get_latest_db_rate")
    def test_fallback_to_last_known(self, mock_latest, mock_fetch):
        mock_fetch.return_value = None
        mock_latest.return_value = Decimal("1.07")
        con = MagicMock()
        result = _resolve_from_frankfurter(con, _MON, "USD", "EUR")
        assert result == Decimal("1.07")

    @patch("dinary.services.exchange_rates._fetch_frankfurter_rate")
    @patch("dinary.services.exchange_rates._get_latest_db_rate")
    def test_returns_none_when_no_data(self, mock_latest, mock_fetch):
        mock_fetch.return_value = None
        mock_latest.return_value = None
        con = MagicMock()
        result = _resolve_from_frankfurter(con, _MON, "USD", "EUR")
        assert result is None


# --- failure caching (DOS protection) tests ---


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


# --- offline mode tests ---


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

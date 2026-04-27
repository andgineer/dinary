"""Resolution-pipeline tests for ``exchange_rates`` / ``nbs``.

Pin ``_resolve_from_nbs`` across the full date-shape matrix —
working-day hit / DB-cache hit / pre-publication walkback,
weekend → Friday alias, holiday → previous-working-day alias,
stale-walkback that must NOT alias under the requested date, and
the no-rate-within-10-days terminal case — plus the Frankfurter
fallback used for non-RSD pairs.

Sibling :file:`test_currency_rates_misc.py` covers the
end-to-end ``get_rate`` plumbing, failure-caching DOS guard, and
``offline=True`` mode short-circuits.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, call, patch

import allure

from dinary.services.exchange_rates import _resolve_from_frankfurter
from dinary.services.nbs import _resolve_from_nbs

from _currency_rates_helpers import (  # noqa: F401  (autouse + fixtures)
    _CON,
    _DAY_BEFORE_HOLIDAY,
    _FRI,
    _HOLIDAY,
    _MON,
    _RATE,
    _SAT,
    _SOURCE,
    _SUN,
    _TARGET,
    _TUE,
    _clear_ttl_caches,
    nbs_mocks,
)


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

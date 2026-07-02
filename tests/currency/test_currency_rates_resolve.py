from datetime import date
from unittest.mock import call

import allure

from dinary.adapters.nbs import resolve_from_nbs

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


@allure.epic("Currencies")
@allure.feature("NBS rates")
@allure.story("resolve_from_nbs — working day, direct hit")
class TestResolveWorkingDayHit:
    def test_returns_rate(self, nbs_mocks):
        _, _, fetch_nbs = nbs_mocks
        fetch_nbs.return_value = _RATE
        assert resolve_from_nbs(_CON, _MON, _SOURCE, _TARGET) == _RATE

    def test_stores_under_rate_date(self, nbs_mocks):
        _, save_db, fetch_nbs = nbs_mocks
        fetch_nbs.return_value = _RATE
        resolve_from_nbs(_CON, _MON, _SOURCE, _TARGET)
        save_db.assert_called_once_with(_CON, _MON, _SOURCE, _TARGET, _RATE)

    def test_fetches_for_rate_date(self, nbs_mocks):
        _, _, fetch_nbs = nbs_mocks
        fetch_nbs.return_value = _RATE
        resolve_from_nbs(_CON, _MON, _SOURCE, _TARGET)
        fetch_nbs.assert_called_once_with(_MON, _SOURCE)


@allure.epic("Currencies")
@allure.feature("NBS rates")
@allure.story("resolve_from_nbs — working day, DB hit")
class TestResolveWorkingDayCacheHit:
    def test_returns_cached(self, nbs_mocks):
        get_db, _, _ = nbs_mocks
        get_db.return_value = _RATE
        assert resolve_from_nbs(_CON, _MON, _SOURCE, _TARGET) == _RATE

    def test_no_fetch(self, nbs_mocks):
        get_db, _, fetch_nbs = nbs_mocks
        get_db.return_value = _RATE
        resolve_from_nbs(_CON, _MON, _SOURCE, _TARGET)
        fetch_nbs.assert_not_called()

    def test_no_save(self, nbs_mocks):
        get_db, save_db, _ = nbs_mocks
        get_db.return_value = _RATE
        resolve_from_nbs(_CON, _MON, _SOURCE, _TARGET)
        save_db.assert_not_called()


@allure.epic("Currencies")
@allure.feature("NBS rates")
@allure.story("resolve_from_nbs — working day, pre-publication (before 08:00)")
class TestResolveWorkingDayPrePublication:
    def test_returns_previous_day_rate(self, nbs_mocks):
        _, _, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == _MON else None
        assert resolve_from_nbs(_CON, _TUE, _SOURCE, _TARGET) == _RATE

    def test_stores_only_under_previous_day(self, nbs_mocks):
        _, save_db, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == _MON else None
        resolve_from_nbs(_CON, _TUE, _SOURCE, _TARGET)
        save_db.assert_called_once_with(_CON, _MON, _SOURCE, _TARGET, _RATE)

    def test_previous_day_in_db(self, nbs_mocks):
        get_db, save_db, _ = nbs_mocks
        get_db.side_effect = lambda con, d, s, t: _RATE if d == _MON else None
        assert resolve_from_nbs(_CON, _TUE, _SOURCE, _TARGET) == _RATE
        save_db.assert_not_called()


@allure.epic("Currencies")
@allure.feature("NBS rates")
@allure.story("resolve_from_nbs — weekend")
class TestResolveWeekend:
    def test_saturday_returns_friday_rate(self, nbs_mocks):
        _, _, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == _FRI else None
        assert resolve_from_nbs(_CON, _SAT, _SOURCE, _TARGET) == _RATE

    def test_sunday_returns_friday_rate(self, nbs_mocks):
        _, _, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == _FRI else None
        assert resolve_from_nbs(_CON, _SUN, _SOURCE, _TARGET) == _RATE

    def test_saturday_stores_under_friday_and_saturday(self, nbs_mocks):
        _, save_db, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == _FRI else None
        resolve_from_nbs(_CON, _SAT, _SOURCE, _TARGET)
        assert save_db.call_args_list == [
            call(_CON, _FRI, _SOURCE, _TARGET, _RATE),
            call(_CON, _SAT, _SOURCE, _TARGET, _RATE),
        ]

    def test_sunday_stores_under_friday_and_sunday(self, nbs_mocks):
        _, save_db, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == _FRI else None
        resolve_from_nbs(_CON, _SUN, _SOURCE, _TARGET)
        assert save_db.call_args_list == [
            call(_CON, _FRI, _SOURCE, _TARGET, _RATE),
            call(_CON, _SUN, _SOURCE, _TARGET, _RATE),
        ]

    def test_no_fetch_for_weekend_days(self, nbs_mocks):
        _, _, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == _FRI else None
        resolve_from_nbs(_CON, _SAT, _SOURCE, _TARGET)
        fetch_nbs.assert_called_once_with(_FRI, _SOURCE)

    def test_friday_in_db_saves_under_saturday(self, nbs_mocks):
        get_db, save_db, _ = nbs_mocks
        get_db.side_effect = lambda con, d, s, t: _RATE if d == _FRI else None
        resolve_from_nbs(_CON, _SAT, _SOURCE, _TARGET)
        save_db.assert_called_once_with(_CON, _SAT, _SOURCE, _TARGET, _RATE)


@allure.epic("Currencies")
@allure.feature("NBS rates")
@allure.story("resolve_from_nbs — weekend, stale walkback")
class TestResolveWeekendStaleWalkback:
    _THU = date(2025, 2, 27)

    def test_returns_thursday_rate(self, nbs_mocks):
        _, _, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == self._THU else None
        assert resolve_from_nbs(_CON, _SAT, _SOURCE, _TARGET) == _RATE

    def test_stores_only_under_thursday(self, nbs_mocks):
        _, save_db, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == self._THU else None
        resolve_from_nbs(_CON, _SAT, _SOURCE, _TARGET)
        save_db.assert_called_once_with(_CON, self._THU, _SOURCE, _TARGET, _RATE)

    def test_db_thursday_no_alias(self, nbs_mocks):
        get_db, save_db, fetch_nbs = nbs_mocks
        get_db.side_effect = lambda con, d, s, t: _RATE if d == self._THU else None
        fetch_nbs.return_value = None
        resolve_from_nbs(_CON, _SAT, _SOURCE, _TARGET)
        save_db.assert_not_called()


@allure.epic("Currencies")
@allure.feature("NBS rates")
@allure.story("resolve_from_nbs — weekend, Saturday already in DB")
class TestResolveWeekendSaturdayCached:
    def test_returns_db_saturday(self, nbs_mocks):
        get_db, save_db, _ = nbs_mocks
        get_db.side_effect = lambda con, d, s, t: _RATE if d == _SAT else None
        assert resolve_from_nbs(_CON, _SAT, _SOURCE, _TARGET) == _RATE
        save_db.assert_not_called()


@allure.epic("Currencies")
@allure.feature("NBS rates")
@allure.story("resolve_from_nbs — holiday")
class TestResolveHoliday:
    def test_returns_previous_working_day_rate(self, nbs_mocks):
        _, _, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == _DAY_BEFORE_HOLIDAY else None
        assert resolve_from_nbs(_CON, _HOLIDAY, _SOURCE, _TARGET) == _RATE

    def test_stores_under_both_dates(self, nbs_mocks):
        _, save_db, fetch_nbs = nbs_mocks
        fetch_nbs.side_effect = lambda d, c: _RATE if d == _DAY_BEFORE_HOLIDAY else None
        resolve_from_nbs(_CON, _HOLIDAY, _SOURCE, _TARGET)
        assert save_db.call_args_list == [
            call(_CON, _DAY_BEFORE_HOLIDAY, _SOURCE, _TARGET, _RATE),
            call(_CON, _HOLIDAY, _SOURCE, _TARGET, _RATE),
        ]


@allure.epic("Currencies")
@allure.feature("NBS rates")
@allure.story("resolve_from_nbs — no rate found")
class TestResolveNoRateFound:
    def test_returns_none(self, nbs_mocks):
        assert resolve_from_nbs(_CON, _MON, _SOURCE, _TARGET) is None

    def test_no_save(self, nbs_mocks):
        _, save_db, _ = nbs_mocks
        resolve_from_nbs(_CON, _MON, _SOURCE, _TARGET)
        save_db.assert_not_called()

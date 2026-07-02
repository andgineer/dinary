import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest

from dinary.config import settings
from dinary.background.rate_prefetch.task import (
    _BELGRADE,
    _RETRY_INTERVAL_SEC,
    _seconds_until_prefetch_hour,
    rate_prefetch_task,
)

_RATE = Decimal("117.32")


def _belgrade_dt(hour: int, *, weekday: int = 0) -> datetime:
    base = date(2025, 2, 24)  # Monday
    d = base + timedelta(days=weekday)
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=_BELGRADE)


@allure.epic("Currencies")
@allure.feature("Background tasks")
@allure.story("_seconds_until_prefetch_hour")
class TestSecondsUntilPrefetchHour:
    def test_before_prefetch_hour_returns_hours_until(self):
        with patch("dinary.background.rate_prefetch.task.datetime") as mock_dt:
            mock_dt.now.return_value = _belgrade_dt(6)
            secs = _seconds_until_prefetch_hour()
            assert 7100 < secs < 7300  # ~2 hours

    def test_after_prefetch_hour_returns_until_tomorrow(self):
        with patch("dinary.background.rate_prefetch.task.datetime") as mock_dt:
            mock_dt.now.return_value = _belgrade_dt(10)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            secs = _seconds_until_prefetch_hour()
            assert 78000 < secs < 80000  # ~22 hours

    def test_never_less_than_retry_interval(self):
        with patch("dinary.background.rate_prefetch.task.datetime") as mock_dt:
            # Just before 08:00 — raw delta is tiny but floor is _RETRY_INTERVAL_SEC
            mock_dt.now.return_value = _belgrade_dt(8).replace(second=0) - timedelta(seconds=10)
            secs = _seconds_until_prefetch_hour()
            assert secs >= _RETRY_INTERVAL_SEC


@allure.epic("Currencies")
@allure.feature("Background tasks")
@allure.story("Before publication hour")
class TestBeforePublicationHour:
    def test_no_fetch_before_8am(self):
        early_morning = _belgrade_dt(6)

        with (
            patch(
                "dinary.background.rate_prefetch.task.datetime",
            ) as mock_dt,
            patch(
                "dinary.background.rate_prefetch.task.storage",
            ) as mock_repo,
            patch(
                "dinary.background.rate_prefetch.task.get_rate",
            ) as mock_get_rate,
            patch(
                "dinary.background.rate_prefetch.task._seconds_until_prefetch_hour",
                return_value=7200,
            ),
            patch(
                "dinary.background.rate_prefetch.task.asyncio.sleep",
                new_callable=AsyncMock,
                side_effect=[None, asyncio.CancelledError],
            ) as mock_sleep,
        ):
            mock_dt.now.return_value = early_morning
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            with pytest.raises(asyncio.CancelledError):
                asyncio.run(rate_prefetch_task())

            mock_get_rate.assert_not_called()
            mock_repo.connection.assert_not_called()
            mock_sleep.assert_awaited_with(7200)


@allure.epic("Currencies")
@allure.feature("Background tasks")
@allure.story("Working day — fetches rate")
class TestWorkingDayFetch:
    def test_fetches_rate(self):
        monday_9am = _belgrade_dt(9)
        con = MagicMock()

        with (
            patch(
                "dinary.background.rate_prefetch.task.datetime",
            ) as mock_dt,
            patch(
                "dinary.background.rate_prefetch.task.storage",
            ) as mock_repo,
            patch(
                "dinary.background.rate_prefetch.task.get_db_rate",
                # first call: no existing rate; second call: verify write succeeded
                side_effect=[None, _RATE],
            ),
            patch(
                "dinary.background.rate_prefetch.task.get_rate",
                return_value=_RATE,
            ) as mock_get_rate,
            patch(
                "dinary.background.rate_prefetch.task._seconds_until_prefetch_hour",
                return_value=80000,
            ),
            patch(
                "dinary.background.rate_prefetch.task.asyncio.sleep",
                new_callable=AsyncMock,
                side_effect=asyncio.CancelledError,
            ),
        ):
            mock_dt.now.return_value = monday_9am
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_repo.connection.return_value.__enter__.return_value = con

            with pytest.raises(asyncio.CancelledError):
                asyncio.run(rate_prefetch_task())

            mock_get_rate.assert_called_once_with(
                con,
                monday_9am.date(),
                settings.app_currency,
                settings.accounting_currency,
            )


@allure.epic("Currencies")
@allure.feature("Background tasks")
@allure.story("Weekend — fetches rate (walkback)")
class TestWeekendFetch:
    def test_fetches_on_saturday(self):
        saturday_10am = _belgrade_dt(10, weekday=5)
        con = MagicMock()

        with (
            patch(
                "dinary.background.rate_prefetch.task.datetime",
            ) as mock_dt,
            patch(
                "dinary.background.rate_prefetch.task.storage",
            ) as mock_repo,
            patch(
                "dinary.background.rate_prefetch.task.get_db_rate",
                side_effect=[None, _RATE],
            ),
            patch(
                "dinary.background.rate_prefetch.task.get_rate",
                return_value=_RATE,
            ) as mock_get_rate,
            patch(
                "dinary.background.rate_prefetch.task._seconds_until_prefetch_hour",
                return_value=80000,
            ),
            patch(
                "dinary.background.rate_prefetch.task.asyncio.sleep",
                new_callable=AsyncMock,
                side_effect=asyncio.CancelledError,
            ),
        ):
            mock_dt.now.return_value = saturday_10am
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_repo.connection.return_value.__enter__.return_value = con

            with pytest.raises(asyncio.CancelledError):
                asyncio.run(rate_prefetch_task())

            mock_get_rate.assert_called_once_with(
                con,
                saturday_10am.date(),
                settings.app_currency,
                settings.accounting_currency,
            )


@allure.epic("Currencies")
@allure.feature("Background tasks")
@allure.story("Already cached — sleeps until tomorrow")
class TestAlreadyCached:
    def test_sleeps_until_tomorrow(self):
        monday_9am = _belgrade_dt(9)
        con = MagicMock()

        with (
            patch(
                "dinary.background.rate_prefetch.task.datetime",
            ) as mock_dt,
            patch(
                "dinary.background.rate_prefetch.task.storage",
            ) as mock_repo,
            patch(
                "dinary.background.rate_prefetch.task.get_db_rate",
                return_value=_RATE,
            ),
            patch(
                "dinary.background.rate_prefetch.task.get_rate",
            ) as mock_get_rate,
            patch(
                "dinary.background.rate_prefetch.task._seconds_until_prefetch_hour",
                return_value=80000,
            ),
            patch(
                "dinary.background.rate_prefetch.task.asyncio.sleep",
                new_callable=AsyncMock,
                side_effect=asyncio.CancelledError,
            ) as mock_sleep,
        ):
            mock_dt.now.return_value = monday_9am
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_repo.connection.return_value.__enter__.return_value = con

            with pytest.raises(asyncio.CancelledError):
                asyncio.run(rate_prefetch_task())

            mock_get_rate.assert_not_called()
            mock_sleep.assert_awaited_with(80000)


@allure.epic("Currencies")
@allure.feature("Background tasks")
@allure.story("Fetch error — retries")
class TestFetchError:
    def test_retries_after_error(self):
        monday_9am = _belgrade_dt(9)
        con = MagicMock()

        with (
            patch(
                "dinary.background.rate_prefetch.task.datetime",
            ) as mock_dt,
            patch(
                "dinary.background.rate_prefetch.task.storage",
            ) as mock_repo,
            patch(
                "dinary.background.rate_prefetch.task.get_db_rate",
                return_value=None,
            ),
            patch(
                "dinary.background.rate_prefetch.task.get_rate",
                side_effect=ValueError("no rate"),
            ),
            patch(
                "dinary.background.rate_prefetch.task.asyncio.sleep",
                new_callable=AsyncMock,
                side_effect=asyncio.CancelledError,
            ) as mock_sleep,
        ):
            mock_dt.now.return_value = monday_9am
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_repo.connection.return_value.__enter__.return_value = con

            with pytest.raises(asyncio.CancelledError):
                asyncio.run(rate_prefetch_task())

            mock_sleep.assert_awaited_with(_RETRY_INTERVAL_SEC)


@allure.epic("Currencies")
@allure.feature("Background tasks")
@allure.story("Stale fallback — retries instead of sleeping until tomorrow")
class TestStaleFallback:
    """get_rate can return a rate without writing it to DB for today; the
    task must retry rather than sleep until tomorrow."""

    def test_retries_when_rate_not_written_to_db(self):
        monday_9am = _belgrade_dt(9)
        con = MagicMock()

        with (
            patch(
                "dinary.background.rate_prefetch.task.datetime",
            ) as mock_dt,
            patch(
                "dinary.background.rate_prefetch.task.storage",
            ) as mock_repo,
            patch(
                "dinary.background.rate_prefetch.task.get_db_rate",
                # first call: no existing rate; second call: still None (not written)
                return_value=None,
            ),
            patch(
                "dinary.background.rate_prefetch.task.get_rate",
                return_value=_RATE,
            ),
            patch(
                "dinary.background.rate_prefetch.task.asyncio.sleep",
                new_callable=AsyncMock,
                side_effect=asyncio.CancelledError,
            ) as mock_sleep,
        ):
            mock_dt.now.return_value = monday_9am
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_repo.connection.return_value.__enter__.return_value = con

            with pytest.raises(asyncio.CancelledError):
                asyncio.run(rate_prefetch_task())

            mock_sleep.assert_awaited_with(_RETRY_INTERVAL_SEC)

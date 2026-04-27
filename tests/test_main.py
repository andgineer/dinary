"""Tests for the lifespan periodic drain loop in dinary.main."""

import asyncio
from unittest.mock import Mock, patch

import allure
import pytest

from dinary.config import settings
from dinary.main import _lifespan, create_app
from dinary.services import ledger_repo, sheet_logging
from dinary import __version__


@pytest.fixture(autouse=True)
def _lifespan_stubs(monkeypatch):
    """Stub out the two lifespan side-effects that are irrelevant to drain-loop tests.

    ``init_db`` runs yoyo migrations on each test — ~300 ms of SQLite
    overhead that has nothing to do with the drain-loop contract.
    ``rate_prefetch_task`` opens a DB connection and may make network
    calls; it runs concurrently and adds noise to timing assertions.
    Both are no-ops here so the tests stay fast and hermetic.
    """
    monkeypatch.setattr(ledger_repo, "init_db", lambda: None)

    async def _noop_rate_prefetch():
        await asyncio.sleep(9999)

    monkeypatch.setattr(
        "dinary.main.rate_prefetch_task",
        _noop_rate_prefetch,
    )


def _run(coro):
    """Run an async function in a fresh event loop (no pytest-asyncio needed)."""
    return asyncio.run(coro)


@allure.epic("Build")
@allure.feature("Version")
def test_version():
    assert __version__


@allure.epic("Background Tasks")
@allure.feature("Sheet logging drain loop")
@allure.story("Drain runs on each tick when enabled")
def test_periodic_drain_runs(monkeypatch):
    """Drain runs immediately + on each tick when enabled."""
    monkeypatch.setattr(settings, "sheet_logging_drain_interval_sec", 0.05)
    app = create_app()
    mock_drain = Mock(
        return_value={
            "years": 1,
            "attempted": 2,
            "appended": 2,
            "already_logged": 0,
            "failed": 0,
            "recovered_with_duplicate": 0,
            "noop_orphan": 0,
            "poisoned": 0,
            "cap_reached": False,
        }
    )

    async def _go():
        with (
            patch(
                "dinary.background.sheet_logging_task.sheet_logging.is_sheet_logging_enabled",
                return_value=True,
            ),
            patch("dinary.background.sheet_logging_task.sheet_logging.drain_pending", mock_drain),
        ):
            async with _lifespan(app):
                await asyncio.sleep(0.2)

    _run(_go())
    assert mock_drain.call_count >= 2


@allure.epic("Background Tasks")
@allure.feature("Sheet logging drain loop")
@allure.story("No-op when interval=0")
def test_disabled_by_interval(monkeypatch):
    """No-op when interval=0."""
    monkeypatch.setattr(settings, "sheet_logging_drain_interval_sec", 0)
    app = create_app()
    mock_drain = Mock()

    async def _go():
        with (
            patch(
                "dinary.background.sheet_logging_task.sheet_logging.is_sheet_logging_enabled",
                return_value=True,
            ),
            patch("dinary.background.sheet_logging_task.sheet_logging.drain_pending", mock_drain),
        ):
            async with _lifespan(app):
                await asyncio.sleep(0.1)

    _run(_go())
    assert mock_drain.call_count == 0


@allure.epic("Background Tasks")
@allure.feature("Sheet logging drain loop")
@allure.story("No-op when sheet logging disabled")
def test_disabled_by_sheet_logging(monkeypatch):
    """No-op when sheet logging is not enabled."""
    monkeypatch.setattr(settings, "sheet_logging_drain_interval_sec", 0.05)
    app = create_app()
    mock_drain = Mock()

    async def _go():
        with (
            patch(
                "dinary.background.sheet_logging_task.sheet_logging.is_sheet_logging_enabled",
                return_value=False,
            ),
            patch("dinary.background.sheet_logging_task.sheet_logging.drain_pending", mock_drain),
        ):
            async with _lifespan(app):
                await asyncio.sleep(0.15)

    _run(_go())
    assert mock_drain.call_count == 0


@allure.epic("Background Tasks")
@allure.feature("Sheet logging drain loop")
@allure.story("Loop survives a failing sweep")
def test_failing_sweep_does_not_kill_loop(monkeypatch):
    """Loop survives a failing sweep."""
    monkeypatch.setattr(settings, "sheet_logging_drain_interval_sec", 0.05)
    app = create_app()
    call_count = 0

    def _side_effect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Sheets API exploded")
        return {
            "years": 0,
            "attempted": 0,
            "appended": 0,
            "already_logged": 0,
            "failed": 0,
            "recovered_with_duplicate": 0,
            "noop_orphan": 0,
            "poisoned": 0,
            "cap_reached": False,
        }

    async def _go():
        with (
            patch(
                "dinary.background.sheet_logging_task.sheet_logging.is_sheet_logging_enabled",
                return_value=True,
            ),
            patch(
                "dinary.background.sheet_logging_task.sheet_logging.drain_pending",
                side_effect=_side_effect,
            ),
        ):
            async with _lifespan(app):
                await asyncio.sleep(0.25)

    _run(_go())
    assert call_count >= 2


@allure.epic("Background Tasks")
@allure.feature("Sheet logging drain loop")
@allure.story("Clean cancellation on shutdown")
def test_cancel_on_shutdown_is_clean(monkeypatch):
    """Task ends cleanly after lifespan exit, with no leaked warnings."""
    monkeypatch.setattr(settings, "sheet_logging_drain_interval_sec", 0.05)
    app = create_app()
    mock_drain = Mock(
        return_value={
            "years": 0,
            "attempted": 0,
            "appended": 0,
            "already_logged": 0,
            "failed": 0,
            "recovered_with_duplicate": 0,
            "noop_orphan": 0,
            "poisoned": 0,
            "cap_reached": False,
        }
    )
    task_ref = None

    async def _go():
        nonlocal task_ref
        with (
            patch(
                "dinary.background.sheet_logging_task.sheet_logging.is_sheet_logging_enabled",
                return_value=True,
            ),
            patch("dinary.background.sheet_logging_task.sheet_logging.drain_pending", mock_drain),
        ):
            async with _lifespan(app):
                for t in asyncio.all_tasks():
                    if t.get_name() == "sheet-logging-task":
                        task_ref = t
                        break
                await asyncio.sleep(0.1)

    _run(_go())
    assert task_ref is not None
    assert task_ref.done()
    assert task_ref.cancelled()


@allure.epic("Background Tasks")
@allure.feature("Sheet logging drain loop")
@allure.story("notify_new_work wakes the loop immediately")
def test_notify_new_work_wakes_drain_immediately(monkeypatch):
    """`notify_new_work` kicks the drain loop without waiting for the timer.

    Uses a long interval (1s) so the timer cannot mask the wake-up: if
    the event channel is wired correctly, `drain_pending` is called
    immediately when notified, not after the timer fires.
    """
    monkeypatch.setattr(settings, "sheet_logging_drain_interval_sec", 1.0)
    app = create_app()
    call_count = 0
    done = asyncio.Event()

    def _side_effect():
        nonlocal call_count
        call_count += 1
        # Signal only on the second call (the post-notify sweep): the
        # first call is the immediate startup sweep the drain loop
        # always runs before its first timed wait.
        if call_count >= 2:  # noqa: PLR2004
            done.set()
        return {
            "attempted": 0,
            "appended": 0,
            "already_logged": 0,
            "failed": 0,
            "recovered_with_duplicate": 0,
            "noop_orphan": 0,
            "poisoned": 0,
            "cap_reached": False,
        }

    async def _go():
        with (
            patch(
                "dinary.background.sheet_logging_task.sheet_logging.is_sheet_logging_enabled",
                return_value=True,
            ),
            patch(
                "dinary.background.sheet_logging_task.sheet_logging.drain_pending",
                side_effect=_side_effect,
            ),
        ):
            async with _lifespan(app):
                # Give the startup sweep a moment to complete.
                for _ in range(20):
                    await asyncio.sleep(0.01)
                    if call_count >= 1:
                        break
                sheet_logging.notify_new_work()
                # Wait up to 0.5s — drain interval is 1s, so if the
                # wake signal doesn't work this assertion fails before
                # the timer can save us.
                await asyncio.wait_for(done.wait(), timeout=0.5)

    _run(_go())
    assert call_count >= 2  # noqa: PLR2004


@allure.epic("Background Tasks")
@allure.feature("Sheet logging drain loop")
@allure.story("notify_new_work no-op without registered channel")
def test_notify_new_work_without_registered_channel_is_noop():
    """Calling `notify_new_work` outside a lifespan must not raise."""
    sheet_logging.clear_wake_channel()
    sheet_logging.notify_new_work()

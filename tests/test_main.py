"""Tests for the lifespan periodic drain loop in dinary.main."""

import asyncio
from unittest.mock import Mock, patch

import pytest

from dinary.config import settings
from dinary.main import _lifespan, create_app
from dinary.services import duckdb_repo, sheet_logging


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    """Point lifespan tests at an ephemeral DuckDB file.

    These tests exercise ``dinary.main._lifespan()``, which always calls
    ``duckdb_repo.init_db()`` on entry. Without overriding ``DB_PATH`` /
    ``DATA_DIR``, the tests would migrate/open the developer's real
    ``data/dinary.duckdb`` and fight with any running local server for the
    file lock. Keep them hermetic by always using ``tmp_path``.
    """
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "DB_PATH", tmp_path / "dinary.duckdb")


def _run(coro):
    """Run an async function in a fresh event loop (no pytest-asyncio needed)."""
    return asyncio.run(coro)


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
            patch("dinary.main.sheet_logging.is_sheet_logging_enabled", return_value=True),
            patch("dinary.main.sheet_logging.drain_pending", mock_drain),
        ):
            async with _lifespan(app):
                await asyncio.sleep(0.2)

    _run(_go())
    assert mock_drain.call_count >= 2


def test_disabled_by_interval(monkeypatch):
    """No-op when interval=0."""
    monkeypatch.setattr(settings, "sheet_logging_drain_interval_sec", 0)
    app = create_app()
    mock_drain = Mock()

    async def _go():
        with (
            patch("dinary.main.sheet_logging.is_sheet_logging_enabled", return_value=True),
            patch("dinary.main.sheet_logging.drain_pending", mock_drain),
        ):
            async with _lifespan(app):
                await asyncio.sleep(0.1)

    _run(_go())
    assert mock_drain.call_count == 0


def test_disabled_by_sheet_logging(monkeypatch):
    """No-op when sheet logging is not enabled."""
    monkeypatch.setattr(settings, "sheet_logging_drain_interval_sec", 0.05)
    app = create_app()
    mock_drain = Mock()

    async def _go():
        with (
            patch("dinary.main.sheet_logging.is_sheet_logging_enabled", return_value=False),
            patch("dinary.main.sheet_logging.drain_pending", mock_drain),
        ):
            async with _lifespan(app):
                await asyncio.sleep(0.15)

    _run(_go())
    assert mock_drain.call_count == 0


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
            patch("dinary.main.sheet_logging.is_sheet_logging_enabled", return_value=True),
            patch("dinary.main.sheet_logging.drain_pending", side_effect=_side_effect),
        ):
            async with _lifespan(app):
                await asyncio.sleep(0.25)

    _run(_go())
    assert call_count >= 2


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
            patch("dinary.main.sheet_logging.is_sheet_logging_enabled", return_value=True),
            patch("dinary.main.sheet_logging.drain_pending", mock_drain),
        ):
            async with _lifespan(app):
                for t in asyncio.all_tasks():
                    if t.get_name() == "sheet-logging-drain":
                        task_ref = t
                        break
                await asyncio.sleep(0.1)

    _run(_go())
    assert task_ref is not None
    assert task_ref.done()
    assert task_ref.cancelled()


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
            patch("dinary.main.sheet_logging.is_sheet_logging_enabled", return_value=True),
            patch("dinary.main.sheet_logging.drain_pending", side_effect=_side_effect),
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


def test_notify_new_work_without_registered_channel_is_noop():
    """Calling `notify_new_work` outside a lifespan must not raise."""
    sheet_logging.clear_wake_channel()
    sheet_logging.notify_new_work()

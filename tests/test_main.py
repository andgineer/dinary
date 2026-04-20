"""Tests for the lifespan periodic drain loop in dinary.main."""

import asyncio
from unittest.mock import Mock, patch

from dinary.config import settings
from dinary.main import _lifespan, create_app


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
            "skipped_expired": 0,
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
            "skipped_expired": 0,
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
            "skipped_expired": 0,
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

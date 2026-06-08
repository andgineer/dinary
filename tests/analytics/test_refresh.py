"""Tests for the background ledger-replica refresh daemon.

The module-level state (``_db_path``, ``_last_refresh``, ``_last_refresh_error``,
``_daemon_thread``, ``_wake_event``) is a singleton shared with real background
threads, so the autouse fixture below resets all of it before and after every test.
Tests that spin up a real ``_refresh_loop`` patch ``refresh_replica`` with a
``side_effect`` sequence ending in ``_StopLoop`` — an exception ``_refresh_loop``
does not catch — so the loop thread always exits cleanly and cannot pollute
whatever test runs next; each such test ``join()``s the thread before asserting.
"""

import json
import logging
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock

import allure
import pytest

import dinary_analytics.refresh as refresh_module
from dinary_analytics.paths import _app_data_dir
from dinary_analytics.refresh import (
    RefreshError,
    get_app_url,
    get_db_path,
    get_last_refresh,
    get_last_refresh_error,
    refresh_replica,
    set_app_url,
    start_refresh_daemon,
    trigger_refresh_now,
)

_VALID_HEADER = b"SQLite format 3\x00"


class _StopLoop(Exception):
    """Sentinel that ``_refresh_loop`` does not catch — used to terminate test threads."""


class _FakeResponse:
    """Minimal stand-in for the object ``urllib.request.urlopen`` returns as a context manager."""

    def __init__(self, status: int, body: bytes, *, chunk_size: int | None = None):
        self.status = status
        self._body = body
        self._pos = 0
        self._chunk_size = chunk_size

    def read(self, n: int = -1) -> bytes:
        size = self._chunk_size if self._chunk_size is not None else n
        chunk = self._body[self._pos :] if size < 0 else self._body[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


@pytest.fixture(autouse=True)
def _reset_refresh_state(monkeypatch):
    def _reset():
        monkeypatch.setattr(refresh_module, "_db_path", None)
        monkeypatch.setattr(refresh_module, "_last_refresh", None)
        monkeypatch.setattr(refresh_module, "_last_refresh_error", None)
        monkeypatch.setattr(refresh_module, "_daemon_thread", None)
        refresh_module._wake_event.clear()

    _reset()
    yield
    _reset()


# --- _app_data_dir ----------------------------------------------------------


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_app_data_dir_darwin(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    path = _app_data_dir()
    assert path == Path.home() / "Library" / "Application Support" / "dinary"


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_app_data_dir_win32(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert _app_data_dir() == tmp_path / "dinary"


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_app_data_dir_win32_raises_without_localappdata(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(RuntimeError, match="LOCALAPPDATA not set"):
        _app_data_dir()


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_app_data_dir_linux_fallback(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert _app_data_dir() == Path.home() / ".local" / "share" / "dinary"


# --- refresh_replica ---------------------------------------------------------


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_refresh_replica_returns_path(tmp_path, monkeypatch):
    db_path = tmp_path / "replica.db"
    monkeypatch.setattr(refresh_module, "REPLICA_PATH", db_path)
    monkeypatch.setattr(refresh_module, "get_app_url", lambda: "https://dinary-host.ts.net")
    body = _VALID_HEADER + b"\x00" * 64

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _FakeResponse(200, body))

    result = refresh_replica()
    assert result == db_path
    assert db_path.read_bytes() == body


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_refresh_replica_raises_on_failure(tmp_path, monkeypatch):
    db_path = tmp_path / "replica.db"
    monkeypatch.setattr(refresh_module, "REPLICA_PATH", db_path)
    monkeypatch.setattr(refresh_module, "get_app_url", lambda: "https://dinary-host.ts.net")

    def raising_urlopen(*_a, **_k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", raising_urlopen)
    with pytest.raises(RefreshError):
        refresh_replica()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _FakeResponse(503, b""))
    with pytest.raises(RefreshError):
        refresh_replica()


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_refresh_replica_raises_on_invalid_snapshot(tmp_path, monkeypatch):
    db_path = tmp_path / "replica.db"
    db_path.write_bytes(b"previous-complete-replica")
    monkeypatch.setattr(refresh_module, "REPLICA_PATH", db_path)
    monkeypatch.setattr(refresh_module, "get_app_url", lambda: "https://dinary-host.ts.net")

    bad_body = b"<html>captive portal</html>"
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _FakeResponse(200, bad_body))

    with pytest.raises(RefreshError, match="not a valid SQLite database"):
        refresh_replica()

    assert db_path.read_bytes() == b"previous-complete-replica"
    assert not db_path.with_suffix(".tmp").exists()


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_refresh_replica_writes_atomically(tmp_path, monkeypatch):
    db_path = tmp_path / "replica.db"
    old_content = _VALID_HEADER + b"old" * 200
    new_content = _VALID_HEADER + b"new" * 200
    db_path.write_bytes(old_content)
    monkeypatch.setattr(refresh_module, "REPLICA_PATH", db_path)
    monkeypatch.setattr(refresh_module, "get_app_url", lambda: "https://dinary-host.ts.net")
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_a, **_k: _FakeResponse(200, new_content, chunk_size=16),
    )

    observed: list[bytes] = []
    stop = threading.Event()

    def poll():
        while not stop.is_set():
            if db_path.exists():
                observed.append(db_path.read_bytes())

    poller = threading.Thread(target=poll, daemon=True)
    poller.start()
    try:
        refresh_replica()
    finally:
        stop.set()
        poller.join(timeout=5)

    assert db_path.read_bytes() == new_content
    assert all(content in (old_content, new_content) for content in observed)


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_refresh_replica_raises_when_unconfigured(monkeypatch):
    monkeypatch.setattr(refresh_module, "get_app_url", lambda: None)
    with pytest.raises(
        RefreshError,
        match=r"no server address is configured",
    ):
        refresh_replica()


# --- get_app_url / set_app_url -----------------------------------------------


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_get_app_url_returns_none_when_missing(tmp_path, monkeypatch):
    config_path = tmp_path / "dinary-ai-config.json"
    monkeypatch.setattr(refresh_module, "LOCAL_CONFIG_PATH", config_path)

    assert get_app_url() is None

    config_path.write_text(json.dumps({}))
    assert get_app_url() is None

    config_path.write_text(json.dumps({"app_url": ""}))
    assert get_app_url() is None


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_set_app_url_then_get_app_url_round_trips(tmp_path, monkeypatch):
    config_path = tmp_path / "sub" / "dinary-ai-config.json"
    monkeypatch.setattr(refresh_module, "LOCAL_CONFIG_PATH", config_path)

    set_app_url("https://dinary-host.tailxxxx.ts.net")

    assert config_path.parent.exists()
    assert get_app_url() == "https://dinary-host.tailxxxx.ts.net"


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_set_app_url_strips_trailing_slash(tmp_path, monkeypatch):
    config_path = tmp_path / "dinary-ai-config.json"
    monkeypatch.setattr(refresh_module, "LOCAL_CONFIG_PATH", config_path)

    set_app_url("https://dinary-host.tailxxxx.ts.net/")

    assert get_app_url() == "https://dinary-host.tailxxxx.ts.net"


# --- _refresh_loop / trigger_refresh_now / start_refresh_daemon -------------


def _run_loop_catching_stop() -> None:
    """Run ``_refresh_loop``, swallowing ``_StopLoop`` so it doesn't escape the thread.

    An exception escaping a thread's target makes pytest emit a
    ``PytestUnhandledThreadExceptionWarning`` even when the test deliberately
    uses that exception to terminate the loop, so it is caught here instead.
    """
    try:
        refresh_module._refresh_loop()
    except _StopLoop:
        pass


def _run_loop_to_completion(monkeypatch, side_effect) -> None:
    """Run a real ``_refresh_loop`` to completion in a background thread, then join it.

    ``side_effect`` must end in ``_StopLoop`` — the one exception ``_refresh_loop``
    does not catch — so the loop terminates deterministically.
    """
    monkeypatch.setattr(refresh_module, "refresh_replica", MagicMock(side_effect=side_effect))
    thread = threading.Thread(target=_run_loop_catching_stop, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive(), "refresh loop thread did not terminate"


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_refresh_loop_sets_db_path(tmp_path, monkeypatch):
    monkeypatch.setattr(refresh_module, "REFRESH_INTERVAL_SECONDS", 0.05)
    target = tmp_path / "replica.db"

    _run_loop_to_completion(monkeypatch, [target, _StopLoop()])

    assert get_db_path() == target


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_refresh_loop_logs_on_error(monkeypatch, caplog):
    monkeypatch.setattr(refresh_module, "REFRESH_RETRY_BASE_SECONDS", 0.01)
    monkeypatch.setattr(refresh_module, "REFRESH_INTERVAL_SECONDS", 0.05)

    with caplog.at_level(logging.WARNING, logger=refresh_module.logger.name):
        _run_loop_to_completion(monkeypatch, [RefreshError("boom"), _StopLoop()])

    assert get_db_path() is None
    assert any("boom" in record.message for record in caplog.records)


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_refresh_loop_keeps_stale_path_on_error(tmp_path, monkeypatch):
    monkeypatch.setattr(refresh_module, "REFRESH_RETRY_BASE_SECONDS", 0.01)
    monkeypatch.setattr(refresh_module, "REFRESH_INTERVAL_SECONDS", 0.02)
    target = tmp_path / "replica.db"

    _run_loop_to_completion(monkeypatch, [target, RefreshError("transient"), _StopLoop()])

    assert get_db_path() == target
    assert get_last_refresh_error() == "transient"


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_refresh_loop_retries_after_failure(monkeypatch):
    monkeypatch.setattr(refresh_module, "REFRESH_RETRY_BASE_SECONDS", 0.01)
    monkeypatch.setattr(refresh_module, "REFRESH_INTERVAL_SECONDS", 0.02)
    fake_refresh = MagicMock(
        side_effect=[RefreshError("first"), RefreshError("second"), _StopLoop()],
    )
    monkeypatch.setattr(refresh_module, "refresh_replica", fake_refresh)

    thread = threading.Thread(target=_run_loop_catching_stop, daemon=True)
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert fake_refresh.call_count == 3


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_refresh_loop_backoff_doubles_on_repeated_errors(monkeypatch):
    monkeypatch.setattr(refresh_module, "REFRESH_RETRY_BASE_SECONDS", 30)
    monkeypatch.setattr(refresh_module, "REFRESH_INTERVAL_SECONDS", 86400)
    waits: list[float | None] = []
    real_wait = refresh_module._wake_event.wait

    def spy_wait(timeout=None):
        waits.append(timeout)
        return real_wait(timeout=0)

    monkeypatch.setattr(refresh_module._wake_event, "wait", spy_wait)

    _run_loop_to_completion(
        monkeypatch,
        [RefreshError("e1"), RefreshError("e2"), RefreshError("e3"), _StopLoop()],
    )

    assert waits[:3] == [30, 60, 120]


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_refresh_loop_resets_delay_after_success(tmp_path, monkeypatch):
    monkeypatch.setattr(refresh_module, "REFRESH_RETRY_BASE_SECONDS", 30)
    monkeypatch.setattr(refresh_module, "REFRESH_INTERVAL_SECONDS", 86400)
    target = tmp_path / "replica.db"
    waits: list[float | None] = []
    real_wait = refresh_module._wake_event.wait

    def spy_wait(timeout=None):
        waits.append(timeout)
        return real_wait(timeout=0)

    monkeypatch.setattr(refresh_module._wake_event, "wait", spy_wait)

    _run_loop_to_completion(
        monkeypatch,
        [RefreshError("e1"), RefreshError("e2"), target, _StopLoop()],
    )

    assert waits[:3] == [30, 60, 86400]


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_trigger_refresh_now_wakes_loop_immediately(monkeypatch):
    monkeypatch.setattr(refresh_module, "REFRESH_RETRY_BASE_SECONDS", 9999)
    monkeypatch.setattr(refresh_module, "REFRESH_INTERVAL_SECONDS", 9999)
    started_waiting = threading.Event()
    real_wait = refresh_module._wake_event.wait

    def spy_wait(timeout=None):
        started_waiting.set()
        return real_wait(timeout=timeout)

    monkeypatch.setattr(refresh_module._wake_event, "wait", spy_wait)
    fake_refresh = MagicMock(side_effect=[RefreshError("boom"), _StopLoop()])
    monkeypatch.setattr(refresh_module, "refresh_replica", fake_refresh)

    thread = threading.Thread(target=_run_loop_catching_stop, daemon=True)
    thread.start()
    try:
        assert started_waiting.wait(timeout=5)
        trigger_refresh_now()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert fake_refresh.call_count == 2
    finally:
        thread.join(timeout=5)


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_start_refresh_daemon_spawns_daemon_thread(monkeypatch):
    started = threading.Event()
    monkeypatch.setattr(refresh_module, "_refresh_loop", started.set)

    start_refresh_daemon()

    thread = refresh_module._daemon_thread
    assert isinstance(thread, threading.Thread)
    assert thread.daemon is True
    assert started.wait(timeout=5)
    thread.join(timeout=5)
    assert not thread.is_alive()


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_get_last_refresh_returns_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(refresh_module, "REFRESH_INTERVAL_SECONDS", 0.05)
    target = tmp_path / "replica.db"
    before = time.time()

    _run_loop_to_completion(monkeypatch, [target, _StopLoop()])

    after = time.time()
    timestamp = get_last_refresh()
    assert timestamp is not None
    assert before <= timestamp <= after


@allure.epic("Analytics")
@allure.feature("Refresh Daemon")
def test_get_last_refresh_error_returns_message(tmp_path, monkeypatch):
    monkeypatch.setattr(refresh_module, "REFRESH_RETRY_BASE_SECONDS", 0.01)
    monkeypatch.setattr(refresh_module, "REFRESH_INTERVAL_SECONDS", 0.02)
    target = tmp_path / "replica.db"
    calls: list[int] = []
    captured: list[str | None] = []

    def fake_refresh():
        calls.append(1)
        if len(calls) == 1:
            raise RefreshError("first failure")
        if len(calls) == 2:
            captured.append(get_last_refresh_error())
            return target
        raise _StopLoop

    thread = threading.Thread(target=_run_loop_catching_stop, daemon=True)
    monkeypatch.setattr(refresh_module, "refresh_replica", fake_refresh)
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert captured == ["first failure"]
    assert get_last_refresh_error() is None

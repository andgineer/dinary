"""Tests for ``inv analytics``'s dinary-ai readiness check."""

import time
import urllib.error
import urllib.request
from unittest.mock import MagicMock

import allure
import pytest

from dinary_analytics.paths import MCP_PORT
from tasks.analytics import _ensure_dinary_ai


class _FakeContext:
    """Minimal stand-in for an Invoke ``Context`` that records commands."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(self, cmd, **_kwargs):
        self.commands.append(cmd)
        return MagicMock(ok=True, failed=False, stdout="")


@allure.epic("Analytics")
@allure.feature("Analytics dashboard")
def test_ensure_dinary_ai_already_running(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: MagicMock())
    ctx = _FakeContext()

    _ensure_dinary_ai(ctx)

    assert ctx.commands == []


@allure.epic("Analytics")
@allure.feature("Analytics dashboard")
def test_ensure_dinary_ai_runs_setup_then_succeeds(monkeypatch):
    attempts = {"n": 0}

    def fake_urlopen(*_a, **_k):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise urllib.error.URLError("connection refused")
        return MagicMock()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    ctx = _FakeContext()

    _ensure_dinary_ai(ctx)

    assert ctx.commands == ["uv run inv setup-dinary-ai"]


@allure.epic("Analytics")
@allure.feature("Analytics dashboard")
def test_ensure_dinary_ai_gives_up_after_timeout(monkeypatch):
    def raising_urlopen(*_a, **_k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", raising_urlopen)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    ctx = _FakeContext()

    with pytest.raises(SystemExit, match=f"dinary-ai did not start on port {MCP_PORT}"):
        _ensure_dinary_ai(ctx)

    assert ctx.commands == ["uv run inv setup-dinary-ai"]

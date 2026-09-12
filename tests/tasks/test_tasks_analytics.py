"""Tests for ``inv analytics``: dinary-ai readiness check and LLM key export."""

import importlib
import time
import urllib.error
import urllib.request
from unittest.mock import MagicMock

import allure
import pytest

from dinary_analytics.llm import key_refs
from dinary_analytics.paths import MCP_PORT
from tasks.analytics import _ensure_dinary_ai, _llm_api_keys

# ``tasks.analytics`` the attribute is the Invoke Task named "analytics", so the
# module itself has to come from the import system for monkeypatching.
analytics_module = importlib.import_module("tasks.analytics")


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


def _write_env(tmp_path, monkeypatch, env_text: str):
    env_path = tmp_path / ".env"
    env_path.write_text(env_text)
    monkeypatch.setattr(analytics_module, "LOCAL_ENV_PATH", str(env_path))


@allure.epic("Analytics")
@allure.feature("Analytics dashboard")
def test_llm_api_keys_exports_every_ref_under_its_own_name(tmp_path, monkeypatch):
    refs = key_refs()
    _write_env(tmp_path, monkeypatch, "".join(f"{ref}=v-{i}\n" for i, ref in enumerate(refs)))

    assert _llm_api_keys() == {ref: f"v-{i}" for i, ref in enumerate(refs)}


@allure.epic("Analytics")
@allure.feature("Analytics dashboard")
def test_llm_api_keys_skips_missing_refs_with_warning(tmp_path, monkeypatch, capsys):
    first, *rest = key_refs()
    _write_env(tmp_path, monkeypatch, f"{first}=v-0\n")

    assert _llm_api_keys() == {first: "v-0"}
    out = capsys.readouterr().out
    assert all(ref in out for ref in rest)


@allure.epic("Analytics")
@allure.feature("Analytics dashboard")
def test_llm_api_keys_reads_the_env_file_not_the_process(tmp_path, monkeypatch):
    """The dashboard runs on another machine's env as often as this one's, so the
    value has to come from the deploy file rather than whatever is exported here."""
    ref = key_refs()[0]
    monkeypatch.setenv(ref, "from-process")
    _write_env(tmp_path, monkeypatch, f"{ref}=from-file\n")

    assert _llm_api_keys()[ref] == "from-file"

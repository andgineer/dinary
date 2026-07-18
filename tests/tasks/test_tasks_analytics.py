"""Tests for ``inv analytics``: dinary-ai readiness check and LLM key export."""

import importlib
import time
import urllib.error
import urllib.request
from unittest.mock import MagicMock

import allure
import pytest

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


_LLMS_TOML = """
[[llms]]
name        = "groq-llama-3.3-70b"
base_url    = "https://api.groq.com/openai/v1"
model       = "llama-3.3-70b-versatile"
api_key_ref = "GROQ_API_KEY"

[[llms]]
name        = "google-gemini-2.5-flash"
base_url    = "https://generativelanguage.googleapis.com/v1beta/openai"
model       = "gemini-2.5-flash"
api_key_ref = "GEMINI_API_KEY"
"""


def _write_llm_files(tmp_path, monkeypatch, env_text: str):
    toml_path = tmp_path / "llms.toml"
    toml_path.write_text(_LLMS_TOML)
    env_path = tmp_path / ".env"
    env_path.write_text(env_text)
    monkeypatch.setattr(analytics_module, "_LLM_TOML", toml_path)
    monkeypatch.setattr(analytics_module, "LOCAL_ENV_PATH", str(env_path))


@allure.epic("Analytics")
@allure.feature("Analytics dashboard")
def test_llm_api_keys_exports_every_ref_under_its_own_name(tmp_path, monkeypatch):
    _write_llm_files(tmp_path, monkeypatch, "GROQ_API_KEY=gr-1\nGEMINI_API_KEY=gm-2\n")

    assert _llm_api_keys() == {"GROQ_API_KEY": "gr-1", "GEMINI_API_KEY": "gm-2"}


@allure.epic("Analytics")
@allure.feature("Analytics dashboard")
def test_llm_api_keys_skips_missing_refs_with_warning(tmp_path, monkeypatch, capsys):
    _write_llm_files(tmp_path, monkeypatch, "GROQ_API_KEY=gr-1\n")

    assert _llm_api_keys() == {"GROQ_API_KEY": "gr-1"}
    assert "google-gemini-2.5-flash (GEMINI_API_KEY)" in capsys.readouterr().out


@allure.epic("Analytics")
@allure.feature("Analytics dashboard")
def test_llm_api_keys_exits_when_providers_file_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(analytics_module, "_LLM_TOML", tmp_path / "absent.toml")

    with pytest.raises(SystemExit, match="cannot resolve LLM keys"):
        _llm_api_keys()

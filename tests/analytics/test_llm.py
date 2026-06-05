"""Tests for the analytics LLM chat turn (dinary_analytics.llm)."""

import allure

import dinary_analytics.llm as llm_module
from dinary.adapters.llm_chat import AllProvidersBusyError, AllProvidersFailedError
from dinary_analytics.llm import (
    _tool_schema,
    load_providers,
    providers_available,
    run_chat_turn,
    tool_name,
)

_TOML = """
[[providers]]
label = "Groq"
base_url = "https://api.groq.com/openai/v1"
api_key = "k1"
model = "llama-3.3-70b-versatile"
rate_limit_sec = 60

[[providers]]
label = "OpenRouter"
base_url = "https://openrouter.ai/api/v1"
api_key = "k2"
model = "openai/gpt-oss-120b:free"
rate_limit_sec = 30
"""


def _write_providers(tmp_path, monkeypatch, body=_TOML):
    path = tmp_path / "llm_providers.toml"
    path.write_text(body)
    monkeypatch.setenv("DINARY_LLM_PROVIDERS_FILE", str(path))
    return path


@allure.epic("Analytics")
@allure.feature("Chat")
def test_load_providers_reads_toml_in_order(tmp_path, monkeypatch):
    _write_providers(tmp_path, monkeypatch)
    providers = load_providers()
    assert [p.label for p in providers] == ["Groq", "OpenRouter"]
    assert providers[0].model == "llama-3.3-70b-versatile"
    assert providers[1].rate_limit_sec == 30


@allure.epic("Analytics")
@allure.feature("Chat")
def test_providers_available_false_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("DINARY_LLM_PROVIDERS_FILE", str(tmp_path / "nope.toml"))
    assert providers_available() is False


@allure.epic("Analytics")
@allure.feature("Chat")
def test_tool_name_cleans_python_names():
    def _query_ledger_fn(sql: str) -> str:
        return ""

    def _propose_view(baskets: list[dict], default_basket: str) -> str:
        return ""

    assert tool_name(_query_ledger_fn) == "query_ledger"
    assert tool_name(_propose_view) == "propose_view"


@allure.epic("Analytics")
@allure.feature("Chat")
def test_tool_schema_types_and_required():
    def _propose_view(baskets: list[dict], default_basket: str, chart_type: str = "bar") -> str:
        """Propose a view."""
        return ""

    schema = _tool_schema(_propose_view)
    fn = schema["function"]
    assert fn["name"] == "propose_view"
    assert fn["description"] == "Propose a view."
    props = fn["parameters"]["properties"]
    assert props["baskets"]["type"] == "array"
    assert props["default_basket"]["type"] == "string"
    # chart_type has a default -> not required
    assert set(fn["parameters"]["required"]) == {"baskets", "default_basket"}


@allure.epic("Analytics")
@allure.feature("Chat")
def test_run_chat_turn_no_providers(tmp_path, monkeypatch):
    monkeypatch.setenv("DINARY_LLM_PROVIDERS_FILE", str(tmp_path / "nope.toml"))
    reply = run_chat_turn("system", [], [], "hi")
    assert "No LLM providers" in reply


@allure.epic("Analytics")
@allure.feature("Chat")
def test_run_chat_turn_returns_reply(tmp_path, monkeypatch):
    _write_providers(tmp_path, monkeypatch)
    captured = {}

    def _fake_complete(providers, messages, *, tools, dispatch):
        captured["messages"] = messages
        captured["providers"] = providers
        return "the answer"

    monkeypatch.setattr(llm_module, "complete_with_tools", _fake_complete)
    reply = run_chat_turn("system", [], [{"role": "model", "content": "earlier"}], "now")

    assert reply == "the answer"
    # system + prior (mapped to assistant) + new user
    assert captured["messages"][0] == {"role": "system", "content": "system"}
    assert captured["messages"][1] == {"role": "assistant", "content": "earlier"}
    assert captured["messages"][-1] == {"role": "user", "content": "now"}


@allure.epic("Analytics")
@allure.feature("Chat")
def test_run_chat_turn_rate_limited(tmp_path, monkeypatch):
    _write_providers(tmp_path, monkeypatch)

    def _raise(*_a, **_k):
        raise AllProvidersBusyError

    monkeypatch.setattr(llm_module, "complete_with_tools", _raise)
    reply = run_chat_turn("system", [], [], "now")
    assert "busy" in reply.lower()


@allure.epic("Analytics")
@allure.feature("Chat")
def test_run_chat_turn_all_failed(tmp_path, monkeypatch):
    _write_providers(tmp_path, monkeypatch)

    def _raise(*_a, **_k):
        raise AllProvidersFailedError

    monkeypatch.setattr(llm_module, "complete_with_tools", _raise)
    reply = run_chat_turn("system", [], [], "now")
    assert "unavailable" in reply.lower()


@allure.epic("Analytics")
@allure.feature("Chat")
def test_run_chat_turn_empty_reply_falls_back(tmp_path, monkeypatch):
    _write_providers(tmp_path, monkeypatch)
    monkeypatch.setattr(llm_module, "complete_with_tools", lambda *_a, **_k: "")
    reply = run_chat_turn("system", [], [], "now")
    assert "view updated" in reply

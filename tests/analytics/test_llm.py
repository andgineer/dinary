"""Tests for the analytics LLM chat turn (dinary_analytics.llm)."""

import allure
import llmbroker

import dinary_analytics.llm as llm_module
from dinary_analytics.llm import (
    _tool_schema,
    run_chat_turn,
    tool_name,
)

_TOML = """
[[llms]]
name = "groq"
base_url = "https://api.groq.com/openai/v1"
model = "llama-3.3-70b-versatile"
api_key_ref = "GROQ_API_KEY"

[[llms]]
name = "openrouter"
base_url = "https://openrouter.ai/api/v1"
model = "openai/gpt-oss-120b:free"
api_key_ref = "OPENROUTER_API_KEY"
"""


def _write_providers(tmp_path, monkeypatch, body=_TOML):
    path = tmp_path / "llm_providers.toml"
    path.write_text(body)
    monkeypatch.setenv("DINARY_LLM_PROVIDERS_FILE", str(path))
    monkeypatch.setenv("GROQ_API_KEY", "k1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k2")
    return path


@allure.epic("Analytics")
@allure.feature("Chat")
def test_tool_name_cleans_python_names():
    def _query_ledger_fn(sql: str) -> str:
        return ""

    def _propose_view(baskets: list[dict], default_basket: str) -> str:
        return ""

    assert tool_name(_query_ledger_fn) == "query_ledger"
    assert tool_name(_propose_view) == "propose_view"

    # Marimo prefixes cell-defined functions with `_cell_<id>_`; it must be stripped.
    _query_ledger_fn.__name__ = "_cell_nHfw_query_ledger_fn"
    _propose_view.__name__ = "_cell_nHfw_propose_view"
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

    def _fake_loop(llms, messages, *, tools, dispatch, operation):
        captured["messages"] = messages
        return "the answer"

    monkeypatch.setattr(llm_module.llmbroker, "run_tool_loop", _fake_loop)
    reply = run_chat_turn("system", [], [{"role": "model", "content": "earlier"}], "now")

    assert reply == "the answer"
    assert captured["messages"][0] == {"role": "system", "content": "system"}
    assert captured["messages"][1] == {"role": "assistant", "content": "earlier"}
    assert captured["messages"][-1] == {"role": "user", "content": "now"}


@allure.epic("Analytics")
@allure.feature("Chat")
def test_run_chat_turn_rate_limited(tmp_path, monkeypatch):
    _write_providers(tmp_path, monkeypatch)

    def _raise(*_a, **_k):
        raise llmbroker.NoLLMAvailableError

    monkeypatch.setattr(llm_module.llmbroker, "run_tool_loop", _raise)
    reply = run_chat_turn("system", [], [], "now")
    assert "busy" in reply.lower()


@allure.epic("Analytics")
@allure.feature("Chat")
def test_run_chat_turn_all_failed(tmp_path, monkeypatch):
    _write_providers(tmp_path, monkeypatch)

    def _raise(*_a, **_k):
        raise llmbroker.AllLLMsFailedError

    monkeypatch.setattr(llm_module.llmbroker, "run_tool_loop", _raise)
    reply = run_chat_turn("system", [], [], "now")
    assert "unavailable" in reply.lower()


@allure.epic("Analytics")
@allure.feature("Chat")
def test_run_chat_turn_empty_reply_falls_back(tmp_path, monkeypatch):
    _write_providers(tmp_path, monkeypatch)
    monkeypatch.setattr(llm_module.llmbroker, "run_tool_loop", lambda *_a, **_k: "")
    reply = run_chat_turn("system", [], [], "now")
    assert "view updated" in reply

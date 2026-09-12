"""Tests for the analytics LLM chat turn (dinary_analytics.llm)."""

import allure
import llmbroker
import pytest

import dinary_analytics.llm as llm_module
from dinary_analytics.llm import (
    _tool_schema,
    key_refs,
    run_chat_turn,
    tool_name,
)


class _Reply:
    def __init__(self, text: str) -> None:
        self.text = text


class _StubBroker:
    """A zero-config ``Broker`` fetches the curated model list over the network on
    first use and caches it in llmbroker's own directory — the operator's real one.
    These tests stub the tool loop, so the broker only has to be a context manager."""

    def __enter__(self) -> "_StubBroker":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


@pytest.fixture(autouse=True)
def _no_real_broker(monkeypatch):
    monkeypatch.setattr(llm_module.llmbroker, "Broker", lambda *_a, **_k: _StubBroker())


def _with_key(monkeypatch):
    """One pool provider keyed; the rest stay keyless, as on a real machine."""
    for ref in key_refs():
        monkeypatch.delenv(ref, raising=False)
    monkeypatch.setenv(key_refs()[0], "k1")


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
    for ref in key_refs():
        monkeypatch.delenv(ref, raising=False)
    monkeypatch.chdir(tmp_path)
    reply = run_chat_turn("system", [], [], "hi")
    assert "No LLM providers" in reply


@allure.epic("Analytics")
@allure.feature("Chat")
def test_providers_available_also_sees_a_dotenv_beside_the_cwd(tmp_path, monkeypatch):
    """The gate refuses the call outright, so missing a source the broker does read
    would disable a chat that works."""
    for ref in key_refs():
        monkeypatch.delenv(ref, raising=False)
    monkeypatch.chdir(tmp_path)
    assert llm_module.providers_available() is False

    (tmp_path / ".env").write_text(f"{key_refs()[0]}=from-file\n")
    assert llm_module.providers_available() is True


@allure.epic("Analytics")
@allure.feature("Chat")
def test_providers_available_ignores_a_blank_key(tmp_path, monkeypatch):
    """`llmbroker env freetier` writes bare `KEY=` lines; an unfilled one is not a key."""
    for ref in key_refs():
        monkeypatch.delenv(ref, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"{key_refs()[0]}=\n")
    assert llm_module.providers_available() is False

    # llmbroker strips before deciding, so whitespace is absent to it too.
    (tmp_path / ".env").write_text(f'{key_refs()[0]}="   "\n')
    assert llm_module.providers_available() is False


@allure.epic("Analytics")
@allure.feature("Chat")
def test_run_chat_turn_returns_reply(monkeypatch):
    _with_key(monkeypatch)
    captured = {}

    def _fake_loop(llms, messages, *, tools, dispatch, operation):
        captured["messages"] = messages
        return _Reply("the answer")

    monkeypatch.setattr(llm_module.llmbroker, "run_tool_loop", _fake_loop)
    reply = run_chat_turn("system", [], [{"role": "model", "content": "earlier"}], "now")

    assert reply == "the answer"
    assert captured["messages"][0] == {"role": "system", "content": "system"}
    assert captured["messages"][1] == {"role": "assistant", "content": "earlier"}
    assert captured["messages"][-1] == {"role": "user", "content": "now"}


@allure.epic("Analytics")
@allure.feature("Chat")
def test_run_chat_turn_rate_limited(monkeypatch):
    _with_key(monkeypatch)

    def _raise(*_a, **_k):
        raise llmbroker.NoLLMAvailableError("no providers", reason="rate_limited")

    monkeypatch.setattr(llm_module.llmbroker, "run_tool_loop", _raise)
    reply = run_chat_turn("system", [], [], "now")
    assert "busy" in reply.lower()


@allure.epic("Analytics")
@allure.feature("Chat")
def test_run_chat_turn_all_failed(monkeypatch):
    _with_key(monkeypatch)

    def _raise(*_a, **_k):
        raise llmbroker.LLMRequestError("all providers failed")

    monkeypatch.setattr(llm_module.llmbroker, "run_tool_loop", _raise)
    reply = run_chat_turn("system", [], [], "now")
    assert "unavailable" in reply.lower()


@allure.epic("Analytics")
@allure.feature("Chat")
def test_run_chat_turn_empty_reply_falls_back(monkeypatch):
    _with_key(monkeypatch)
    monkeypatch.setattr(llm_module.llmbroker, "run_tool_loop", lambda *_a, **_k: _Reply(""))
    reply = run_chat_turn("system", [], [], "now")
    assert "view updated" in reply


@allure.epic("Analytics")
@allure.feature("Chat")
def test_run_chat_turn_tool_loop_exhausted(monkeypatch):
    _with_key(monkeypatch)

    def _raise(*_a, **_k):
        raise llmbroker.ToolLoopLimitError("8 steps")

    monkeypatch.setattr(llm_module.llmbroker, "run_tool_loop", _raise)
    reply = run_chat_turn("system", [], [], "now")
    assert "without answering" in reply


@allure.epic("Analytics")
@allure.feature("Chat")
def test_key_refs_come_from_the_curated_pool():
    """The launcher exports keys by these exact names, so they have to be the ones
    the broker will look for — read from the copy on this machine, never the network."""
    assert key_refs() == list(llmbroker.curated_pool().keys)
    assert key_refs()

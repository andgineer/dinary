"""Tests for the analytics LLM chat turn (dinary_analytics.llm)."""

import json

import allure
import httpx
import llmbroker
import pytest
from llmbroker.direct import DirectClient

import dinary_analytics.llm as llm_module
from dinary_analytics.llm import (
    CHAT_ALIAS,
    _tool_schema,
    key_ref,
    run_chat_turn,
    tool_name,
)


class _Reply:
    def __init__(self, text: str) -> None:
        self.text = text


class _StubClient:
    def __init__(self) -> None:
        self.closed = False

    def __enter__(self) -> "_StubClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.closed = True


class _StubBroker:
    """A zero-config ``Broker`` reads and caches the curated catalog in llmbroker's
    own directory — the operator's real one. These tests stub it, so all it has to do
    is hand out the direct client and record how it was used."""

    def __init__(self, client: object) -> None:
        self.client = client
        self.declared: list[str] = []
        self.requested: list[str] = []
        self.closed = False

    def direct(self, alias: str) -> object:
        self.requested.append(alias)
        return self.client

    def __enter__(self) -> "_StubBroker":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.closed = True


@pytest.fixture
def broker(monkeypatch):
    stub = _StubBroker(_StubClient())

    def _build(*, direct):
        stub.declared = list(direct)
        return stub

    monkeypatch.setattr(llm_module.llmbroker, "Broker", _build)
    return stub


def _with_key(monkeypatch):
    monkeypatch.setenv(key_ref(), "k1")


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
def test_run_chat_turn_without_a_key(tmp_path, monkeypatch, broker):
    monkeypatch.delenv(key_ref(), raising=False)
    monkeypatch.chdir(tmp_path)
    reply = run_chat_turn("system", [], [], "hi")
    assert "No key for the AI chat" in reply
    assert key_ref() in reply
    assert broker.requested == []


@allure.epic("Analytics")
@allure.feature("Chat")
def test_chat_key_available_also_sees_a_dotenv_beside_the_cwd(tmp_path, monkeypatch):
    """The gate refuses the call outright, so missing a source the broker does read
    would disable a chat that works."""
    monkeypatch.delenv(key_ref(), raising=False)
    monkeypatch.chdir(tmp_path)
    assert llm_module.chat_key_available() is False

    (tmp_path / ".env").write_text(f"{key_ref()}=from-file\n")
    assert llm_module.chat_key_available() is True


@allure.epic("Analytics")
@allure.feature("Chat")
def test_chat_key_available_ignores_a_blank_key(tmp_path, monkeypatch):
    """`llmbroker env` writes bare `KEY=` lines; an unfilled one is not a key."""
    monkeypatch.delenv(key_ref(), raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"{key_ref()}=\n")
    assert llm_module.chat_key_available() is False

    # llmbroker strips before deciding, so whitespace is absent to it too.
    (tmp_path / ".env").write_text(f'{key_ref()}="   "\n')
    assert llm_module.chat_key_available() is False


@allure.epic("Analytics")
@allure.feature("Chat")
def test_run_chat_turn_drives_the_direct_client(monkeypatch, broker):
    _with_key(monkeypatch)
    captured = {}

    def _fake_loop(llms, messages, *, tools, dispatch):
        captured["llms"] = llms
        captured["messages"] = messages
        return _Reply("the answer")

    monkeypatch.setattr(llm_module.llmbroker, "run_tool_loop", _fake_loop)
    reply = run_chat_turn("system", [], [{"role": "model", "content": "earlier"}], "now")

    assert reply == "the answer"
    assert broker.declared == [CHAT_ALIAS]
    assert broker.requested == [CHAT_ALIAS]
    assert captured["llms"] is broker.client
    assert captured["messages"][0] == {"role": "system", "content": "system"}
    assert captured["messages"][1] == {"role": "assistant", "content": "earlier"}
    assert captured["messages"][-1] == {"role": "user", "content": "now"}
    assert broker.client.closed
    assert broker.closed


@allure.epic("Analytics")
@allure.feature("Chat")
def test_run_chat_turn_round_trips_tool_calls(monkeypatch, broker):
    """The real tool loop over a real direct client: the tools reach the request, the
    model's tool call runs through dispatch, and its result goes back to the model."""
    _with_key(monkeypatch)
    bodies: list[dict] = []

    def _provider(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            call = {
                "id": "c1",
                "type": "function",
                "function": {"name": "add", "arguments": '{"a": 2, "b": 3}'},
            }
            message = {"role": "assistant", "content": None, "tool_calls": [call]}
        else:
            message = {"role": "assistant", "content": f"sum is {body['messages'][-1]['content']}"}
        return httpx.Response(
            200, json={"choices": [{"message": message, "finish_reason": "stop"}]}
        )

    broker.client = DirectClient(
        base_url="https://llm.test/v1",
        model="test-model",
        api_key="k1",
        client=httpx.Client(transport=httpx.MockTransport(_provider)),
    )

    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    reply = run_chat_turn("system", [add], [], "2+3?")

    assert reply == "sum is 5"
    assert len(bodies) == 2
    assert bodies[0]["tools"][0]["function"]["name"] == "add"
    assert bodies[1]["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": "5"}


@allure.epic("Analytics")
@allure.feature("Chat")
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (llmbroker.RateLimitError("429", status=429), "rate-limited"),
        (llmbroker.LLMTimeoutError("slow"), "did not answer in time"),
        (llmbroker.AuthError("401", status=401), "rejected the key"),
        (llmbroker.MissingKeyError("no key"), "No key for the AI chat"),
        (llmbroker.ProviderError("500 boom", status=500), "AI model unavailable:** 500 boom"),
        (llmbroker.ToolLoopLimitError("8 steps"), "without answering"),
        (ValueError("bad"), "AI error:** bad"),
    ],
)
def test_run_chat_turn_reports_errors_as_text(monkeypatch, broker, error, expected):
    _with_key(monkeypatch)

    def _raise(*_a, **_k):
        raise error

    monkeypatch.setattr(llm_module.llmbroker, "run_tool_loop", _raise)
    reply = run_chat_turn("system", [], [], "now")
    assert expected in reply
    assert broker.closed


@allure.epic("Analytics")
@allure.feature("Chat")
def test_run_chat_turn_empty_reply_falls_back(monkeypatch, broker):  # noqa: ARG001
    _with_key(monkeypatch)
    monkeypatch.setattr(llm_module.llmbroker, "run_tool_loop", lambda *_a, **_k: _Reply(""))
    reply = run_chat_turn("system", [], [], "now")
    assert "view updated" in reply


@allure.epic("Analytics")
@allure.feature("Chat")
def test_key_ref_comes_from_the_paid_catalog():
    """The launcher exports the key by this exact name, so it has to be the one the
    broker will look for — read from the copy on this machine, never the network."""
    row = next(row for row in llmbroker.curated_paid() if row.alias == CHAT_ALIAS)
    assert key_ref() == row.provider.api_key_ref
    assert key_ref()

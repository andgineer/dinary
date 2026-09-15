"""The analytics chat on the real llmbroker ``Broker``, only provider HTTP and curated-list
downloads answered locally — the unit tests in ``test_llm.py`` stub the broker out."""

import logging

import allure
import httpx
import llmbroker
import pytest

from dinary_analytics.llm import CHAT_ALIAS, key_ref, run_chat_turn

from _llmbroker_support import FakeProviders, completion, route_provider_http, serve_bundled_presets


@pytest.fixture
def providers(monkeypatch, tmp_path) -> FakeProviders:
    for ref in [*llmbroker.curated_pool().keys, key_ref()]:
        monkeypatch.delenv(ref, raising=False)
    monkeypatch.setenv(key_ref(), "fake-openai")
    monkeypatch.chdir(tmp_path)
    fake = FakeProviders()
    route_provider_http(monkeypatch, fake, blocking_clients=True)
    serve_bundled_presets(monkeypatch)
    return fake


def _add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


def _tool_then_answer(body: dict) -> httpx.Response:
    last = body["messages"][-1]
    if last["role"] == "tool":
        return completion(f"sum is {last['content']}")
    call = {
        "id": "c1",
        "type": "function",
        "function": {"name": "add", "arguments": '{"a": 2, "b": 3}'},
    }
    return completion(None, tool_calls=[call])


@allure.epic("Analytics")
@allure.feature("llmbroker contract")
def test_a_chat_turn_runs_its_tools_on_the_chat_model(providers, caplog):
    providers.reply = _tool_then_answer
    chat_model = next(row for row in llmbroker.curated_paid() if row.alias == CHAT_ALIAS)

    with caplog.at_level(logging.INFO, logger="llmbroker"):
        reply = run_chat_turn("Use add for arithmetic.", [_add], [], "2+3?")

    assert reply == "sum is 5"
    assert len(providers.bodies) == 2
    for body in providers.bodies:
        assert body["model"] == chat_model.model
        assert body["tools"][0]["function"]["name"] == "add"
        # OpenAI refuses function tools on chat completions for this model otherwise.
        assert body["reasoning_effort"] == "none"
    assert providers.bodies[1]["messages"][-1]["content"] == "5"
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []


@allure.epic("Analytics")
@allure.feature("llmbroker contract")
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(429, text="slow down"), "rate-limited"),
        (httpx.Response(401, text="bad key"), "rejected the key"),
        (httpx.Response(500, text="boom"), "AI model unavailable"),
    ],
)
def test_a_provider_failure_reaches_the_user_as_text(providers, response, expected):
    providers.reply = lambda _body: response

    assert expected in run_chat_turn("system", [_add], [], "2+3?")


@allure.epic("Analytics")
@allure.feature("llmbroker contract")
def test_a_missing_key_is_reported_before_any_call(providers, monkeypatch):
    monkeypatch.delenv(key_ref(), raising=False)

    reply = run_chat_turn("system", [_add], [], "2+3?")

    assert key_ref() in reply
    assert providers.bodies == []

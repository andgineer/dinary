"""Tests for the shared OpenAI-compatible chat primitives and sync client."""

from unittest.mock import MagicMock, patch

import allure
import httpx
import pytest

from dinary.adapters.llm_chat import (
    AllProvidersBusyError,
    AllProvidersFailedError,
    ProviderConfig,
    build_chat_request,
    complete_with_tools,
    is_rate_limit,
    message_from_response,
    retry_after_seconds,
    run_tool_step,
)


def _provider(label: str = "P1") -> ProviderConfig:
    return ProviderConfig(
        label=label,
        base_url="https://api.example.com/v1",
        api_key="key",
        model="model-x",
        rate_limit_sec=60,
        rate_limited_until=None,
    )


# --- pure helpers ----------------------------------------------------------


@allure.epic("Receipts")
@allure.feature("Pipeline")
@allure.story("LLM chat")
class TestPrimitives:
    def test_build_chat_request_without_tools(self):
        url, headers, body = build_chat_request(_provider(), [{"role": "user", "content": "hi"}])
        assert url == "https://api.example.com/v1/chat/completions"
        assert headers == {"Authorization": "Bearer key"}
        assert body == {"model": "model-x", "messages": [{"role": "user", "content": "hi"}]}
        assert "tools" not in body

    def test_build_chat_request_with_tools(self):
        _, _, body = build_chat_request(_provider(), [], tools=[{"type": "function"}])
        assert body["tools"] == [{"type": "function"}]
        assert body["tool_choice"] == "auto"

    def test_message_from_response(self):
        data = {"choices": [{"message": {"content": "yo"}}]}
        assert message_from_response(data) == {"content": "yo"}

    def test_is_rate_limit(self):
        assert is_rate_limit(429)
        assert is_rate_limit(503)
        assert not is_rate_limit(500)

    def test_retry_after_seconds_parses_header(self):
        assert retry_after_seconds({"Retry-After": "12"}, 60) == 12

    def test_retry_after_seconds_falls_back(self):
        assert retry_after_seconds({}, 45) == 45
        assert retry_after_seconds({"Retry-After": "nope"}, 45) == 45

    def test_run_tool_step_returns_content_when_no_tool_calls(self):
        content, tool_msgs = run_tool_step({"content": "done"}, {})
        assert content == "done"
        assert tool_msgs == []

    def test_run_tool_step_executes_tools(self):
        calls = []

        def my_tool(x):
            calls.append(x)
            return "result"

        message = {
            "tool_calls": [{"id": "c1", "function": {"name": "my_tool", "arguments": '{"x": 7}'}}],
        }
        content, tool_msgs = run_tool_step(message, {"my_tool": my_tool})
        assert content is None
        assert calls == [7]
        assert tool_msgs == [{"role": "tool", "tool_call_id": "c1", "content": "result"}]

    def test_run_tool_step_unknown_tool(self):
        message = {"tool_calls": [{"id": "c1", "function": {"name": "ghost", "arguments": "{}"}}]}
        content, tool_msgs = run_tool_step(message, {})
        assert content is None
        assert "Unknown tool ghost" in tool_msgs[0]["content"]

    def test_run_tool_step_null_arguments_for_no_arg_tool(self):
        # providers sometimes send "null" (or a bare value) as arguments
        calls = []

        def no_arg_tool():
            calls.append(True)
            return "summary"

        for arguments in ("null", "", "[]"):
            message = {
                "tool_calls": [
                    {"id": "c1", "function": {"name": "no_arg_tool", "arguments": arguments}},
                ],
            }
            content, tool_msgs = run_tool_step(message, {"no_arg_tool": no_arg_tool})
            assert content is None
            assert tool_msgs[0]["content"] == "summary"
        assert calls == [True, True, True]

    def test_run_tool_step_tool_exception_reported_back(self):
        def boom(**_kwargs):
            raise ValueError("bad arg")

        message = {
            "tool_calls": [{"id": "c1", "function": {"name": "boom", "arguments": '{"x": 1}'}}],
        }
        content, tool_msgs = run_tool_step(message, {"boom": boom})
        assert content is None
        assert "Tool boom failed: bad arg" in tool_msgs[0]["content"]


# --- sync client -----------------------------------------------------------


def _sync_ctx(client: MagicMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=client)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def _ok(content: str = "ok") -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


def _tool_call(name: str, arguments: str, call_id: str = "c1") -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": call_id, "function": {"name": name, "arguments": arguments}},
                    ],
                },
            },
        ],
    }
    return resp


def _raises_429() -> MagicMock:
    resp = MagicMock()
    err_resp = MagicMock()
    err_resp.status_code = 429
    resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("429", request=MagicMock(), response=err_resp),
    )
    return resp


@allure.epic("Receipts")
@allure.feature("Pipeline")
@allure.story("LLM chat")
class TestCompleteWithTools:
    def test_returns_content_without_tools(self):
        client = MagicMock()
        client.post = MagicMock(return_value=_ok("hi there"))
        with patch("dinary.adapters.llm_chat.httpx.Client", return_value=_sync_ctx(client)):
            out = complete_with_tools([_provider()], [{"role": "user", "content": "q"}])
        assert out == "hi there"

    def test_executes_tool_then_returns_final(self):
        calls: list = []

        def my_tool(x):
            calls.append(x)
            return "tool result"

        client = MagicMock()
        client.post = MagicMock(
            side_effect=[_tool_call("my_tool", '{"x": 5}'), _ok("final answer")]
        )
        with patch("dinary.adapters.llm_chat.httpx.Client", return_value=_sync_ctx(client)):
            out = complete_with_tools(
                [_provider()],
                [{"role": "user", "content": "q"}],
                tools=[{"type": "function", "function": {"name": "my_tool"}}],
                dispatch={"my_tool": my_tool},
            )
        assert out == "final answer"
        assert calls == [5]

    def test_failover_to_second_provider_on_429(self):
        client = MagicMock()
        client.post = MagicMock(side_effect=[_raises_429(), _ok("from second")])
        providers = [_provider("P1"), _provider("P2")]
        with patch("dinary.adapters.llm_chat.httpx.Client", return_value=_sync_ctx(client)):
            out = complete_with_tools(providers, [{"role": "user", "content": "q"}])
        assert out == "from second"

    def test_all_rate_limited_raises_busy(self):
        client = MagicMock()
        client.post = MagicMock(side_effect=[_raises_429(), _raises_429()])
        providers = [_provider("P1"), _provider("P2")]
        with (
            patch("dinary.adapters.llm_chat.httpx.Client", return_value=_sync_ctx(client)),
            pytest.raises(AllProvidersBusyError),
        ):
            complete_with_tools(providers, [{"role": "user", "content": "q"}])

    def test_no_providers_raises_failed(self):
        with pytest.raises(AllProvidersFailedError):
            complete_with_tools([], [{"role": "user", "content": "q"}])

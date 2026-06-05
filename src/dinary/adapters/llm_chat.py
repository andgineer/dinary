"""OpenAI-compatible chat primitives shared by the async broker and sync client.

Request building, response parsing, tool-call execution and retry parsing live
here once. ``LLMBroker`` (async, concurrent — see ``llmbroker.py``) and
``complete_with_tools`` (sync, blocking — below) both build on these helpers so
the two transports never drift apart.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

_CHAT_PATH = "/chat/completions"


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    label: str
    base_url: str
    api_key: str
    model: str
    rate_limit_sec: int
    rate_limited_until: datetime | None


class AllProvidersBusyError(Exception):
    """Every provider returned a rate-limit (429/503) for this request."""


class AllProvidersFailedError(Exception):
    """No provider could be reached (empty list or transient network errors)."""


def is_rate_limit(status_code: int) -> bool:
    return status_code in (429, 503)


def retry_after_seconds(headers: Mapping[str, str], default_sec: int) -> int:
    try:
        return int(headers.get("Retry-After", default_sec))
    except (ValueError, TypeError):
        return default_sec


def build_chat_request(
    provider: ProviderConfig,
    messages: list[dict],
    tools: list[dict] | None = None,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Return (url, headers, json_body) for an OpenAI-compatible chat completion."""
    body: dict[str, Any] = {"model": provider.model, "messages": messages}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    return (
        f"{provider.base_url}{_CHAT_PATH}",
        {"Authorization": f"Bearer {provider.api_key}"},
        body,
    )


def message_from_response(data: dict) -> dict:
    """Extract the assistant message object from a chat-completion response body."""
    return data["choices"][0]["message"]


def run_tool_step(
    message: dict,
    dispatch: Mapping[str, Callable[..., object]],
) -> tuple[str | None, list[dict]]:
    """Process one assistant message.

    Returns (final_content, tool_result_messages). When the model requested tool
    calls, final_content is None and the caller should append ``message`` plus the
    returned tool messages and loop again. Otherwise final_content is the answer.
    """
    tool_calls = message.get("tool_calls")
    if not tool_calls:
        return str(message.get("content") or ""), []
    results: list[dict] = []
    for call in tool_calls:
        name = call["function"]["name"]
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            # Some providers send "null" or a bare value for no-arg tools.
            args = {}
        fn = dispatch.get(name)
        if fn is None:
            output: object = f"Unknown tool {name}"
        else:
            try:
                output = fn(**args)
            except Exception as exc:  # noqa: BLE001 - report back to the model so it can retry
                output = f"Tool {name} failed: {exc}"
        results.append({"role": "tool", "tool_call_id": call.get("id"), "content": str(output)})
    return None, results


def complete_with_tools(
    providers: Sequence[ProviderConfig],
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    dispatch: Mapping[str, Callable[..., object]] | None = None,
    max_steps: int = 8,
    timeout: float = 60.0,
) -> str:
    """Run one chat completion synchronously, trying providers in priority order.

    A provider returning 429/503 is skipped for the next; tool calls (when
    ``tools``/``dispatch`` are given) are executed locally and fed back until the
    model returns a final answer. Raises AllProvidersBusyError if every provider
    was rate-limited, or AllProvidersFailedError if none could be reached.
    """
    if not providers:
        raise AllProvidersFailedError
    saw_rate_limit = False
    with httpx.Client(timeout=timeout) as client:
        for provider in providers:
            try:
                return _run_tool_loop(
                    client,
                    provider,
                    list(messages),
                    tools,
                    dispatch or {},
                    max_steps,
                )
            except httpx.HTTPStatusError as exc:
                if is_rate_limit(exc.response.status_code):
                    saw_rate_limit = True
                    continue
                raise
            except (httpx.TimeoutException, httpx.ConnectError, OSError):
                continue
    raise AllProvidersBusyError if saw_rate_limit else AllProvidersFailedError


def _run_tool_loop(
    client: httpx.Client,
    provider: ProviderConfig,
    messages: list[dict],
    tools: list[dict] | None,
    dispatch: Mapping[str, Callable[..., object]],
    max_steps: int,
) -> str:
    for _ in range(max_steps):
        url, headers, body = build_chat_request(provider, messages, tools)
        resp = client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        message = message_from_response(resp.json())
        content, tool_messages = run_tool_step(message, dispatch)
        if content is not None:
            return content
        messages.append(message)
        messages.extend(tool_messages)
    return ""

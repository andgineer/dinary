"""LLM chat turn for the analytics dashboard.

Uses the standalone ``llmbroker`` package for OpenAI-compatible completion with
provider failover. Providers come from ``.deploy/llms.toml`` — the same
static config the server seeds from — so analytics never calls the running
dinary server. Tool calling drives the draft view (propose_view, query_ledger, …).
"""

import inspect
import os
import re
import tomllib
import typing
from collections.abc import Callable, Sequence
from pathlib import Path

import llmbroker

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PROVIDERS_FILE = _REPO_ROOT / ".deploy" / "llms.toml"

_NO_PROVIDERS_MESSAGE = "**No LLM providers configured.** Add them to `.deploy/llms.toml`."

_JSON_TYPES: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _providers_path() -> Path:
    override = os.getenv("DINARY_LLM_PROVIDERS_FILE")
    return Path(override) if override else _DEFAULT_PROVIDERS_FILE


def providers_available() -> bool:
    """Return True if at least one LLM is configured in the providers file."""
    path = _providers_path()
    if not path.exists():
        return False
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return bool(data.get("llms"))


# Functions defined inside Marimo cells get a `_cell_<id>_` prefix on __name__.
_CELL_PREFIX = re.compile(r"^_cell_[A-Za-z0-9]+_")


def tool_name(fn: Callable[..., object]) -> str:
    """Clean a Python tool callable's name into a stable LLM-facing tool name.

    Strips Marimo's cell prefix, leading/trailing underscores and a trailing `_fn`,
    so the names match what the system prompt references (e.g. propose_view).
    """
    name = _CELL_PREFIX.sub("", fn.__name__).strip("_")
    return name[:-3] if name.endswith("_fn") else name


def _json_type(annotation: object) -> dict:
    origin = typing.get_origin(annotation)
    if origin in (list, set, tuple):
        return {"type": "array", "items": {}}
    if origin is dict or annotation is dict:
        return {"type": "object"}
    if isinstance(annotation, type) and annotation in _JSON_TYPES:
        return {"type": _JSON_TYPES[annotation]}
    return {"type": "string"}


def _tool_schema(fn: Callable[..., object]) -> dict:
    properties: dict[str, dict] = {}
    required: list[str] = []
    for name, param in inspect.signature(fn).parameters.items():
        properties[name] = _json_type(param.annotation)
        if param.default is inspect.Parameter.empty:
            required.append(name)
    return {
        "type": "function",
        "function": {
            "name": tool_name(fn),
            "description": inspect.getdoc(fn) or "",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def run_chat_turn(
    system_prompt: str,
    tools: Sequence[Callable[..., object]],
    history: Sequence[dict[str, str]],
    user_text: str,
) -> str:
    """Send history + user_text to an available provider and return the reply.

    history items are {"role": "user"|"model", "content": str}. Provider/network
    errors (including rate limits) are returned as user-facing text, not raised.
    """
    if not providers_available():
        return _NO_PROVIDERS_MESSAGE
    with llmbroker.Broker(registry=llmbroker.Registry(_providers_path())) as llms:
        schemas = [_tool_schema(fn) for fn in tools]
        dispatch = {tool_name(fn): fn for fn in tools}

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        messages.extend(
            {"role": "assistant" if m["role"] == "model" else "user", "content": m["content"]}
            for m in history
        )
        messages.append({"role": "user", "content": user_text})

        try:
            reply = llmbroker.run_tool_loop(
                llms,
                messages,
                tools=schemas,
                dispatch=dispatch,
                operation="analytics_chat",
            )
        except llmbroker.NoLLMAvailableError:
            return "**All providers are busy right now.** Press 🔁 Retry in a moment."
        except llmbroker.LLMRequestError:
            return "**AI providers unavailable.** Check `.deploy/llms.toml`."
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
            return f"**AI error:** {str(exc)[:300]}"

        return reply or "*(view updated — see the draft below)*"

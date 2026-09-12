"""LLM chat turn for the analytics dashboard.

Uses the standalone ``llmbroker`` package in its zero-config form: llmbroker keeps
the model list and journal in its own directory and resolves provider keys from
this process's environment, so analytics touches neither the server nor its
database.
Tool calling drives the draft view (propose_view, query_ledger, …).
"""

import contextlib
import inspect
import os
import re
import typing
from collections.abc import Callable, Sequence
from pathlib import Path

import llmbroker
from llmbroker.standalone.secrets import parse_env_file

_NO_PROVIDERS_MESSAGE = (
    "**No LLM providers configured.** Add a provider key to `.deploy/.env`"
    " — `llmbroker env freetier` prints the ones the pool uses."
)

_JSON_TYPES: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def key_refs() -> list[str]:
    """The env-var names the curated pool wants, in declaration order. Read from the
    copy of the list already on this machine, never the network."""
    return list(llmbroker.curated_pool().keys)


def _filled(value: str | None) -> bool:
    """``llmbroker env`` writes bare ``KEY=`` lines, and llmbroker itself counts a
    blank one as absent — so must this, or the chat calls with no key at all."""
    return bool(value and value.strip())


def providers_available() -> bool:
    """Return True if at least one pool provider's key is resolvable here.

    Both sources a zero-config ``Broker`` reads, in its order: the environment,
    then a ``.env`` beside the working directory. This gate does not merely warn —
    ``run_chat_turn`` refuses to call on a False — so missing the second source
    would disable a chat that would have worked.
    """
    refs = key_refs()
    if any(_filled(os.getenv(ref)) for ref in refs):
        return True
    values: dict[str, str] = {}
    with contextlib.suppress(OSError):
        values = parse_env_file(Path(".env").read_text(encoding="utf-8"))
    return any(_filled(values.get(ref)) for ref in refs)


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
    with llmbroker.Broker() as llms:
        schemas = [_tool_schema(fn) for fn in tools]
        dispatch = {tool_name(fn): fn for fn in tools}

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        messages.extend(
            {"role": "assistant" if m["role"] == "model" else "user", "content": m["content"]}
            for m in history
        )
        messages.append({"role": "user", "content": user_text})

        try:
            result = llmbroker.run_tool_loop(
                llms,
                messages,
                tools=schemas,
                dispatch=dispatch,
                operation="analytics_chat",
            )
        except llmbroker.ToolLoopLimitError:
            return "**The model kept calling tools without answering.** Rephrase and retry."
        except llmbroker.NoLLMAvailableError:
            return "**All providers are busy right now.** Press 🔁 Retry in a moment."
        except llmbroker.LLMRequestError:
            return "**AI providers unavailable.** Check the provider keys in `.deploy/.env`."
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
            return f"**AI error:** {str(exc)[:300]}"

        return result.text or "*(view updated — see the draft below)*"

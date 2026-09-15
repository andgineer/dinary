"""LLM chat turn for the analytics dashboard.

Calls one paid model directly through the standalone ``llmbroker`` package: the
model comes from llmbroker's curated paid catalog by alias and its key from this
process's environment, so analytics touches neither the server nor its database.
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

CHAT_ALIAS = "gpt-fast"

_NO_KEY = "**No key for the AI chat.** Add `{ref}` to `.deploy/.env`."

# First match wins, so a subclass has to precede its base.
_ERROR_REPLIES: tuple[tuple[type[Exception], str], ...] = (
    (
        llmbroker.ToolLoopLimitError,
        "**The model kept calling tools without answering.** Rephrase and retry.",
    ),
    (
        llmbroker.RateLimitError,
        "**The AI model is rate-limited right now.** Press 🔁 Retry in a moment.",
    ),
    (llmbroker.LLMTimeoutError, "**The AI model did not answer in time.** Press 🔁 Retry."),
    (llmbroker.MissingKeyError, _NO_KEY),
    (llmbroker.AuthError, "**The AI model rejected the key.** Check `{ref}` in `.deploy/.env`."),
    (llmbroker.LLMRequestError, "**AI model unavailable:** {detail}"),
)

_JSON_TYPES: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def key_ref() -> str:
    """The env-var name the chat model's key is read from. Read from the copy of the
    catalog already on this machine, never the network."""
    row = next(row for row in llmbroker.curated_paid() if row.alias == CHAT_ALIAS)
    return row.provider.api_key_ref


def _filled(value: str | None) -> bool:
    """``llmbroker env`` writes bare ``KEY=`` lines, and llmbroker itself counts a
    blank one as absent — so must this, or the chat calls with no key at all."""
    return bool(value and value.strip())


def chat_key_available() -> bool:
    """Return True if the chat model's key is resolvable here.

    Both sources a zero-config ``Broker`` reads, in its order: the environment,
    then a ``.env`` beside the working directory. This gate does not merely warn —
    ``run_chat_turn`` refuses to call on a False — so missing the second source
    would disable a chat that would have worked.
    """
    ref = key_ref()
    if _filled(os.getenv(ref)):
        return True
    values: dict[str, str] = {}
    with contextlib.suppress(OSError):
        values = parse_env_file(Path(".env").read_text(encoding="utf-8"))
    return _filled(values.get(ref))


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
    """Send history + user_text to the chat model and return the reply.

    history items are {"role": "user"|"model", "content": str}. Provider/network
    errors (including rate limits) are returned as user-facing text, not raised.
    """
    ref = key_ref()
    if not chat_key_available():
        return _NO_KEY.format(ref=ref)
    schemas = [_tool_schema(fn) for fn in tools]
    dispatch = {tool_name(fn): fn for fn in tools}

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    messages.extend(
        {"role": "assistant" if m["role"] == "model" else "user", "content": m["content"]}
        for m in history
    )
    messages.append({"role": "user", "content": user_text})

    try:
        with (
            llmbroker.Broker(direct=[CHAT_ALIAS]) as llms,
            llms.direct(CHAT_ALIAS) as model,
        ):
            result = llmbroker.run_tool_loop(model, messages, tools=schemas, dispatch=dispatch)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
        return _error_reply(exc, ref)

    return result.text or "*(view updated — see the draft below)*"


def _error_reply(exc: Exception, ref: str) -> str:
    template = next(
        (text for kind, text in _ERROR_REPLIES if isinstance(exc, kind)),
        "**AI error:** {detail}",
    )
    return template.format(ref=ref, detail=str(exc)[:300])

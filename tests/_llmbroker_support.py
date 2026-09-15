"""Driving the real llmbroker in tests: its registry seeded through its own public
store, and its outside world — provider chat completions and curated-list downloads —
answered locally. Nothing else of llmbroker is replaced."""

import asyncio
import contextlib
import io
import json
import unittest.mock
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

import httpx
import llmbroker
import pytest
from fastapi.testclient import TestClient
from llmbroker.sqlite import Registry, Secrets

from dinary.adapters.rates import helpers
from dinary.db import category_seed, db_migrations, storage
from dinary.main import create_app

_BUNDLED_PRESETS = Path(llmbroker.__file__).parent / "presets"


def completion(content: str | None, tool_calls: list[dict] | None = None) -> httpx.Response:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return httpx.Response(
        200,
        json={
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )


class FakeProviders:
    """Answers every chat completion with ``reply`` and records the request bodies."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []
        self.hosts: list[str] = []
        self.reply: Callable[[dict], httpx.Response] = lambda _body: completion("")

    def handle(self, request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/chat/completions"):
            return httpx.Response(404)
        body = json.loads(request.content)
        self.bodies.append(body)
        self.hosts.append(request.url.host)
        return self.reply(body)


def route_provider_http(
    monkeypatch: pytest.MonkeyPatch,
    providers: FakeProviders,
    *,
    blocking_clients: bool = False,
) -> None:
    """Every httpx client llmbroker opens talks to ``providers``. The blocking client is
    left alone unless asked for: FastAPI's TestClient is one."""
    transport = httpx.MockTransport(providers.handle)
    classes = [httpx.AsyncClient, httpx.Client] if blocking_clients else [httpx.AsyncClient]
    for cls in classes:
        original = cls.__init__

        def init(self, *args, _original=original, **kwargs) -> None:
            kwargs["transport"] = transport
            _original(self, *args, **kwargs)

        monkeypatch.setattr(cls, "__init__", init)


class _Download(io.BytesIO):
    def __enter__(self) -> "_Download":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def serve_bundled_presets(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Answer llmbroker's curated-list downloads with the copies its wheel ships, and
    return the list of file names fetched."""
    fetched: list[str] = []

    def urlopen(request, timeout=None) -> _Download:  # noqa: ARG001
        url = request if isinstance(request, str) else request.full_url
        name = url.rsplit("/", 1)[-1]
        fetched.append(name)
        return _Download((_BUNDLED_PRESETS / name).read_bytes())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return fetched


def seed_registry(
    configs: Iterable[llmbroker.LLMConfig],
    keys: Iterable[tuple[str, str]] = (),
) -> None:
    """Write the pool the app will serve. Outside a sync there is no key bootstrap,
    so a provider that must resolve one gets it stored here."""

    async def _run() -> None:
        registry = Registry(storage.DB_PATH)
        secrets = Secrets(storage.DB_PATH)
        try:
            await registry.mirror(list(configs))
            for ref, value in keys:
                await secrets.set(ref, value)
        finally:
            await registry.aclose()
            await secrets.aclose()

    asyncio.run(_run())


@contextlib.contextmanager
def build_app_client() -> Iterator[TestClient]:
    """Mirrors the shared ``client`` fixture but keeps the network stubs active for
    the whole lifespan (startup, requests, and shutdown)."""
    with (
        unittest.mock.patch.object(helpers, "_get_json_or_none", return_value=None),
        unittest.mock.patch.object(db_migrations, "migrate_db"),
        unittest.mock.patch.object(category_seed, "bootstrap_categories"),
    ):
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

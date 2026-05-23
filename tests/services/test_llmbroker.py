"""Tests for LLMBroker: failover, rate-limiting, queue behaviour, storage callbacks."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import httpx

from dinary.adapters.llmbroker import CallEvent, LLMBroker, NullStorage, ProviderConfig


def _make_provider(pid: int = 1, *, label: str = "P1", rate_limit_sec: int = 60) -> ProviderConfig:
    return ProviderConfig(
        id=pid,
        label=label,
        base_url="https://api.example.com/v1",
        api_key="key",
        model="model",
        priority=pid - 1,
        rate_limit_sec=rate_limit_sec,
        rate_limited_until=None,
    )


class _SeededStorage(NullStorage):
    def __init__(self, providers: list[ProviderConfig]) -> None:
        self._providers = providers

    async def load_providers(self) -> list[ProviderConfig]:
        return list(self._providers)


class _TrackingStorage(_SeededStorage):
    def __init__(self, providers: list[ProviderConfig]) -> None:
        super().__init__(providers)
        self.logged: list[CallEvent] = []
        self.rate_limited: list[tuple] = []

    async def on_call_logged(self, event: CallEvent) -> None:
        self.logged.append(event)

    async def on_rate_limited(self, provider_id: object, until: datetime) -> None:
        self.rate_limited.append((provider_id, until))


def _ok_response(content: str = "ok") -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


def _http_ctx(response: MagicMock) -> MagicMock:
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _429_response(retry_after: int | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 429
    resp.headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    return resp


@allure.epic("Services")
@allure.feature("LLMBroker")
class TestLLMBrokerChat:
    def test_success_returns_content(self):
        async def run():
            broker = LLMBroker(_SeededStorage([_make_provider(1)]))
            await broker.start()
            with patch(
                "dinary.adapters.llmbroker.httpx.AsyncClient",
                return_value=_http_ctx(_ok_response("result")),
            ):
                result = await broker.chat([{"role": "user", "content": "hi"}])
            await broker.stop()
            return result

        assert asyncio.run(run()) == "result"

    def test_429_no_wait_returns_none_and_provider_leaves_queue(self):
        storage = _SeededStorage([_make_provider(1)])

        async def run():
            broker = LLMBroker(storage)
            await broker.start()
            client = AsyncMock()
            client.post = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "429", request=MagicMock(), response=_429_response()
                )
            )
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=client)
            ctx.__aexit__ = AsyncMock(return_value=False)
            with patch("dinary.adapters.llmbroker.httpx.AsyncClient", return_value=ctx):
                result = await broker.chat([{"role": "user", "content": "hi"}], wait=False)
            queue_empty = broker._queue.empty()
            await broker.stop()
            return result, queue_empty

        result, queue_empty = asyncio.run(run())
        assert result is None
        assert queue_empty  # provider is cooling, not in queue

    def test_429_wait_blocks_then_retries(self):
        call_count = {"n": 0}

        def _side_effect(*_a, **_kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise httpx.HTTPStatusError("429", request=MagicMock(), response=_429_response())
            return _ok_response("retried")

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=_side_effect)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        async def run():
            broker = LLMBroker(_SeededStorage([_make_provider(1)]))
            await broker.start()
            with patch("dinary.adapters.llmbroker.httpx.AsyncClient", return_value=ctx):
                t = asyncio.create_task(broker.chat([{"role": "user", "content": "hi"}]))
                await asyncio.sleep(0)
                assert not t.done(), "should be waiting for cooldown"
                # manually return provider to queue to simulate cooldown expiry
                broker._queue.put_nowait(broker._providers[0])
                result = await asyncio.wait_for(t, timeout=2.0)
            await broker.stop()
            return result

        assert asyncio.run(run()) == "retried"

    def test_failover_second_provider_answers_after_429(self):
        p1 = _make_provider(1, label="P1")
        p2 = _make_provider(2, label="P2")
        call_count = {"n": 0}

        def _side_effect(*_a, **_kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise httpx.HTTPStatusError("429", request=MagicMock(), response=_429_response())
            return _ok_response("fallback")

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=_side_effect)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        async def run():
            broker = LLMBroker(_SeededStorage([p1, p2]))
            await broker.start()
            with patch("dinary.adapters.llmbroker.httpx.AsyncClient", return_value=ctx):
                # wait=True: P1 → 429 → loops internally → P2 → "fallback"
                result = await broker.chat([{"role": "user", "content": "hi"}])
            await broker.stop()
            return result

        assert asyncio.run(run()) == "fallback"

    def test_429_uses_retry_after_header(self):
        storage = _TrackingStorage([_make_provider(1, rate_limit_sec=60)])

        async def run():
            broker = LLMBroker(storage)
            await broker.start()
            client = AsyncMock()
            client.post = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "429", request=MagicMock(), response=_429_response(retry_after=120)
                )
            )
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=client)
            ctx.__aexit__ = AsyncMock(return_value=False)
            with patch("dinary.adapters.llmbroker.httpx.AsyncClient", return_value=ctx):
                # wait=False: returns None immediately, logs the 429 event
                await broker.chat([{"role": "user", "content": "hi"}], wait=False)
            await broker.stop()
            return storage.logged

        logged = asyncio.run(run())
        rate_limited_events = [e for e in logged if e.status == "429"]
        assert rate_limited_events, "429 event must be logged"
        until = rate_limited_events[0].rate_limited_until
        assert until is not None
        delta = (until - datetime.now(UTC)).total_seconds()
        assert 110 <= delta <= 130, f"Retry-After=120 should set until ~120s ahead, got {delta}"

    def test_429_without_retry_after_uses_rate_limit_sec(self):
        storage = _TrackingStorage([_make_provider(1, rate_limit_sec=45)])

        async def run():
            broker = LLMBroker(storage)
            await broker.start()
            client = AsyncMock()
            client.post = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "429", request=MagicMock(), response=_429_response(retry_after=None)
                )
            )
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=client)
            ctx.__aexit__ = AsyncMock(return_value=False)
            with patch("dinary.adapters.llmbroker.httpx.AsyncClient", return_value=ctx):
                # wait=False: returns None immediately, logs the 429 event
                await broker.chat([{"role": "user", "content": "hi"}], wait=False)
            await broker.stop()
            return storage.logged

        logged = asyncio.run(run())
        rate_limited_events = [e for e in logged if e.status == "429"]
        assert rate_limited_events
        until = rate_limited_events[0].rate_limited_until
        delta = (until - datetime.now(UTC)).total_seconds()
        assert 35 <= delta <= 55, f"No Retry-After → should use rate_limit_sec=45, got {delta}"

    def test_all_providers_cooling_waits_then_proceeds(self):
        async def run():
            broker = LLMBroker(_SeededStorage([_make_provider(1)]))
            await broker.start()
            provider = broker._queue.get_nowait()  # simulate provider cooling / in-flight

            with patch(
                "dinary.adapters.llmbroker.httpx.AsyncClient",
                return_value=_http_ctx(_ok_response("waited")),
            ):
                t = asyncio.create_task(broker.chat([{"role": "user", "content": "hi"}]))
                await asyncio.sleep(0)
                assert not t.done(), "should be waiting while provider is unavailable"
                broker._queue.put_nowait(provider)  # release
                content = await asyncio.wait_for(t, timeout=2.0)
            await broker.stop()
            return content

        assert asyncio.run(run()) == "waited"

    def test_on_call_logged_called_for_every_attempt(self):
        storage = _TrackingStorage([_make_provider(1), _make_provider(2, label="P2")])
        call_count = {"n": 0}

        def _side_effect(*_a, **_kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise httpx.HTTPStatusError("429", request=MagicMock(), response=_429_response())
            return _ok_response("ok")

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=_side_effect)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        async def run():
            broker = LLMBroker(storage)
            await broker.start()
            with patch("dinary.adapters.llmbroker.httpx.AsyncClient", return_value=ctx):
                # wait=True: P1 → 429 → loops → P2 → "ok", both logged in one call
                await broker.chat([{"role": "user", "content": "hi"}])
            await broker.stop()
            return storage.logged, storage.rate_limited

        logged, rate_limited = asyncio.run(run())
        assert len(logged) == 2, "one log entry per attempt"
        assert any(e.status == "429" for e in logged)
        assert any(e.status == "ok" for e in logged)
        assert len(rate_limited) == 1, "on_rate_limited only called on 429"

    def test_background_tasks_start_and_stop_cleanly(self):
        async def run():
            broker = LLMBroker(NullStorage())
            await broker.start()
            assert broker._bg_refresh is not None
            assert not broker._bg_refresh.done()
            await broker.stop()
            assert broker._bg_refresh.cancelled() or broker._bg_refresh.done()

        asyncio.run(run())


@allure.epic("Services")
@allure.feature("LLMBroker")
class TestLLMBrokerChatNoWait:
    def test_returns_none_when_no_providers(self):
        async def run():
            broker = LLMBroker(NullStorage())
            await broker.start()
            result = await broker.chat([{"role": "user", "content": "hi"}], wait=False)
            await broker.stop()
            return result

        assert asyncio.run(run()) is None

    def test_returns_none_when_all_providers_unavailable(self):
        async def run():
            broker = LLMBroker(_SeededStorage([_make_provider(1)]))
            await broker.start()
            broker._queue.get_nowait()  # drain — simulate provider in-flight or cooling
            result = await broker.chat([{"role": "user", "content": "hi"}], wait=False)
            await broker.stop()
            return result

        assert asyncio.run(run()) is None

    def test_returns_content_when_provider_available(self):
        async def run():
            broker = LLMBroker(_SeededStorage([_make_provider(1)]))
            await broker.start()
            with patch(
                "dinary.adapters.llmbroker.httpx.AsyncClient",
                return_value=_http_ctx(_ok_response("chain")),
            ):
                result = await broker.chat([{"role": "user", "content": "hi"}], wait=False)
            await broker.stop()
            return result

        assert asyncio.run(run()) == "chain"

    def test_returns_none_on_network_error(self):
        async def run():
            broker = LLMBroker(_SeededStorage([_make_provider(1)]))
            await broker.start()
            client = AsyncMock()
            client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=client)
            ctx.__aexit__ = AsyncMock(return_value=False)
            with patch("dinary.adapters.llmbroker.httpx.AsyncClient", return_value=ctx):
                result = await broker.chat([{"role": "user", "content": "hi"}], wait=False)
            await broker.stop()
            return result

        assert asyncio.run(run()) is None

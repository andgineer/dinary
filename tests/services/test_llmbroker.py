"""Tests for LLMBroker: failover, rate-limiting, queue behaviour, storage callbacks."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import httpx

from conftest import NullStorage
from dinary.adapters.llmbroker import CallEvent, Execution, LLMBroker, ProviderConfig


def _make_provider(*, label: str = "P1", rate_limit_sec: int = 60) -> ProviderConfig:
    return ProviderConfig(
        label=label,
        base_url="https://api.example.com/v1",
        api_key="key",
        model="model",
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
        self.quality_feedback: list[tuple] = []

    async def on_call_logged(self, event: CallEvent) -> None:
        self.logged.append(event)

    async def on_rate_limited(self, provider_label: str, until: datetime) -> None:
        self.rate_limited.append((provider_label, until))

    async def on_quality_feedback(self, provider_label: str, *, usable: bool) -> None:
        self.quality_feedback.append((provider_label, usable))


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


@allure.epic("Receipts")
@allure.feature("Pipeline")
@allure.story("LLM broker")
class TestLLMBrokerExecute:
    def test_success_returns_content(self):
        async def run():
            broker = LLMBroker(_SeededStorage([_make_provider(label="P1")]))
            await broker.start()
            with patch(
                "dinary.adapters.llmbroker.httpx.AsyncClient",
                return_value=_http_ctx(_ok_response("result")),
            ):
                execution = await broker.execute([{"role": "user", "content": "hi"}])
            await broker.stop()
            return execution.output

        assert asyncio.run(run()) == "result"

    def test_success_execution_has_provider_label(self):
        async def run():
            broker = LLMBroker(_SeededStorage([_make_provider(label="Groq")]))
            await broker.start()
            with patch(
                "dinary.adapters.llmbroker.httpx.AsyncClient",
                return_value=_http_ctx(_ok_response("ok")),
            ):
                execution = await broker.execute([{"role": "user", "content": "hi"}])
            await broker.stop()
            return execution.provider_label

        assert asyncio.run(run()) == "Groq"

    def test_429_no_wait_returns_none_and_provider_leaves_queue(self):
        storage = _SeededStorage([_make_provider(label="P1")])

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
                execution = await broker.execute([{"role": "user", "content": "hi"}], wait=False)
            queue_empty = broker._queue.empty()
            await broker.stop()
            return execution.output, queue_empty

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
            broker = LLMBroker(_SeededStorage([_make_provider(label="P1")]))
            await broker.start()
            with patch("dinary.adapters.llmbroker.httpx.AsyncClient", return_value=ctx):
                t = asyncio.create_task(broker.execute([{"role": "user", "content": "hi"}]))
                await asyncio.sleep(0)
                assert not t.done(), "should be waiting for cooldown"
                # manually return provider to queue to simulate cooldown expiry
                broker._queue.put_nowait(broker._providers[0])
                execution = await asyncio.wait_for(t, timeout=2.0)
            await broker.stop()
            return execution.output

        assert asyncio.run(run()) == "retried"

    def test_failover_second_provider_answers_after_429(self):
        p1 = _make_provider(label="P1")
        p2 = _make_provider(label="P2")
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
                execution = await broker.execute([{"role": "user", "content": "hi"}])
            await broker.stop()
            return execution.output

        assert asyncio.run(run()) == "fallback"

    def test_429_uses_retry_after_header(self):
        storage = _TrackingStorage([_make_provider(label="P1", rate_limit_sec=60)])

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
                await broker.execute([{"role": "user", "content": "hi"}], wait=False)
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
        storage = _TrackingStorage([_make_provider(label="P1", rate_limit_sec=45)])

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
                await broker.execute([{"role": "user", "content": "hi"}], wait=False)
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
            broker = LLMBroker(_SeededStorage([_make_provider(label="P1")]))
            await broker.start()
            provider = broker._queue.get_nowait()  # simulate provider cooling / in-flight

            with patch(
                "dinary.adapters.llmbroker.httpx.AsyncClient",
                return_value=_http_ctx(_ok_response("waited")),
            ):
                t = asyncio.create_task(broker.execute([{"role": "user", "content": "hi"}]))
                await asyncio.sleep(0)
                assert not t.done(), "should be waiting while provider is unavailable"
                broker._queue.put_nowait(provider)  # release
                execution = await asyncio.wait_for(t, timeout=2.0)
            await broker.stop()
            return execution.output

        assert asyncio.run(run()) == "waited"

    def test_on_call_logged_called_for_every_attempt(self):
        storage = _TrackingStorage([_make_provider(label="P1"), _make_provider(label="P2")])
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
                await broker.execute([{"role": "user", "content": "hi"}])
            await broker.stop()
            return storage.logged, storage.rate_limited

        logged, rate_limited = asyncio.run(run())
        assert len(logged) == 2, "one log entry per attempt"
        assert any(e.status == "429" for e in logged)
        assert any(e.status == "ok" for e in logged)
        assert len(rate_limited) == 1, "on_rate_limited only called on 429"

    def test_on_call_logged_uses_provider_label_not_id(self):
        storage = _TrackingStorage([_make_provider(label="Groq")])

        async def run():
            broker = LLMBroker(storage)
            await broker.start()
            with patch(
                "dinary.adapters.llmbroker.httpx.AsyncClient",
                return_value=_http_ctx(_ok_response("ok")),
            ):
                await broker.execute([{"role": "user", "content": "hi"}])
            await broker.stop()
            return storage.logged

        logged = asyncio.run(run())
        assert logged[0].provider_label == "Groq"

    def test_error_detail_set_on_non_429_http_error(self):
        storage = _TrackingStorage([_make_provider(label="P1")])
        body = '{"error": {"message": "Incorrect API key provided"}}'

        async def run():
            broker = LLMBroker(storage)
            await broker.start()
            resp = MagicMock()
            resp.status_code = 401
            resp.text = body
            resp.raise_for_status = MagicMock(
                side_effect=httpx.HTTPStatusError("401", request=MagicMock(), response=resp)
            )
            resp.json.return_value = {}
            client = AsyncMock()
            client.post = AsyncMock(return_value=resp)
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=client)
            ctx.__aexit__ = AsyncMock(return_value=False)
            with patch("dinary.adapters.llmbroker.httpx.AsyncClient", return_value=ctx):
                try:
                    await broker.execute([{"role": "user", "content": "hi"}])
                except httpx.HTTPStatusError:
                    pass
            await broker.stop()
            return storage.logged

        logged = asyncio.run(run())
        assert len(logged) == 1
        assert logged[0].status == "error"
        assert logged[0].error_detail == body

    def test_error_detail_truncated_to_300_chars(self):
        storage = _TrackingStorage([_make_provider(label="P1")])
        long_body = "x" * 500

        async def run():
            broker = LLMBroker(storage)
            await broker.start()
            resp = MagicMock()
            resp.status_code = 500
            resp.text = long_body
            resp.raise_for_status = MagicMock(
                side_effect=httpx.HTTPStatusError("500", request=MagicMock(), response=resp)
            )
            resp.json.return_value = {}
            client = AsyncMock()
            client.post = AsyncMock(return_value=resp)
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=client)
            ctx.__aexit__ = AsyncMock(return_value=False)
            with patch("dinary.adapters.llmbroker.httpx.AsyncClient", return_value=ctx):
                try:
                    await broker.execute([{"role": "user", "content": "hi"}])
                except httpx.HTTPStatusError:
                    pass
            await broker.stop()
            return storage.logged

        logged = asyncio.run(run())
        assert logged[0].error_detail == "x" * 300

    def test_error_detail_none_on_success(self):
        storage = _TrackingStorage([_make_provider(label="P1")])

        async def run():
            broker = LLMBroker(storage)
            await broker.start()
            with patch(
                "dinary.adapters.llmbroker.httpx.AsyncClient",
                return_value=_http_ctx(_ok_response("ok")),
            ):
                await broker.execute([{"role": "user", "content": "hi"}])
            await broker.stop()
            return storage.logged

        logged = asyncio.run(run())
        assert logged[0].error_detail is None

    def test_background_tasks_start_and_stop_cleanly(self):
        async def run():
            broker = LLMBroker(NullStorage())
            await broker.start()
            assert broker._bg_refresh is not None
            assert not broker._bg_refresh.done()
            await broker.stop()
            assert broker._bg_refresh.cancelled() or broker._bg_refresh.done()

        asyncio.run(run())

    def test_provider_count_returns_number_of_loaded_providers(self):
        async def run():
            broker = LLMBroker(
                _SeededStorage([_make_provider(label="P1"), _make_provider(label="P2")])
            )
            await broker.start()
            count = broker.provider_count
            await broker.stop()
            return count

        assert asyncio.run(run()) == 2

    def test_provider_count_zero_when_no_providers(self):
        async def run():
            broker = LLMBroker(NullStorage())
            await broker.start()
            count = broker.provider_count
            await broker.stop()
            return count

        assert asyncio.run(run()) == 0


@allure.epic("Receipts")
@allure.feature("Pipeline")
@allure.story("LLM broker")
class TestLLMBrokerExecuteNoWait:
    def test_returns_none_when_no_providers(self):
        async def run():
            broker = LLMBroker(NullStorage())
            await broker.start()
            execution = await broker.execute([{"role": "user", "content": "hi"}], wait=False)
            await broker.stop()
            return execution.output

        assert asyncio.run(run()) is None

    def test_provider_label_none_when_queue_empty(self):
        async def run():
            broker = LLMBroker(NullStorage())
            await broker.start()
            execution = await broker.execute([{"role": "user", "content": "hi"}], wait=False)
            await broker.stop()
            return execution.provider_label

        assert asyncio.run(run()) is None

    def test_returns_none_when_all_providers_unavailable(self):
        async def run():
            broker = LLMBroker(_SeededStorage([_make_provider(label="P1")]))
            await broker.start()
            broker._queue.get_nowait()  # drain — simulate provider in-flight or cooling
            execution = await broker.execute([{"role": "user", "content": "hi"}], wait=False)
            await broker.stop()
            return execution.output

        assert asyncio.run(run()) is None

    def test_returns_content_when_provider_available(self):
        async def run():
            broker = LLMBroker(_SeededStorage([_make_provider(label="P1")]))
            await broker.start()
            with patch(
                "dinary.adapters.llmbroker.httpx.AsyncClient",
                return_value=_http_ctx(_ok_response("chain")),
            ):
                execution = await broker.execute([{"role": "user", "content": "hi"}], wait=False)
            await broker.stop()
            return execution.output

        assert asyncio.run(run()) == "chain"

    def test_returns_none_on_network_error(self):
        async def run():
            broker = LLMBroker(_SeededStorage([_make_provider(label="P1")]))
            await broker.start()
            client = AsyncMock()
            client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=client)
            ctx.__aexit__ = AsyncMock(return_value=False)
            with patch("dinary.adapters.llmbroker.httpx.AsyncClient", return_value=ctx):
                execution = await broker.execute([{"role": "user", "content": "hi"}], wait=False)
            await broker.stop()
            return execution.output

        assert asyncio.run(run()) is None


@allure.epic("Receipts")
@allure.feature("Pipeline")
@allure.story("LLM broker")
class TestExecutionMarkFailed:
    def test_mark_failed_calls_on_quality_feedback(self):
        storage = _TrackingStorage([_make_provider(label="P1")])

        async def run():
            broker = LLMBroker(storage)
            await broker.start()
            with patch(
                "dinary.adapters.llmbroker.httpx.AsyncClient",
                return_value=_http_ctx(_ok_response("ok")),
            ):
                execution = await broker.execute([{"role": "user", "content": "hi"}])
            await broker.stop()
            await execution.mark_failed()
            return storage.quality_feedback

        feedback = asyncio.run(run())
        assert len(feedback) == 1
        assert feedback[0] == ("P1", False)

    def test_mark_failed_noop_when_provider_label_is_none(self):
        storage = _TrackingStorage([])

        async def run():
            execution = Execution(output=None, provider_label=None, storage=storage)
            await execution.mark_failed()
            return storage.quality_feedback

        feedback = asyncio.run(run())
        assert feedback == [], (
            "mark_failed on QueueEmpty Execution must not call on_quality_feedback"
        )

"""Self-contained LLM provider broker with round-robin failover and rate-limit handling.

No imports from dinary.db, dinary.adapters.llm_client, or any SQLite module —
intentionally isolated for future extraction as a standalone package.
"""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    id: Any
    label: str
    base_url: str
    api_key: str
    model: str
    priority: int
    rate_limit_sec: int
    rate_limited_until: datetime | None


@dataclass(frozen=True, slots=True)
class CallEvent:
    provider_id: Any
    context_id: Any | None
    status: str
    latency_ms: int
    timestamp: datetime
    rate_limited_until: datetime | None = field(default=None)


class BrokerStorage(Protocol):
    async def load_providers(self) -> list[ProviderConfig]: ...
    async def on_call_logged(self, event: CallEvent) -> None: ...
    async def on_rate_limited(self, provider_id: Any, until: datetime) -> None: ...


class NullStorage:
    """No-op storage for no-persistence mode."""

    async def load_providers(self) -> list[ProviderConfig]:
        return []

    async def on_call_logged(self, event: CallEvent) -> None:
        pass

    async def on_rate_limited(self, provider_id: Any, until: datetime) -> None:
        pass


class LLMBroker:
    """Round-robin LLM provider broker with per-provider availability events.

    ``complete()`` waits indefinitely when all providers are cooling; it never
    raises for rate limits. ``try_complete()`` returns None immediately when
    no provider is available.
    """

    def __init__(
        self,
        storage: BrokerStorage,
        *,
        refresh_interval: float = 60.0,
    ) -> None:
        self._storage = storage
        self._refresh_interval = refresh_interval
        self._providers: list[ProviderConfig] = []
        self._provider_events: dict[Any, asyncio.Event] = {}
        self._log_queue: asyncio.Queue[CallEvent] = asyncio.Queue()
        self._next_idx: int = 0
        self._bg_refresh: asyncio.Task | None = None
        self._bg_log_drain: asyncio.Task | None = None

    async def start(self) -> None:
        providers = await self._storage.load_providers()
        self._providers = providers
        for p in providers:
            self._init_event(p)
        self._bg_refresh = asyncio.create_task(self._run_refresh(), name="llmbroker-refresh")
        self._bg_log_drain = asyncio.create_task(
            self._run_log_drain(),
            name="llmbroker-log-drain",
        )

    async def stop(self) -> None:
        for task in (self._bg_refresh, self._bg_log_drain):
            if task:
                task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            if self._bg_refresh:
                await self._bg_refresh
        with contextlib.suppress(asyncio.CancelledError):
            if self._bg_log_drain:
                await self._bg_log_drain
        while not self._log_queue.empty():
            try:
                event = self._log_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                await self._storage.on_call_logged(event)
                if event.rate_limited_until is not None:
                    await self._storage.on_rate_limited(event.provider_id, event.rate_limited_until)
            except Exception:  # noqa: BLE001
                logger.exception("LLMBroker: log drain failed during shutdown flush")
            finally:
                self._log_queue.task_done()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[dict],
        context_id: Any | None = None,
    ) -> tuple[str, bool]:
        """Return (content, used_fallback).

        Waits indefinitely when all providers are cooling; never raises for
        rate limits. used_fallback=True when the primary provider was skipped
        or a provider failed before a successful call.
        """
        used_fallback = False
        while True:
            provider, skipped_primary = await self._wait_and_pick()
            used_fallback = used_fallback or skipped_primary
            content = await self._attempt_call(provider, messages, context_id)
            if content is not None:
                self._advance_idx(provider)
                return content, used_fallback
            used_fallback = True

    async def try_complete(self, messages: list[dict]) -> str | None:
        """Return content from first available provider, or None if none available right now."""
        provider = self._pick_first_available()
        if provider is None:
            return None
        t0 = time.monotonic()
        status, rate_limited_until = "ok", None
        try:
            return await self._call_provider(provider, messages)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in (429, 503):
                delay = self._parse_retry_after(exc.response, provider.rate_limit_sec)
                rate_limited_until = datetime.now(UTC) + timedelta(seconds=delay)
                self._rate_limit_provider(provider.id, delay)
                status = str(code)
            else:
                status = "error"
            logger.warning(
                "LLMBroker.try_complete: %s failed with HTTP %s",
                provider.label,
                code,
            )
            return None
        except (httpx.TimeoutException, httpx.ConnectError, OSError) as exc:
            status = "error"
            logger.warning(
                "LLMBroker.try_complete: %s network error (%s)",
                provider.label,
                type(exc).__name__,
            )
            return None
        finally:
            self._emit(
                provider.id,
                None,
                status,
                int((time.monotonic() - t0) * 1000),
                rate_limited_until=rate_limited_until,
            )

    # ------------------------------------------------------------------
    # Provider selection helpers
    # ------------------------------------------------------------------

    def _available_ids(self, providers: list[ProviderConfig]) -> set:
        return {
            p.id for p in providers if self._provider_events.get(p.id, asyncio.Event()).is_set()
        }

    def _pick_round_robin(
        self,
        providers: list[ProviderConfig],
        available: set,
    ) -> tuple[ProviderConfig | None, bool]:
        """Return (provider, skipped_primary) using round-robin over available providers."""
        n = len(providers)
        for i in range(n):
            candidate = providers[(self._next_idx + i) % n]
            if candidate.id in available:
                return candidate, i > 0
        return None, False

    def _pick_first_available(self) -> ProviderConfig | None:
        """Return the first available provider in round-robin order, or None."""
        providers = self._providers
        n = len(providers)
        for i in range(n):
            candidate = providers[(self._next_idx + i) % n]
            if self._provider_events.get(candidate.id, asyncio.Event()).is_set():
                return candidate
        return None

    async def _wait_for_any_available(self, providers: list[ProviderConfig]) -> None:
        logger.info("LLMBroker: all providers cooling — waiting for availability")
        events = [self._provider_events[p.id] for p in providers if p.id in self._provider_events]
        if not events:
            await asyncio.sleep(5)
            return
        tasks = [asyncio.create_task(ev.wait()) for ev in events]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()

    async def _wait_and_pick(self) -> tuple[ProviderConfig, bool]:
        """Block until a provider is available; return (provider, skipped_primary)."""
        while True:
            providers = self._providers
            if not providers:
                logger.warning("LLMBroker: no providers configured — waiting 60s")
                await asyncio.sleep(60)
                continue
            available = self._available_ids(providers)
            if not available:
                await self._wait_for_any_available(providers)
                continue
            provider, skipped = self._pick_round_robin(providers, available)
            if provider is not None:
                return provider, skipped

    # ------------------------------------------------------------------
    # HTTP call helpers
    # ------------------------------------------------------------------

    async def _attempt_call(
        self,
        provider: ProviderConfig,
        messages: list[dict],
        context_id: Any | None,
    ) -> str | None:
        """Call one provider. Returns content on success, None on retriable failure.

        Raises on non-retriable HTTP errors or unexpected exceptions — those
        propagate to the caller for poisoning.
        """
        t0 = time.monotonic()
        try:
            content = await self._call_provider(provider, messages)
            self._emit(provider.id, context_id, "ok", int((time.monotonic() - t0) * 1000))
            return content
        except httpx.HTTPStatusError as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            return self._handle_http_error(exc, provider, context_id, latency_ms)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            self._emit(provider.id, context_id, "error", latency_ms)
            logger.warning(
                "LLMBroker: provider %s network error (%s)",
                provider.label,
                type(exc).__name__,
            )
            return None

    def _handle_http_error(
        self,
        exc: httpx.HTTPStatusError,
        provider: ProviderConfig,
        context_id: Any | None,
        latency_ms: int,
    ) -> str | None:
        """Log and handle an HTTPStatusError. Returns None for rate limits, re-raises otherwise."""
        code = exc.response.status_code
        if code in (429, 503):
            delay = self._parse_retry_after(exc.response, provider.rate_limit_sec)
            until = datetime.now(UTC) + timedelta(seconds=delay)
            self._emit(provider.id, context_id, str(code), latency_ms, rate_limited_until=until)
            self._rate_limit_provider(provider.id, delay)
            logger.warning(
                "LLMBroker: provider %s returned %s, cooling for %ds",
                provider.label,
                code,
                delay,
            )
            return None
        self._emit(provider.id, context_id, "error", latency_ms)
        raise exc

    def _advance_idx(self, provider: ProviderConfig) -> None:
        providers = self._providers
        if providers:
            self._next_idx = (providers.index(provider) + 1) % len(providers)

    async def _call_provider(self, provider: ProviderConfig, messages: list[dict]) -> str:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{provider.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {provider.api_key}"},
                json={"model": provider.model, "messages": messages},
            )
            resp.raise_for_status()
        return str(resp.json()["choices"][0]["message"]["content"])

    @staticmethod
    def _parse_retry_after(resp: httpx.Response, default_sec: int) -> int:
        try:
            return int(resp.headers.get("Retry-After", default_sec))
        except (ValueError, TypeError):
            return default_sec

    def _emit(
        self,
        provider_id: Any,
        context_id: Any | None,
        status: str,
        latency_ms: int,
        *,
        rate_limited_until: datetime | None = None,
    ) -> None:
        self._log_queue.put_nowait(
            CallEvent(
                provider_id=provider_id,
                context_id=context_id,
                status=status,
                latency_ms=latency_ms,
                timestamp=datetime.now(UTC),
                rate_limited_until=rate_limited_until,
            ),
        )

    # ------------------------------------------------------------------
    # Event management
    # ------------------------------------------------------------------

    def _init_event(self, p: ProviderConfig) -> None:
        if p.id in self._provider_events:
            return
        ev = asyncio.Event()
        now = datetime.now(UTC)
        if p.rate_limited_until and p.rate_limited_until > now:
            remaining = (p.rate_limited_until - now).total_seconds()
            asyncio.get_running_loop().call_later(max(0.0, remaining), ev.set)
        else:
            ev.set()
        self._provider_events[p.id] = ev

    def _rate_limit_provider(self, provider_id: Any, delay_sec: int) -> None:
        ev = self._provider_events.get(provider_id)
        if ev is None:
            return
        ev.clear()
        asyncio.get_running_loop().call_later(float(delay_sec), ev.set)

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    async def _run_refresh(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._refresh_interval)
            except asyncio.CancelledError:
                break
            try:
                new_providers = await self._storage.load_providers()
                self._providers = new_providers
                for p in new_providers:
                    self._init_event(p)
            except Exception:  # noqa: BLE001
                logger.exception("LLMBroker: provider refresh failed")

    async def _run_log_drain(self) -> None:
        while True:
            try:
                event = await self._log_queue.get()
            except asyncio.CancelledError:
                break
            try:
                await self._storage.on_call_logged(event)
                if event.rate_limited_until is not None:
                    await self._storage.on_rate_limited(
                        event.provider_id,
                        event.rate_limited_until,
                    )
            except Exception:  # noqa: BLE001
                logger.exception("LLMBroker: log drain failed")
            finally:
                self._log_queue.task_done()

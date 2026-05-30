"""Self-contained LLM provider broker with round-robin failover and rate-limit handling.

No imports from dinary.db or any SQLite module —
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
    label: str
    base_url: str
    api_key: str
    model: str
    rate_limit_sec: int
    rate_limited_until: datetime | None


@dataclass(frozen=True, slots=True)
class CallEvent:
    provider_label: str
    execution_id: Any | None
    status: str
    latency_ms: int
    timestamp: datetime
    rate_limited_until: datetime | None = field(default=None)
    error_detail: str | None = field(default=None)


class BrokerStorage(Protocol):
    async def load_providers(self) -> list[ProviderConfig]: ...
    async def on_call_logged(self, event: CallEvent) -> None: ...
    async def on_rate_limited(self, provider_label: str, until: datetime) -> None: ...
    async def on_quality_feedback(self, provider_label: str, *, usable: bool) -> None: ...


class Execution:
    """Result of a single broker call with a handle to report quality failure."""

    def __init__(
        self,
        output: str | None,
        provider_label: str | None,
        storage: BrokerStorage,
    ) -> None:
        self.output = output
        self.provider_label = provider_label
        self._storage = storage

    async def mark_failed(self) -> None:
        if self.provider_label is None:
            return
        await self._storage.on_quality_feedback(self.provider_label, usable=False)


class LLMBroker:
    """Round-robin LLM provider broker with per-provider rate-limit handling.

    Each provider occupies one slot in ``_queue``. Acquiring a provider removes
    it from the queue; releasing puts it back — immediately on success/error, or
    after the cooldown delay on 429/503.  At most one in-flight request per
    provider at any time, so the thundering-herd 429 storm cannot happen.

    ``execute(wait=True)`` blocks until a provider is free.
    ``execute(wait=False)`` returns Execution(output=None) immediately if none is available.
    """

    def __init__(
        self,
        storage: BrokerStorage,
        *,
        refresh_interval: float = 60.0,
    ) -> None:
        self._storage = storage
        self._refresh_interval = refresh_interval
        self._queue: asyncio.Queue[ProviderConfig] = asyncio.Queue()
        self._known_ids: set[str] = set()
        self._providers: list[ProviderConfig] = []  # snapshot for introspection / tests
        self._bg_refresh: asyncio.Task | None = None

    async def start(self) -> None:
        await self._enqueue_providers_from_db()
        self._bg_refresh = asyncio.create_task(self._run_refresh(), name="llmbroker-refresh")

    async def stop(self) -> None:
        if self._bg_refresh:
            self._bg_refresh.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            if self._bg_refresh:
                await self._bg_refresh

    async def _enqueue_providers_from_db(self) -> None:
        providers = await self._storage.load_providers()
        self._providers = providers
        now = datetime.now(UTC)
        loop = asyncio.get_running_loop()
        for p in providers:
            if p.label not in self._known_ids:
                self._known_ids.add(p.label)
                if p.rate_limited_until and p.rate_limited_until > now:
                    delay = (p.rate_limited_until - now).total_seconds()
                    loop.call_later(max(0.0, delay), self._queue.put_nowait, p)
                else:
                    self._queue.put_nowait(p)

    @property
    def provider_count(self) -> int:
        return len(self._providers)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        messages: list[dict],
        *,
        wait: bool = True,
        execution_id: Any | None = None,
    ) -> "Execution":
        """Send a chat completion job to an available provider.

        Returns an Execution whose output is the response string, or None on transient error.
        Raises on permanent HTTP errors (non-429/503).
        wait=True  — on 429/503 blocks until cooldown expires and retries; blocks if queue empty.
        wait=False — returns Execution(output=None) immediately if no provider is available
                     or on 429/503.
        """
        while True:
            try:
                provider = await self._queue.get() if wait else self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return Execution(output=None, provider_label=None, storage=self._storage)

            t0 = time.monotonic()
            status, rate_limited_until, error_detail = "ok", None, None
            try:
                content = await self._call_provider(provider, messages)
                self._queue.put_nowait(provider)
                return Execution(
                    output=content,
                    provider_label=provider.label,
                    storage=self._storage,
                )
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                error_detail = exc.response.text[:300]
                if code in (429, 503):
                    delay = self._parse_retry_after(exc.response, provider.rate_limit_sec)
                    rate_limited_until = datetime.now(UTC) + timedelta(seconds=delay)
                    asyncio.get_running_loop().call_later(
                        float(delay),
                        self._queue.put_nowait,
                        provider,
                    )
                    status = str(code)
                    logger.warning(
                        "LLMBroker: %s returned %s, cooling for %ds",
                        provider.label,
                        code,
                        delay,
                    )
                    if not wait:
                        return Execution(
                            output=None,
                            provider_label=provider.label,
                            storage=self._storage,
                        )
                    # wait=True: loop — queue.get() blocks until cooldown or another provider
                else:
                    self._queue.put_nowait(provider)
                    status = "error"
                    raise
            except (httpx.TimeoutException, httpx.ConnectError, OSError) as exc:
                self._queue.put_nowait(provider)
                status = "error"
                logger.warning(
                    "LLMBroker: %s network error (%s)",
                    provider.label,
                    type(exc).__name__,
                )
                return Execution(
                    output=None,
                    provider_label=provider.label,
                    storage=self._storage,
                )
            finally:
                await self._log_call(
                    provider.label,
                    execution_id,
                    status,
                    int((time.monotonic() - t0) * 1000),
                    rate_limited_until=rate_limited_until,
                    error_detail=error_detail,
                )

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Storage callbacks
    # ------------------------------------------------------------------

    async def _log_call(
        self,
        provider_label: str,
        execution_id: Any | None,
        status: str,
        latency_ms: int,
        *,
        rate_limited_until: datetime | None = None,
        error_detail: str | None = None,
    ) -> None:
        event = CallEvent(
            provider_label=provider_label,
            execution_id=execution_id,
            status=status,
            latency_ms=latency_ms,
            timestamp=datetime.now(UTC),
            rate_limited_until=rate_limited_until,
            error_detail=error_detail,
        )
        try:
            await self._storage.on_call_logged(event)
            if rate_limited_until is not None:
                await self._storage.on_rate_limited(provider_label, rate_limited_until)
        except Exception:  # noqa: BLE001
            logger.exception("LLMBroker: storage call failed")

    # ------------------------------------------------------------------
    # Background refresh
    # ------------------------------------------------------------------

    async def _run_refresh(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._refresh_interval)
            except asyncio.CancelledError:
                break
            try:
                await self._enqueue_providers_from_db()
            except Exception:  # noqa: BLE001
                logger.exception("LLMBroker: provider refresh failed")

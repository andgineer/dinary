import asyncio
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_RATE_LIMIT_SEC = 60
_CHAIN_NAME_PROMPT = (
    "What retail chain is this store? "
    "Raw name: {store_name_raw}. "
    "Reply with just the canonical chain name (e.g. Lidl, Maxi, DM, Metro). "
    "No explanation."
)


@dataclass(slots=True)
class ClassificationResult:
    item_name_normalized: str
    category_id: int | None
    confidence_level: int
    alternative_category_ids: list[int] = field(default_factory=list)
    tag_ids: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ReceiptContext:
    """Optional receipt metadata for LLM call audit logging."""

    receipt_id: int | None = None
    invoice_number: str = field(default="")


class LLMClient(Protocol):
    async def classify_receipt(
        self,
        items: list[str],
        store_name_raw: str,
        categories: dict[int, str],
        tags: dict[int, str],
    ) -> list[ClassificationResult]: ...


_SYSTEM_PROMPT = (
    "You are a receipt classifier for a personal expense tracker in Serbia.\n"
    "Classify each item into one of the provided categories.\n"
    "Reply with a JSON array only — no explanation, no markdown fences.\n"
    'Each element: {"item": "<item name>", "category_id": <int or null>, "confidence": <1-4>}\n'
    "Confidence scale: 1=cannot classify, 2=rough guess, 3=likely correct, 4=certain\n"
    'Always add "alternatives": [<cat_id>, ...] with 2-3 next-best category IDs'
    " ordered by likelihood.\n"
    'If tags are provided, add "tags": [<tag_id>, ...] with tag IDs that clearly apply to the'
    " item; omit if none clearly fit; do not guess."
)


def _build_user_message(
    items: list[str],
    store_name_raw: str,
    categories: dict[int, str],
    tags: dict[int, str],
) -> str:
    cat_lines = "\n".join(f"{cat_id}: {name}" for cat_id, name in sorted(categories.items()))
    item_lines = "\n".join(f"- {item}" for item in items)
    msg = f"Store: {store_name_raw}\n\nCategories:\n{cat_lines}"
    if tags:
        tag_lines = "\n".join(f"{tag_id}: {name}" for tag_id, name in sorted(tags.items()))
        msg += f"\n\nTags:\n{tag_lines}"
    msg += f"\n\nItems:\n{item_lines}"
    return msg


def _parse_response(
    raw: str,
    items: list[str],
    tag_id_set: set[int],
) -> list[ClassificationResult]:
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError("expected list")  # noqa: TRY004
        return [
            ClassificationResult(
                item_name_normalized=str(entry.get("item", "")),
                category_id=int(entry["category_id"])
                if entry.get("category_id") is not None
                else None,
                confidence_level=int(entry.get("confidence", 1)),
                alternative_category_ids=[
                    int(a)
                    for a in entry.get("alternatives", [])
                    if isinstance(a, (int, float)) and float(a) == int(a)
                ][:3],
                tag_ids=[
                    int(t)
                    for t in entry.get("tags", [])
                    if isinstance(t, (int, float)) and int(t) in tag_id_set
                ],
            )
            for entry in parsed
        ]
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        logger.warning("LLM parse error (%s), fallback conf=1: %.200s", exc, raw)
        return [
            ClassificationResult(item_name_normalized=item, category_id=None, confidence_level=1)
            for item in items
        ]


class OpenAICompatibleClient:
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

    async def classify_receipt(
        self,
        items: list[str],
        store_name_raw: str,
        categories: dict[int, str],
        tags: dict[int, str] | None = None,
    ) -> list[ClassificationResult]:
        if tags is None:
            tags = {}
        user_msg = _build_user_message(items, store_name_raw, categories, tags)
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return _parse_response(content, items, set(tags.keys()))

    async def get_chain_name(self, store_name_raw: str) -> str:
        prompt = _CHAIN_NAME_PROMPT.format(store_name_raw=store_name_raw)
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return next((ln.strip() for ln in content.splitlines() if ln.strip()), "")


# ---------------------------------------------------------------------------
# Provider pool — round-robin failover across llm_providers rows
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _ProviderRow:
    id: int
    label: str
    base_url: str
    api_key: str
    model: str
    priority: int
    rate_limited_until: datetime | None


def _load_providers(conn: sqlite3.Connection) -> list[_ProviderRow]:
    rows = conn.execute(
        """
        SELECT id, label, base_url, api_key, model, priority, rate_limited_until
          FROM llm_providers
         WHERE is_enabled = 1
         ORDER BY priority, id
        """,
    ).fetchall()
    result = []
    for r in rows:
        rlu = None
        if r[6]:
            try:
                rlu = datetime.fromisoformat(str(r[6]))
                if rlu.tzinfo is None:
                    rlu = rlu.replace(tzinfo=UTC)
            except ValueError:
                pass
        result.append(
            _ProviderRow(
                id=int(r[0]),
                label=str(r[1]),
                base_url=str(r[2]),
                api_key=str(r[3]),
                model=str(r[4]),
                priority=int(r[5]),
                rate_limited_until=rlu,
            ),
        )
    return result


def _get_start_idx(conn: sqlite3.Connection, n_providers: int) -> int:
    row = conn.execute(
        "SELECT value FROM app_metadata WHERE key = 'llm_last_provider_idx'",
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(row[0]) % n_providers
    except (ValueError, ZeroDivisionError):
        return 0


def _set_metadata(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_metadata (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [key, value],
    )


def _increment_metadata_int(conn: sqlite3.Connection, key: str) -> None:
    conn.execute(
        "INSERT INTO app_metadata (key, value) VALUES (?, '1')"
        " ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)",
        [key],
    )


def _mark_rate_limited(conn: sqlite3.Connection, provider_id: int, retry_after_sec: int) -> None:
    until = (datetime.now(UTC) + timedelta(seconds=retry_after_sec)).isoformat()
    conn.execute(
        "UPDATE llm_providers SET rate_limited_until = ? WHERE id = ?",
        [until, provider_id],
    )


def _log_call(
    conn: sqlite3.Connection,
    provider_id: int,
    receipt_id: int | None,
    status: str,
    latency_ms: int,
) -> None:
    conn.execute(
        "INSERT INTO llm_call_log"
        " (provider_id, receipt_id, status, latency_ms) VALUES (?, ?, ?, ?)",
        [provider_id, receipt_id, status, latency_ms],
    )


def _is_rate_limited(provider: _ProviderRow) -> bool:
    if provider.rate_limited_until is None:
        return False
    now = datetime.now(UTC)
    return provider.rate_limited_until > now


def _retry_after(resp: httpx.Response) -> int:
    try:
        return int(resp.headers.get("Retry-After", _DEFAULT_RATE_LIMIT_SEC))
    except (ValueError, TypeError):
        return _DEFAULT_RATE_LIMIT_SEC


async def _call_provider_classify(
    provider: _ProviderRow,
    items: list[str],
    store_name_raw: str,
    categories: dict[int, str],
    tags: dict[int, str],
) -> list[ClassificationResult]:
    client = OpenAICompatibleClient(provider.base_url, provider.api_key, provider.model)
    return await client.classify_receipt(items, store_name_raw, categories, tags)


async def _call_provider_chain_name(provider: _ProviderRow, store_name_raw: str) -> str:
    client = OpenAICompatibleClient(provider.base_url, provider.api_key, provider.model)
    return await client.get_chain_name(store_name_raw)


def _record_provider_switch(
    conn: sqlite3.Connection,
    provider: _ProviderRow,
    providers: list,
    idx: int,
    reason: str,
) -> str:
    """Record a provider switch in app_metadata. Returns next_label."""
    next_idx = (idx + 1) % len(providers)
    next_label = providers[next_idx].label if len(providers) > 1 else "none"
    now_str = datetime.now(UTC).isoformat()
    _set_metadata(
        conn,
        "llm_provider_switch_last",
        f"{now_str} | from: {provider.label} | reason: {reason} | to: {next_label}",
    )
    _increment_metadata_int(conn, "llm_provider_switch_count")
    return next_label


def _on_provider_success(
    conn: sqlite3.Connection,
    providers: list,
    idx: int,
    used_failover: bool,
) -> None:
    """Update metadata after a successful provider call."""
    next_idx = (idx + 1) % len(providers)
    _set_metadata(conn, "llm_last_provider_idx", str(next_idx))
    if not used_failover:
        conn.execute(
            "DELETE FROM app_metadata"
            " WHERE key IN ('llm_all_exhausted_last', 'llm_provider_switch_last')",
        )


class ProviderPool:
    """Round-robin LLM provider pool with per-provider availability events.

    When all providers are rate-limited, ``classify_receipt`` waits (via asyncio.Event)
    until any provider's cooldown expires — it never raises an exception for exhaustion.
    ``get_chain_name`` is opportunistic: returns ``store_name_raw`` if no provider is ready.
    """

    def __init__(self) -> None:
        self._provider_events: dict[int, asyncio.Event] = {}

    def _ensure_event(self, provider: _ProviderRow) -> None:
        """Initialize an availability event for *provider* if not already present."""
        if provider.id in self._provider_events:
            return
        ev = asyncio.Event()
        if _is_rate_limited(provider):
            remaining = (provider.rate_limited_until - datetime.now(UTC)).total_seconds()  # type: ignore[operator]
            asyncio.get_running_loop().call_later(max(0.0, remaining), ev.set)
        else:
            ev.set()
        self._provider_events[provider.id] = ev

    def _rate_limit_provider(self, provider: _ProviderRow, retry_after_sec: int) -> None:
        """Clear the provider's event and schedule re-activation after *retry_after_sec*."""
        ev = self._provider_events.get(provider.id)
        if ev is None:
            return
        ev.clear()
        asyncio.get_running_loop().call_later(float(retry_after_sec), ev.set)

    async def classify_receipt(  # noqa: C901, PLR0912, PLR0915
        self,
        conn: sqlite3.Connection,
        items: list[str],
        store_name_raw: str,
        categories: dict[int, str],
        tags: dict[int, str] | None = None,
        ctx: ReceiptContext | None = None,
    ) -> tuple[list[ClassificationResult], bool]:
        """Return (results, used_failover).

        used_failover=True means the primary provider was skipped/failed and
        a fallback answered — caller should apply a −1 confidence penalty.
        Waits (indefinitely) when all providers are cooling down instead of raising.
        """
        if ctx is None:
            ctx = ReceiptContext()
        if tags is None:
            tags = {}
        used_failover = False

        while True:
            providers = _load_providers(conn)
            if not providers:
                logger.warning("No LLM providers configured — waiting 60 s")
                await asyncio.sleep(60)
                continue

            for p in providers:
                self._ensure_event(p)

            available_ids = {p.id for p in providers if self._provider_events[p.id].is_set()}
            if not available_ids:
                logger.info("All LLM providers cooling down — waiting for availability")
                tasks = [asyncio.create_task(self._provider_events[p.id].wait()) for p in providers]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for t in tasks:
                        t.cancel()
                continue

            start_idx = _get_start_idx(conn, len(providers))
            attempted_any = False

            for i in range(len(providers)):
                idx = (start_idx + i) % len(providers)
                provider = providers[idx]

                if provider.id not in available_ids:
                    if not attempted_any:
                        used_failover = True
                    continue

                attempted_any = True
                t0 = time.monotonic()
                try:
                    results = await _call_provider_classify(
                        provider,
                        items,
                        store_name_raw,
                        categories,
                        tags,
                    )
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    _log_call(conn, provider.id, ctx.receipt_id, "ok", latency_ms)
                    _on_provider_success(conn, providers, idx, used_failover)
                    return results, used_failover

                except httpx.HTTPStatusError as exc:
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    status_code = exc.response.status_code
                    if status_code in (429, 503):
                        retry_after = _retry_after(exc.response)
                        _mark_rate_limited(conn, provider.id, retry_after)
                        _log_call(conn, provider.id, ctx.receipt_id, str(status_code), latency_ms)
                        self._rate_limit_provider(provider, retry_after)
                        next_label = _record_provider_switch(
                            conn,
                            provider,
                            providers,
                            idx,
                            str(status_code),
                        )
                        logger.warning(
                            "Provider %s returned %s, switching to %s",
                            provider.label,
                            status_code,
                            next_label,
                        )
                        used_failover = True
                        available_ids.discard(provider.id)
                    else:
                        _log_call(conn, provider.id, ctx.receipt_id, "error", latency_ms)
                        raise

                except (httpx.TimeoutException, httpx.ConnectError) as exc:
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    _log_call(conn, provider.id, ctx.receipt_id, "error", latency_ms)
                    next_label = _record_provider_switch(
                        conn,
                        provider,
                        providers,
                        idx,
                        type(exc).__name__,
                    )
                    logger.warning(
                        "Provider %s network error (%s), switching to %s",
                        provider.label,
                        type(exc).__name__,
                        next_label,
                    )
                    used_failover = True
                    available_ids.discard(provider.id)

                except Exception:
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    _log_call(conn, provider.id, ctx.receipt_id, "error", latency_ms)
                    raise

            # All currently-available providers exhausted this round — loop to wait.

    async def get_chain_name(
        self,
        conn: sqlite3.Connection,
        store_name_raw: str,
    ) -> str:
        """Return canonical chain name for a raw store name.

        Opportunistic: if no provider is available right now, returns store_name_raw.
        """
        providers = _load_providers(conn)
        if not providers:
            return store_name_raw

        for i, provider in enumerate(providers):
            if _is_rate_limited(provider):
                continue
            t0 = time.monotonic()
            try:
                result = await _call_provider_chain_name(provider, store_name_raw)
                latency_ms = int((time.monotonic() - t0) * 1000)
                _log_call(conn, provider.id, None, "ok", latency_ms)
                return result
            except httpx.HTTPStatusError as exc:
                latency_ms = int((time.monotonic() - t0) * 1000)
                status_code = exc.response.status_code
                if status_code in (429, 503):
                    retry_after = _retry_after(exc.response)
                    _mark_rate_limited(conn, provider.id, retry_after)
                    _log_call(conn, provider.id, None, str(status_code), latency_ms)
                    self._ensure_event(provider)
                    self._rate_limit_provider(provider, retry_after)
                    next_label = providers[i + 1].label if i + 1 < len(providers) else "none"
                    _set_metadata(
                        conn,
                        "llm_provider_switch_last",
                        f"{datetime.now(UTC).isoformat()} | from: {provider.label}"
                        f" | reason: {status_code} | to: {next_label}",
                    )
                    _increment_metadata_int(conn, "llm_provider_switch_count")
                else:
                    _log_call(conn, provider.id, None, "error", latency_ms)
                logger.warning("Chain name LLM call failed with %s", provider.label, exc_info=True)
            except (httpx.HTTPError, OSError):
                latency_ms = int((time.monotonic() - t0) * 1000)
                _log_call(conn, provider.id, None, "error", latency_ms)
                logger.warning("Chain name LLM call failed with %s", provider.label, exc_info=True)

        return store_name_raw


_provider_pool: ProviderPool | None = None


def get_provider_pool() -> ProviderPool:
    """Return the module-level ProviderPool singleton, creating it on first call."""
    global _provider_pool  # noqa: PLW0603
    if _provider_pool is None:
        _provider_pool = ProviderPool()
    return _provider_pool

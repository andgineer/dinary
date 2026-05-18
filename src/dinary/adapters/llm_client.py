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
    'When confidence < 4, add "alternatives": [<cat_id>, ...] with 2-3 next-best category IDs'
    " ordered by likelihood; omit when confidence=4.\n"
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
        return resp.json()["choices"][0]["message"]["content"].strip()


# ---------------------------------------------------------------------------
# Provider pool — round-robin failover across llm_providers rows
# ---------------------------------------------------------------------------


class AllProvidersExhausted(Exception):  # noqa: N818
    pass


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
    provider: "_ProviderRow",
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
    """Round-robin LLM provider pool with failover, rate-limit tracking, and DB audit."""

    async def classify_receipt(  # noqa: C901
        self,
        conn: sqlite3.Connection,
        items: list[str],
        store_name_raw: str,
        categories: dict[int, str],
        tags: dict[int, str] | None = None,
        ctx: ReceiptContext | None = None,
    ) -> tuple[list[ClassificationResult], bool]:
        """Return (results, used_failover).

        used_failover=True means the first-attempted provider failed and
        a different provider answered — caller should apply a −1 confidence penalty.
        Raises AllProvidersExhausted when every provider fails.
        """
        if ctx is None:
            ctx = ReceiptContext()
        if tags is None:
            tags = {}
        providers = _load_providers(conn)
        if not providers:
            raise AllProvidersExhausted("No enabled LLM providers configured")

        start_idx = _get_start_idx(conn, len(providers))
        used_failover = False

        for i in range(len(providers)):
            idx = (start_idx + i) % len(providers)
            provider = providers[idx]

            if _is_rate_limited(provider):
                logger.info("Provider %s rate-limited, skipping", provider.label)
                if i == 0:
                    used_failover = True
                continue

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
                    _mark_rate_limited(conn, provider.id, _retry_after(exc.response))
                    _log_call(conn, provider.id, ctx.receipt_id, str(status_code), latency_ms)
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

            except Exception:
                latency_ms = int((time.monotonic() - t0) * 1000)
                _log_call(conn, provider.id, ctx.receipt_id, "error", latency_ms)
                raise

        exhausted_val = (
            f"{datetime.now(UTC).isoformat()} | invoice: {ctx.invoice_number}"
            if ctx.invoice_number
            else datetime.now(UTC).isoformat()
        )
        _set_metadata(conn, "llm_all_exhausted_last", exhausted_val)
        raise AllProvidersExhausted("All LLM providers exhausted")

    async def get_chain_name(
        self,
        conn: sqlite3.Connection,
        store_name_raw: str,
    ) -> str:
        """Return canonical chain name for a raw store name. Uses first available provider."""
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
                    _mark_rate_limited(conn, provider.id, _retry_after(exc.response))
                    _log_call(conn, provider.id, None, str(status_code), latency_ms)
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

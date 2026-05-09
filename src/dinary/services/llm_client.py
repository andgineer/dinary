import json
import logging
import sqlite3
import time
from dataclasses import dataclass
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


class LLMClient(Protocol):
    async def classify_receipt(
        self,
        items: list[str],
        store_name_raw: str,
        categories: dict[int, str],
    ) -> list[ClassificationResult]: ...


_SYSTEM_PROMPT = """\
You are a receipt classifier for a personal expense tracker in Serbia.
Classify each item into one of the provided categories.
Reply with a JSON array only — no explanation, no markdown fences.
Each element: {"item": "<item name>", "category_id": <int or null>, "confidence": <1-4>}
Confidence scale: 1=cannot classify, 2=rough guess, 3=likely correct, 4=certain"""


def _build_user_message(items: list[str], store_name_raw: str, categories: dict[int, str]) -> str:
    cat_lines = "\n".join(f"{cat_id}: {name}" for cat_id, name in sorted(categories.items()))
    item_lines = "\n".join(f"- {item}" for item in items)
    return f"Store: {store_name_raw}\n\nCategories:\n{cat_lines}\n\nItems:\n{item_lines}"


def _parse_response(raw: str, items: list[str]) -> list[ClassificationResult]:
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
    ) -> list[ClassificationResult]:
        user_msg = _build_user_message(items, store_name_raw, categories)
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
        return _parse_response(content, items)

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
) -> list[ClassificationResult]:
    client = OpenAICompatibleClient(provider.base_url, provider.api_key, provider.model)
    return await client.classify_receipt(items, store_name_raw, categories)


async def _call_provider_chain_name(provider: _ProviderRow, store_name_raw: str) -> str:
    client = OpenAICompatibleClient(provider.base_url, provider.api_key, provider.model)
    return await client.get_chain_name(store_name_raw)


class ProviderPool:
    """Round-robin LLM provider pool with failover, rate-limit tracking, and DB audit."""

    async def classify_receipt(  # noqa: PLR0913, PLR0915
        self,
        conn: sqlite3.Connection,
        items: list[str],
        store_name_raw: str,
        categories: dict[int, str],
        receipt_id: int | None = None,
        invoice_number: str = "",
    ) -> tuple[list[ClassificationResult], bool]:
        """Return (results, used_failover).

        used_failover=True means the first-attempted provider failed and
        a different provider answered — caller should apply a −1 confidence penalty.
        Raises AllProvidersExhausted when every provider fails.
        """
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
                results = await _call_provider_classify(provider, items, store_name_raw, categories)
                latency_ms = int((time.monotonic() - t0) * 1000)
                _log_call(conn, provider.id, receipt_id, "ok", latency_ms)
                next_idx = (idx + 1) % len(providers)
                _set_metadata(conn, "llm_last_provider_idx", str(next_idx))
                # Only clear the switch/exhausted markers when the *primary* provider
                # succeeds (no failover used).  Clearing on a failover success would
                # hide the fact that the primary is still rate-limited.
                if not used_failover:
                    conn.execute(
                        "DELETE FROM app_metadata"
                        " WHERE key IN ('llm_all_exhausted_last', 'llm_provider_switch_last')",
                    )
                return results, used_failover

            except httpx.HTTPStatusError as exc:
                latency_ms = int((time.monotonic() - t0) * 1000)
                status_code = exc.response.status_code
                if status_code in (429, 503):
                    retry_after = _retry_after(exc.response)
                    _mark_rate_limited(conn, provider.id, retry_after)
                    _log_call(conn, provider.id, receipt_id, str(status_code), latency_ms)
                    next_idx = (idx + 1) % len(providers)
                    next_label = providers[next_idx].label if len(providers) > 1 else "none"
                    now_str = datetime.now(UTC).isoformat()
                    switch_msg = (
                        f"{now_str} | from: {provider.label}"
                        f" | reason: {status_code} | to: {next_label}"
                    )
                    _set_metadata(conn, "llm_provider_switch_last", switch_msg)
                    _increment_metadata_int(conn, "llm_provider_switch_count")
                    logger.warning(
                        "Provider %s returned %s, switching to %s",
                        provider.label,
                        status_code,
                        next_label,
                    )
                    used_failover = True
                else:
                    _log_call(conn, provider.id, receipt_id, "error", latency_ms)
                    raise

            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                latency_ms = int((time.monotonic() - t0) * 1000)
                _log_call(conn, provider.id, receipt_id, "error", latency_ms)
                next_idx = (idx + 1) % len(providers)
                next_label = providers[next_idx].label if len(providers) > 1 else "none"
                now_str = datetime.now(UTC).isoformat()
                _set_metadata(
                    conn,
                    "llm_provider_switch_last",
                    f"{now_str} | from: {provider.label}"
                    f" | reason: {type(exc).__name__} | to: {next_label}",
                )
                _increment_metadata_int(conn, "llm_provider_switch_count")
                logger.warning(
                    "Provider %s network error (%s), switching to %s",
                    provider.label,
                    type(exc).__name__,
                    next_label,
                )
                used_failover = True

            except Exception:
                latency_ms = int((time.monotonic() - t0) * 1000)
                _log_call(conn, provider.id, receipt_id, "error", latency_ms)
                raise

        now_str = datetime.now(UTC).isoformat()
        exhausted_val = f"{now_str} | invoice: {invoice_number}" if invoice_number else now_str
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
